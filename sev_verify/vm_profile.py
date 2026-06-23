"""
SEV-SNP guest launch configuration and helpers for the certification test harness.

VMProfile is the per-test source of truth for how a guest should be started.
Host→guest commands use AF_VSOCK (see :mod:`guest_vsock`).
"""

from __future__ import annotations
import base64
import binascii
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, TextIO

DEFAULT_OVMF_CANDIDATES = (
    "/usr/share/ovmf/OVMF.amdsev.fd",
    "/usr/share/edk2/ovmf/OVMF.amdsev.fd",
)

DEFAULT_GUEST_ERROR_LOG = "/tmp/guest-error.log"
DEFAULT_QEMU_BINARY = "qemu-system-x86_64"
DEFAULT_MEMORY_MB = 2048
DEFAULT_VSOCK_CID = 3
DEFAULT_VSOCK_PORT = 5000
HOST_DATA_SIZE = 32


class VMProfileError(Exception):
    """Raised when a VM profile is invalid or launch prerequisites are missing."""


class VMLaunchError(VMProfileError):
    """Raised when QEMU fails to start or exits immediately."""


def _decode_host_data(host_data: str) -> bytes:
    """
    Decode host_data from hex or base64.

    SEV-SNP HOST_DATA must be exactly 32 bytes (typically a SHA-256 digest).
    """
    text = host_data.strip()
    if not text:
        raise VMProfileError("host_data must be non-empty when provided")

    hex_text = text[2:] if text.lower().startswith("0x") else text
    if len(hex_text) == HOST_DATA_SIZE * 2:
        try:
            return bytes.fromhex(hex_text)
        except ValueError as exc:
            raise VMProfileError(
                f"host_data hex value is invalid: {exc}"
            ) from exc

    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise VMProfileError(
            f"host_data must be {HOST_DATA_SIZE} bytes as hex (64 chars) or base64; "
            f"could not decode value: {exc}"
        ) from exc


def _validate_host_data(host_data: str) -> None:
    decoded = _decode_host_data(host_data)
    if len(decoded) != HOST_DATA_SIZE:
        raise VMProfileError(
            f"host_data must be exactly {HOST_DATA_SIZE} bytes; got {len(decoded)} bytes"
        )


