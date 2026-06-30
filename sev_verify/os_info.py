"""Detect Host and Guest OS name and release information."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .vm_profile import VMProfile


def _parse_os_release(content: str) -> dict[str, str]:
    """Parse /etc/os-release content into a dict."""
    result: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip('"').strip("'")
        result[key] = value
    return result


def get_host_os_info(os_release_path: str = "/etc/os-release") -> dict[str, str | None]:
    """Read host OS name and release from /etc/os-release.

    Returns a dict with keys:
        - host_os_name: e.g. "Fedora Linux"
        - host_os_release: e.g. "41 (Server Edition)"
        - host_os_pretty_name: e.g. "Fedora Linux 41 (Server Edition)"
        - host_os_id: e.g. "fedora"
    """
    try:
        content = Path(os_release_path).read_text()
    except OSError:
        return {
            "host_os_name": None,
            "host_os_release": None,
            "host_os_pretty_name": None,
            "host_os_id": None,
        }

    data = _parse_os_release(content)
    return {
        "host_os_name": data.get("NAME"),
        "host_os_release": data.get("VERSION"),
        "host_os_pretty_name": data.get("PRETTY_NAME"),
        "host_os_id": data.get("ID"),
    }


def get_guest_os_info(profile: "VMProfile") -> dict[str, str | None]:
    """Read guest OS name and release via vsock command.

    Requires an active guest with vsock agent running.

    Returns a dict with keys:
        - guest_os_name: e.g. "Ubuntu"
        - guest_os_release: e.g. "24.04 LTS (Noble Numbat)"
        - guest_os_pretty_name: e.g. "Ubuntu 24.04 LTS"
        - guest_os_id: e.g. "ubuntu"
    """
    from .guest_vsock import run_guest_command, GuestVsockError

    try:
        result = run_guest_command(profile, "cat /etc/os-release", timeout=10)
        if result.exit_code != 0:
            return {
                "guest_os_name": None,
                "guest_os_release": None,
                "guest_os_pretty_name": None,
                "guest_os_id": None,
            }

        data = _parse_os_release(result.stdout)
        return {
            "guest_os_name": data.get("NAME"),
            "guest_os_release": data.get("VERSION"),
            "guest_os_pretty_name": data.get("PRETTY_NAME"),
            "guest_os_id": data.get("ID"),
        }
    except GuestVsockError:
        return {
            "guest_os_name": None,
            "guest_os_release": None,
            "guest_os_pretty_name": None,
            "guest_os_id": None,
        }


def format_os_info(os_info: dict[str, str | None]) -> str | None:
    """Format OS info dict into a single display string.

    Prefers PRETTY_NAME if available, otherwise combines NAME and VERSION.
    Returns None if no info is available.
    """
    pretty = os_info.get("host_os_pretty_name") or os_info.get("guest_os_pretty_name")
    if pretty:
        return pretty

    name = os_info.get("host_os_name") or os_info.get("guest_os_name")
    release = os_info.get("host_os_release") or os_info.get("guest_os_release")

    if name and release:
        return f"{name} {release}"
    return name or release or None


def update_environment_with_guest_os(
    environment: dict[str, str | None],
    profile: "VMProfile",
) -> None:
    """Update environment dict in-place with guest OS information.

    Requires an active guest with vsock agent. Silently skips if guest
    info cannot be retrieved.
    """
    if environment.get("guest_os_pretty_name"):
        return

    guest_info = get_guest_os_info(profile)
    environment["guest_os_name"] = guest_info.get("guest_os_name")
    environment["guest_os_release"] = guest_info.get("guest_os_release")
    environment["guest_os_pretty_name"] = guest_info.get("guest_os_pretty_name")
    environment["guest_os_id"] = guest_info.get("guest_os_id")
