"""
Run commands on a guest over AF_VSOCK from the host.

Requires the guest to run a vsock exec agent (see ``guest_agent/vsock_exec.py``)
and QEMU to expose ``vhost-vsock-pci`` / ``virtio-vsock-pci`` with matching CID
(see :func:`vm_profile.build_qemu_command`).
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import shlex
import socket
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from dataclasses import dataclass

if TYPE_CHECKING:
    from .vm_profile import VMProfile

AF_VSOCK = getattr(socket, "AF_VSOCK", 40)

@dataclass(frozen=True)
class GuestCommandResult:
    """Outcome of a command executed on the guest."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    channel: str = "vsock"

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class GuestCommandError(Exception):
    """Raised when guest command transport fails."""



class GuestVsockError(GuestCommandError):
    """Raised when vsock connectivity or command execution fails."""


def _connect(profile: VMProfile, timeout: float) -> socket.socket:
    sock = socket.socket(AF_VSOCK, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((profile.vsock_cid, profile.vsock_port))
    except OSError as exc:
        sock.close()
        raise GuestVsockError(
            f"Failed to connect to guest vsock CID {profile.vsock_cid} "
            f"port {profile.vsock_port}: {exc}"
        ) from exc
    return sock


def _send_request(sock: socket.socket, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sock.sendall(data)


def _read_response(sock: socket.socket, max_bytes: int) -> dict[str, Any]:
    chunks: list[bytes] = []
    total = 0
    while True:
        try:
            part = sock.recv(65536)
        except socket.timeout as exc:
            raise GuestVsockError("Timed out waiting for guest vsock response") from exc
        if not part:
            break
        total += len(part)
        if total > max_bytes:
            raise GuestVsockError(
                f"Guest vsock response exceeded {max_bytes} bytes"
            )
        chunks.append(part)
    if not chunks:
        raise GuestVsockError("Guest vsock agent closed without a response")
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise GuestVsockError("Guest vsock agent returned invalid JSON") from exc


def _request(
    profile: VMProfile,
    payload: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    with _connect(profile, timeout) as sock:
        _send_request(sock, payload)
        return _read_response(sock, profile.vsock_max_response_bytes)

def _parse_command_response(
    command: str, response: dict[str, Any], duration_ms: int
) -> GuestCommandResult:
    if response.get("ok") is True and "exit_code" not in response:
        return GuestCommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=duration_ms,
        )
    if "exit_code" not in response:
        message = str(response.get("error") or response)
        raise GuestVsockError(f"Unexpected guest vsock response: {message}")
    return GuestCommandResult(
        command=command,
        exit_code=int(response["exit_code"]),
        stdout=str(response.get("stdout") or ""),
        stderr=str(response.get("stderr") or ""),
        duration_ms=duration_ms,
    )

def _ping_guest(profile: VMProfile) -> GuestCommandResult:
    start = time.monotonic()

    response = _request(
        profile,
        {"ping": True},
        timeout=profile.vsock_connect_timeout,
    )

    duration_ms = int((time.monotonic() - start) * 1000)
    return _parse_command_response("ping", response, duration_ms)


def wait_for_guest(
    profile: VMProfile,
    *,
    timeout: float | None = None,
    poll_interval: float = 2.0,
) -> None:
    """
    Block until the guest vsock agent responds or timeout is reached.
    """
    boot_timeout = timeout if timeout is not None else profile.vsock_boot_timeout
    deadline = time.monotonic() + boot_timeout
    last_error = ""

    while time.monotonic() < deadline:
        try:
            _ping_guest(profile)
            return
        except GuestVsockError as exc:
            last_error = str(exc)
            time.sleep(poll_interval)

    raise GuestVsockError(
        f"Vsock agent on CID {profile.vsock_cid}:{profile.vsock_port} "
        f"not ready after {boot_timeout}s"
        + (f": {last_error}" if last_error else "")
    )

def check_guest_ready(
    profile: VMProfile,
    *,
    timeout: float | None = None,
    poll_interval: float = 2.0,
) -> tuple[bool, str]:
    """
    Return whether the guest vsock agent responds.
    """
    try:
        wait_for_guest(profile, timeout=timeout, poll_interval=poll_interval)
    except GuestVsockError as exc:
        return False, str(exc)

    return True, ""

def run_guest_command(
    profile: VMProfile,
    command: str,
    *,
    timeout: float | None = None,
    wait_for_ready: bool = False,
) -> GuestCommandResult:
    """
    Run ``command`` on the guest via the vsock exec agent.

    Request JSON: ``{"cmd": "<command>"}``
    Response JSON: ``{"exit_code": int, "stdout": str, "stderr": str}``
    """
    if wait_for_ready:
        wait_for_guest(profile)

    run_timeout = timeout if timeout is not None else profile.vsock_command_timeout
    start = time.monotonic()
    with _connect(profile, run_timeout) as sock:
        _send_request(sock, {"cmd": command})
        response = _read_response(sock, profile.vsock_max_response_bytes)
    duration_ms = int((time.monotonic() - start) * 1000)
    return _parse_command_response(command, response, duration_ms)


def fetch_guest_file_bytes(
    profile: VMProfile,
    guest_path: str,
    *,
    timeout: float | None = None,
) -> bytes:
    """
    Read a file from the guest by running ``base64`` on it over the cmd channel.

    Whitespace is stripped from stdout before decoding (PEM-style line breaks).
    Large files may exceed the vsock response limit; prefer small artifacts
    (e.g. attestation reports) or raise ``guest_vsock`` limits on ``VMProfile``.
    """
    cmd = f"base64 {shlex.quote(guest_path)}"
    gcr = run_guest_command(profile, cmd, timeout=timeout)
    if gcr.exit_code != 0:
        raise GuestVsockError(
            f"Guest base64 of {guest_path!r} failed with exit {gcr.exit_code}: "
            f"{gcr.stderr.strip() or gcr.stdout.strip()}"
        )
    clean = re.sub(r"\s+", "", gcr.stdout)
    if not clean:
        raise GuestVsockError(f"Guest returned empty base64 for {guest_path!r}")
    try:
        return base64.b64decode(clean, validate=True)
    except binascii.Error as exc:
        raise GuestVsockError(
            f"Invalid base64 while fetching {guest_path!r} from guest"
        ) from exc


def pull_guest_file_to_host(
    profile: VMProfile,
    guest_path: str,
    host_path: Path,
    *,
    timeout: float | None = None,
) -> None:
    """Decode :func:`fetch_guest_file_bytes` and write bytes to ``host_path``."""
    data = fetch_guest_file_bytes(profile, guest_path, timeout=timeout)
    host_path = Path(host_path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_bytes(data)