@dataclass(frozen=True)
class VMProfile:
    """Launch-time configuration for an SEV-SNP guest."""
    # QEMU variables with non-default values
    image_path: str
    # QEMU variables
    qemu_binary: str = DEFAULT_QEMU_BINARY
    ovmf_path: str | None = None
    memory_mb: int = DEFAULT_MEMORY_MB
    guest_error_log: str = DEFAULT_GUEST_ERROR_LOG
    # QEMU user-mode NAT: guest outbound Internet (e.g. certificate downloads).
    network_enabled: bool = True
    # Host↔guest command channel over AF_VSOCK (see :mod:`guest_vsock`).
    vsock_cid: int = DEFAULT_VSOCK_CID
    vsock_port: int = DEFAULT_VSOCK_PORT
    vsock_use_vhost: bool = True
    vsock_boot_timeout: float = 180.0
    vsock_connect_timeout: float = 10.0
    vsock_command_timeout: float = 300.0
    vsock_max_response_bytes: int = 16 * 1024 * 1024
    # SEV-SNP variable parameters
    host_data: str | None = None
    policy: str | int | None = None
    auth_key_enabled: bool = False
    kernel_hashes: bool = True
    # Fixed SEV-SNP parameters used by the existing launch scripts.
    cbitpos: int = 51
    reduced_phys_bits: int = 1

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> VMProfile:
        """Build a profile from a dict (e.g. parsed test YAML/JSON)."""
        aliases = {
            "auth-key-enabled": "auth_key_enabled",
            "auth_key_enabled": "auth_key_enabled",
            "network_enabled": "network_enabled",
            "network-enabled": "network_enabled",
            "vsock-cid": "vsock_cid",
            "vsock_cid": "vsock_cid",
            "vsock-port": "vsock_port",
            "vsock_port": "vsock_port",
            "vsock-use-vhost": "vsock_use_vhost",
            "vsock_use_vhost": "vsock_use_vhost",
        }
        normalized: dict[str, Any] = {}
        for key, value in data.items():
            field_name = aliases.get(key, key.replace("-", "_"))
            normalized[field_name] = value
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in normalized.items() if k in known})

    def resolved_ovmf_path(self) -> str:
        """"
        Verify provided OVMF path is present.

        If not, look for OVMF on default locations.
        """
        if self.ovmf_path:
            path = Path(self.ovmf_path)
            if not path.is_file():
                raise VMProfileError(f"OVMF firmware not found on provided path: {self.ovmf_path}")
            return str(path)
        for candidate in DEFAULT_OVMF_CANDIDATES:
            if Path(candidate).is_file():
                return candidate
        raise VMProfileError(
            "AMD SEV-compatible OVMF is not present; cannot launch SEV-SNP guest. "
            f"Tried: {', '.join(DEFAULT_OVMF_CANDIDATES)}"
        )

    def validate(self) -> None:
        """
        Validate that provided parameters before launch
        """
        image = Path(self.image_path)
        if not image.is_file():
            raise VMProfileError(f"Guest image not found: {self.image_path}")
        if self.host_data is not None:
            _validate_host_data(str(self.host_data))
        if self.memory_mb <= 0:
            raise VMProfileError("memory_mb must be positive")
        if self.vsock_cid < 3:
            raise VMProfileError(
                f"vsock_cid must be >= 3 (host uses CID 2), got {self.vsock_cid}"
            )
        if not 1 <= self.vsock_port <= 4294967295:
            raise VMProfileError(
                f"vsock_port must be between 1 and 4294967295, got {self.vsock_port}"
            )
        if self.vsock_max_response_bytes <= 0:
            raise VMProfileError("vsock_max_response_bytes must be positive")
        self.resolved_ovmf_path()

    def vm_launch(
        self,
        *,
        wait_ready_seconds: float = 2,
        wait_for_boot: bool = True,
        print_qemu_command: bool = False
    ) -> VMLaunchResult:
        """
        Launch this profile's guest, verify QEMU is running, and optionally wait for boot.

        Launch verification checks the QEMU process and command line.
        Boot verification (``wait_for_boot=True``) pings the guest vsock agent, which
        confirms the kernel, vsock driver, and exec agent are up.

        Raises :class:`VMLaunchError` if QEMU fails to start or exits immediately.
        Returns :class:`VMLaunchResult` with ``ok=False`` when verification fails.
        """
        self.validate()
        command = build_qemu_command(self)

        if print_qemu_command:
            print_qemu(command)

        error_log = Path(self.guest_error_log)
        error_log.parent.mkdir(parents=True, exist_ok=True)

        with open(error_log, "wb") as err_file:
            process = subprocess.Popen(
                command,
                stderr=err_file,
                stdout=subprocess.DEVNULL,
                start_new_session=True,
            )

        time.sleep(wait_ready_seconds)

        if process.poll() is not None:
            stderr_tail = error_log.read_text(encoding="utf-8", errors="replace").strip()
            raise VMLaunchError(
                f"QEMU exited immediately with code {process.returncode}"
                + (f": {stderr_tail}" if stderr_tail else "")
            )

        message = "VM launch verified"
       
        ok = True

        if wait_for_boot:
            # Import here to avoid circular import with guest_vsock.
            from .guest_vsock import check_guest_ready

            booted, boot_error = check_guest_ready(self)
            if not booted:
                ok = False
                message = f"Guest did not boot: {boot_error}"
            elif message == "VM launch verified":
                message = "VM launched and guest booted"

        return VMLaunchResult(
            pid=process.pid,
            command=command,
            profile=self,
            process=process,
            ok=ok,
            message=message,
        )


@dataclass
class VMLaunchResult:
    """Outcome of launching and verifying a guest."""

    pid: int
    command: list[str]
    profile: VMProfile
    ok: bool
    message: str
    checks: dict[str, bool] = field(default_factory=dict)
    process: subprocess.Popen[bytes] | None = None

    @property
    def command_line(self) -> str:
        return " ".join(shlex.quote(part) for part in self.command)


