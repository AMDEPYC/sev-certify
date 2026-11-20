"""Identify the host EPYC processor and the certifications that apply to it.

CPUID leaf ``8000_0001`` supplies the family, model, and socket package type.
Package type is required where one family and model range covers two products
(Bergamo and Siena, Turin and Sorano). Venice is intentionally absent.

Certifications accumulate. A processor runs every generation up to its own,
matching the hardware tables in ``docs/certifications.md``. Bergamo and Siena
share Genoa's CC root certificate, so they run through 3.1. Naples and Rome
predate these certifications and select none.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

# Manifest versions in the order a newer processor still runs.
_CERT_ORDER = ("3.0", "3.1", "4.0", "4.1")


class CpuidReadError(Exception):
    """CPUID leaf 8000_0001 could not be read."""


@dataclass(frozen=True)
class Processor:
    """One AMD EPYC generation."""

    name: str
    series: str
    socket: str
    cc_root: str
    cert_versions: tuple[str, ...]


def _through(version: str) -> tuple[str, ...]:
    """Certifications from 3.0 through ``version``, inclusive."""
    index = _CERT_ORDER.index(version)
    return _CERT_ORDER[: index + 1]


def _processor(
    name: str, series: str, socket: str, cc_root: str, max_cert: str | None,
) -> Processor:
    versions = () if max_cert is None else _through(max_cert)
    return Processor(name, series, socket, cc_root, versions)


# (family, model min, model max, pkg type or None, processor)
# pkg type None matches any socket. Specific rows are listed before none.
_ROWS: tuple[tuple[int, int, int, int | None, Processor], ...] = (
    (0x17, 0x00, 0x0F, None, _processor("Naples", "7001", "SP3", "Naples", None)),
    (0x17, 0x30, 0x3F, None, _processor("Rome", "7002", "SP3", "Rome", None)),
    (0x19, 0x00, 0x0F, None, _processor("Milan", "7003", "SP3", "Milan", "3.0")),
    (0x19, 0x10, 0x1F, None, _processor("Genoa", "9004", "SP5", "Genoa", "3.1")),
    (0x19, 0xA0, 0xAF, 0x4, _processor("Bergamo", "97X4", "SP5", "Genoa", "3.1")),
    (0x19, 0xA0, 0xAF, 0x8, _processor("Siena", "8004", "SP6", "Genoa", "3.1")),
    (0x1A, 0x00, 0x1F, 0x4, _processor("Turin", "9005", "SP5", "Turin", "4.1")),
    (0x1A, 0x00, 0x1F, 0x8, _processor("Sorano", "8005", "SP6", "Turin", "4.0")),
)


def _bits(value: int, hi: int, lo: int) -> int:
    """Inclusive bit field ``value[hi:lo]``."""
    width = hi - lo + 1
    return (value >> lo) & ((1 << width) - 1)


def decode_cpuid(eax: int, ebx: int) -> tuple[int, int, int]:
    """Return ``(family, model, pkg_type)`` from leaf ``8000_0001`` EAX and EBX.

    Family is base family (EAX 11:8) plus extended family (EAX 27:20). Model is
    base model (EAX 7:4) with extended model (EAX 19:16) in the high nibble.
    Package type is EBX 31:28.
    """
    family = _bits(eax, 11, 8) + _bits(eax, 27, 20)
    model = _bits(eax, 7, 4) | (_bits(eax, 19, 16) << 4)
    pkg_type = _bits(ebx, 31, 28)
    return family, model, pkg_type


def identify(family: int, model: int, pkg_type: int) -> Processor | None:
    """Match a decoded CPUID identity. ``None`` when the part is not in the table."""
    for row_family, model_min, model_max, row_pkg, processor in _ROWS:
        if family != row_family or not model_min <= model <= model_max:
            continue
        if row_pkg is not None and row_pkg != pkg_type:
            continue
        return processor
    return None


def _read_cpuid_dev(leaf: int) -> tuple[int, int, int, int] | None:
    """Read one CPUID leaf from the kernel cpuid device. ``None`` if unavailable."""
    path = Path("/dev/cpu/0/cpuid")
    if not path.exists():
        return None
    try:
        with path.open("rb") as cpu:
            cpu.seek(leaf)
            data = cpu.read(16)
    except OSError:
        return None
    if len(data) != 16:
        return None
    return struct.unpack("<4I", data)


def _read_cpuid_module(leaf: int) -> tuple[int, int, int, int] | None:
    """Read one CPUID leaf via the optional ``cpuid`` package."""
    try:
        from cpuid import cpuid
    except ImportError:
        return None
    try:
        values = cpuid(leaf)
    except (OSError, ValueError):
        return None
    if values is None or len(values) < 4:
        return None
    return tuple(int(value) for value in values[:4])


def read_cpuid(leaf: int) -> tuple[int, int, int, int]:
    """Return ``(eax, ebx, ecx, edx)`` for ``leaf``.

    The kernel device is preferred. The ``cpuid`` package is the fallback used
    by the host image.
    """
    values = _read_cpuid_dev(leaf)
    if values is None:
        values = _read_cpuid_module(leaf)
    if values is None:
        raise CpuidReadError(
            "could not read CPUID "
            f"(leaf 0x{leaf:08X}); /dev/cpu/0/cpuid and the cpuid package "
            "were both unavailable"
        )
    return values


def identify_host_processor() -> Processor | None:
    """Identify the processor this process is running on."""
    eax, ebx, _, _ = read_cpuid(0x80000001)
    family, model, pkg_type = decode_cpuid(eax, ebx)
    return identify(family, model, pkg_type)
