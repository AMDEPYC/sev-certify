"""Detect host environment versions (QEMU, kernel, OVMF, OS) for reporting."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess

from pathlib import Path

from .os_info import get_host_os_info


def _get_qemu_version(binary: str) -> str | None:
    """Run ``<binary> --version`` and parse the version string."""
    resolved = shutil.which(binary)
    if not resolved:
        return None
    try:
        proc = subprocess.run(
            [resolved, "--version"],
            capture_output=True, text=True, timeout=5,
        )
        first_line = proc.stdout.split("\n", 1)[0]
        prefix = "QEMU emulator version "
        if first_line.startswith(prefix):
            return first_line[len(prefix):]
        return None
    except Exception:
        return None


def _get_kernel_version() -> str | None:
    """Return the running kernel release string."""
    try:
        return platform.release()
    except Exception:
        return None


def _get_ovmf_version(path: str) -> str | None:
    """Try dpkg then rpm to find the package version owning *path*."""
    if not os.path.exists(path):
        return None

    # dpkg -S /path -> "package: /path"
    try:
        proc = subprocess.run(
            ["dpkg", "-S", path],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            pkg = proc.stdout.strip().split(":", 1)[0]
            info = subprocess.run(
                ["dpkg", "-s", pkg],
                capture_output=True, text=True, timeout=5,
            )
            for line in info.stdout.splitlines():
                if line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
                    return f"{version} ({pkg})"
    except Exception:
        pass

    # rpm -qf /path -> "package-version"
    try:
        proc = subprocess.run(
            ["rpm", "-qf", path],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass

    return None


def _run_tool(args: list[str], timeout: int = 5) -> str | None:
    """Run *args* and return stripped stdout, or None on any failure."""
    resolved = shutil.which(args[0])
    if not resolved:
        return None
    try:
        proc = subprocess.run(
            [resolved, *args[1:]],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return out or None


def _get_tool_version(tool: str) -> str | None:
    """Return ``<tool> --version`` output, e.g. ``snpguest 0.10.0``."""
    out = _run_tool([tool, "--version"])
    return out.splitlines()[0].strip() if out else None


def _get_sev_firmware_version() -> str | None:
    """Return the SEV firmware version the PSP is currently running.

    This is the firmware actually in effect, which is not necessarily the one
    the BIOS supplied: the ``ccp`` driver loads ``/lib/firmware/amd/*.sbin`` at
    boot when present, so it varies with the host OS image rather than with the
    platform.  Requires root; returns None otherwise.
    """
    out = _run_tool(["snphost", "show", "version"])
    return out.splitlines()[0].strip() if out else None


def _get_reported_tcb() -> str | None:
    """Return the reported TCB as a single line, e.g. ``bootloader=9 tee=0 …``.

    ``snphost show tcb`` prints a multi-line block; it is condensed here so the
    value fits on one line of the environment report.
    """
    out = _run_tool(["snphost", "show", "tcb"])
    if not out:
        return None
    wanted = {
        "boot loader": "bootloader",
        "tee": "tee",
        "snp": "snp",
        "microcode": "microcode",
        "fmc": "fmc",
    }
    found: dict[str, str] = {}
    for line in out.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        short = wanted.get(key.strip().lower())
        if short and value.strip():
            found[short] = value.strip()
    if not found:
        return None
    order = ("bootloader", "tee", "snp", "microcode", "fmc")
    return " ".join(f"{k}={found[k]}" for k in order if k in found)


def _get_platform_identifier() -> str | None:
    """Return the platform identifier from ``snphost show identifier``."""
    out = _run_tool(["snphost", "show", "identifier"])
    return out.splitlines()[0].strip() if out else None


# Offsets into the SNP attestation report, per the ABI specification (56860).
# CPUID_FAM_ID / MOD_ID / STEP exist only from report version 3 onwards.
_REPORT_CPUID_FAM = 0x188
_REPORT_CPUID_MOD = 0x189
_REPORT_CPUID_STEP = 0x18A
_REPORT_CHIP_ID = 0x1A0
_REPORT_CHIP_ID_LEN = 64
_REPORT_MIN_LEN = _REPORT_CHIP_ID + _REPORT_CHIP_ID_LEN


def summarize_report(path: str | os.PathLike[str]) -> str | None:
    """Summarize a report: version, CPUID triple, and whether CHIP_ID is zeroed.

    These are the fields that decide how a report is *parsed*, as distinct from
    what it attests.  Consumers pick a TCB layout from the processor generation,
    which they derive from the CPUID bytes, so a report carrying unexpected
    values there is rejected before any of its contents are read.

    CHIP_ID is reported as zeroed or present because ``MASK_CHIP_ID`` zeroes it
    and ``snphost show`` offers no way to read that setting back: a zeroed
    CHIP_ID is the only externally visible sign that masking is in effect.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except Exception:
        return None
    if len(data) < _REPORT_MIN_LEN:
        return None

    version = int.from_bytes(data[0:4], "little")
    parts = [f"version={version}"]
    if version >= 3:
        parts.append(
            "cpuid=0x{:02x}/0x{:02x}/0x{:02x}".format(
                data[_REPORT_CPUID_FAM],
                data[_REPORT_CPUID_MOD],
                data[_REPORT_CPUID_STEP],
            )
        )
    chip_id = data[_REPORT_CHIP_ID:_REPORT_CHIP_ID + _REPORT_CHIP_ID_LEN]
    parts.append("chip_id=" + ("zeroed" if not any(chip_id) else "present"))
    return " ".join(parts)


#: Report artifacts to describe, in no particular preference order — the newest
#: wins.  ``tsm-report.bin`` comes from the kernel's configfs-TSM interface and
#: exists even when the snpguest-produced ``report.bin`` does not, since that
#: command declines to write a report it cannot classify.
REPORT_ARTIFACT_NAMES = ("report.bin", "tsm-report.bin")


def find_recent_report(
    artifacts_root: str | os.PathLike[str],
    since: float,
) -> "Path | None":
    """Return the newest report artifact under *artifacts_root* newer than *since*.

    The mtime bound stops a report left behind by an earlier run being described
    as though this run had produced it.
    """
    candidates: list[Path] = []
    try:
        for name in REPORT_ARTIFACT_NAMES:
            candidates += [
                p for p in Path(artifacts_root).rglob(name)
                if p.stat().st_mtime >= since
            ]
    except Exception:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


#: Written by modules/build/common/snpguest/mkosi.build at image build time.
SNPGUEST_TAG_FILE = "/usr/local/share/sev-certify/snpguest-tag"


def _get_snpguest_tag() -> str | None:
    """Return the snpguest release tag this image was built with.

    The image build resolves "latest" at build time unless SNPGUEST_TAG is
    pinned, so the installed tooling is not implied by the source revision.
    Absent on hosts that were not built by this project.
    """
    try:
        with open(SNPGUEST_TAG_FILE) as fh:
            return fh.read().strip() or None
    except Exception:
        return None


def _get_host_cpu() -> dict[str, str | None]:
    """Return the host CPU model name and CPUID family/model/stepping.

    Reported because generation-dependent behaviour keys on family and model —
    both in this harness and in the tooling it drives — so a failure that turns
    on the processor generation is otherwise undiagnosable from a result alone.
    """
    fields: dict[str, str] = {}
    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                key, sep, value = line.partition(":")
                if not sep:
                    continue
                key = key.strip().lower()
                if key in ("model name", "cpu family", "model", "stepping"):
                    fields.setdefault(key, value.strip())
                if len(fields) == 4:
                    break
    except Exception:
        return {"host_cpu_model": None, "host_cpu_id": None}

    def _hex(name: str) -> str | None:
        raw = fields.get(name)
        try:
            return f"0x{int(raw):x}"
        except (TypeError, ValueError):
            return None

    family, model, stepping = _hex("cpu family"), _hex("model"), _hex("stepping")
    cpu_id = None
    if family and model:
        cpu_id = f"family {family} model {model}"
        if stepping:
            cpu_id += f" stepping {stepping}"
    return {"host_cpu_model": fields.get("model name"), "host_cpu_id": cpu_id}


def detect_environment(
    *,
    qemu_binary: str = "qemu-system-x86_64",
    ovmf_path: str | None = None,
) -> dict[str, str | None]:
    """Return a dict of detected host component versions.

    All detection is best-effort: failures produce ``None`` values.
    """
    host_os = get_host_os_info()
    host_cpu = _get_host_cpu()
    return {
        "qemu_version": _get_qemu_version(qemu_binary),
        "qemu_binary": qemu_binary,
        "kernel_version": _get_kernel_version(),
        "ovmf_version": _get_ovmf_version(ovmf_path) if ovmf_path else None,
        "ovmf_path": ovmf_path,
        "host_os_name": host_os.get("host_os_name"),
        "host_os_release": host_os.get("host_os_release"),
        "host_os_pretty_name": host_os.get("host_os_pretty_name"),
        # SEV-specific facts.  These are what distinguish two hosts that look
        # identical by OS and QEMU version but behave differently under SNP.
        "sev_firmware_version": _get_sev_firmware_version(),
        "reported_tcb": _get_reported_tcb(),
        "snphost_version": _get_tool_version("snphost"),
        "snpguest_version": _get_tool_version("snpguest"),
        "snpguest_tag": _get_snpguest_tag(),
        "platform_identifier": _get_platform_identifier(),
        "host_cpu_model": host_cpu["host_cpu_model"],
        "host_cpu_id": host_cpu["host_cpu_id"],
    }