def _format_policy(policy: str | int) -> str:
    if isinstance(policy, int):
        return hex(policy)
    text = str(policy).strip()
    if text.lower().startswith("0x"):
        return text
    if text.isdigit():
        return hex(int(text))
    return text


def _build_sev_snp_guest_object(profile: VMProfile) -> str:
    parts = [
        "sev-snp-guest",
        "id=sev0",
        f"cbitpos={profile.cbitpos}",
        f"reduced-phys-bits={profile.reduced_phys_bits}",
    ]
    if profile.kernel_hashes:
        parts.append("kernel-hashes=on")
    if profile.host_data:
        parts.append(f'host-data="{profile.host_data}"')
    if profile.policy is not None:
        parts.append(f"policy={_format_policy(profile.policy)}")
    if profile.auth_key_enabled:
        parts.append("author-key-enabled=true")
    return ",".join(parts)


def _build_vsock_device(profile: VMProfile) -> str:
    device_type = "vhost-vsock-pci" if profile.vsock_use_vhost else "virtio-vsock-pci"
    return f"{device_type},guest-cid={profile.vsock_cid},id=vsock0"


def build_qemu_command(profile: VMProfile) -> list[str]:
    """Build the QEMU argv list for the given profile (does not execute)."""
    profile.validate()
    ovmf = profile.resolved_ovmf_path()
    sev_object = _build_sev_snp_guest_object(profile)

    cmd = [
        profile.qemu_binary,
        "-enable-kvm",
        "-machine",
        "q35,memory-encryption=sev0,memory-backend=ram1",
        "-cpu",
        "EPYC-v4",
        "-monitor",
        "none",
        "-display",
        "none",
        "-object",
        f"memory-backend-memfd,id=ram1,size={profile.memory_mb}M",
        "-object",
        sev_object,
        "-bios",
        ovmf,
        "-kernel",
        profile.image_path,
        "-device",
        _build_vsock_device(profile),
    ]

    if profile.network_enabled:
        cmd.extend(["-netdev", "user,id=net0", "-device", "virtio-net-pci,netdev=net0"])

    return cmd


def _qemu_pretty_chunks(command: list[str]) -> list[str]:
    """
    Group argv into display chunks: program path, then each flag alone or ``flag value`` pair.
    """
    if not command:
        return []
    chunks: list[str] = [command[0]]
    i = 1
    while i < len(command):
        cur = command[i]
        if (
            cur.startswith("-")
            and i + 1 < len(command)
            and not command[i + 1].startswith("-")
        ):
            chunks.append(f"{cur} {command[i + 1]}")
            i += 2
        else:
            chunks.append(cur)
            i += 1
    return chunks


def print_qemu(
    command: list[str],
    *,
    indent: str = "  ",
    file: TextIO | None = None,
) -> None:
    """
    Pretty-print a QEMU argv list (e.g. from :func:`build_qemu_command`) with line continuations.

    Pairs like ``-machine`` and ``q35,...`` are kept on one line. Standalone flags like
    ``-enable-kvm`` stay on their own line.
    """
    out = sys.stdout if file is None else file
    chunks = _qemu_pretty_chunks(command)
    if not chunks:
        return
    out.write(chunks[0])
    for chunk in chunks[1:]:
        out.write(" \\\n")
        out.write(indent)
        out.write(chunk)
    out.write("\n")



def _read_guest_errors(path: str, max_bytes: int = 8192) -> str:
    log_path = Path(path)
    if not log_path.is_file():
        return ""
    data = log_path.read_bytes()
    if len(data) > max_bytes:
        data = data[-max_bytes:]
    return data.decode("utf-8", errors="replace")


def stop_vm(launch: VMLaunchResult, *, signal: int = 15, timeout: float = 10.0) -> None:
    """Terminate a guest started by :meth:`VMProfile.vm_launch`."""
    if launch.process is None:
        os.kill(launch.pid, signal)
        return
    launch.process.terminate()
    try:
        launch.process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        launch.process.kill()
        launch.process.wait(timeout=timeout)
