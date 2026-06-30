"""Detect host environment versions (QEMU, kernel, OVMF, OS) for reporting."""

from __future__ import annotations

import platform
import shutil
import subprocess

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


def detect_environment(
    *,
    qemu_binary: str = "qemu-system-x86_64",
    ovmf_path: str | None = None,
) -> dict[str, str | None]:
    """Return a dict of detected host component versions.

    All detection is best-effort: failures produce ``None`` values.
    """
    host_os = get_host_os_info()
    return {
        "qemu_version": _get_qemu_version(qemu_binary),
        "kernel_version": _get_kernel_version(),
        "ovmf_version": _get_ovmf_version(ovmf_path) if ovmf_path else None,
        "ovmf_path": ovmf_path,
        "host_os_name": host_os.get("host_os_name"),
        "host_os_release": host_os.get("host_os_release"),
        "host_os_pretty_name": host_os.get("host_os_pretty_name"),
    }
