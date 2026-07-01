"""Parse the SEV-SNP ATTESTATION_REPORT binary structure.

Reading ``report.bin`` directly, rather than regexing ``snpguest display
report``, removes a dependency on a CLI's human-readable output format. The
binary layout is fixed by the SEV-SNP ABI and, unlike the CLI text, the report
is *self-describing*: VERSION is the first four bytes, so every report states
which layout it uses and can be checked on the spot.

Layout is version-dependent in practice — the report version tracks firmware,
which tracks CPU generation — but versions have only ever *appended* fields.
Everything below 0x188 is common to v2 and v3; the CPUID family/model/stepping
triple at 0x188 exists only in v3+.

Every offset here was validated against a real v3 report from an EPYC 9654
(Genoa, CPUID 19h/11h), cross-checked against independently known values:
GUEST_SVN/POLICY/FAMILY_ID/IMAGE_ID against the values the ID block was built
with, REPORTED_TCB against ``snphost ok``, AUTHOR_KEY_DIGEST against the known
all-zero author key, and CPUID against the CPU model.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

#: ATTESTATION_REPORT is a fixed-size structure.
REPORT_SIZE = 1184

#: Report versions whose layout we read. v3 is verified on hardware; v2 shares
#: the same layout for every field below 0x188.
KNOWN_VERSIONS = frozenset({2, 3})

# Field offsets. See module docstring for how these were validated.
_OFF_VERSION = 0x000
_OFF_GUEST_SVN = 0x004
_OFF_POLICY = 0x008
_OFF_FAMILY_ID = 0x010
_OFF_IMAGE_ID = 0x020
_OFF_VMPL = 0x030
_OFF_REPORT_DATA = 0x050
_OFF_MEASUREMENT = 0x090
_OFF_HOST_DATA = 0x0C0
_OFF_ID_KEY_DIGEST = 0x0E0
_OFF_AUTHOR_KEY_DIGEST = 0x110
_OFF_REPORT_ID = 0x140
_OFF_REPORTED_TCB = 0x180
_OFF_CPUID_FAM = 0x188  # v3+

_LEN_ID = 16
_LEN_MEASUREMENT = 48
_LEN_DIGEST = 48
_LEN_REPORT_DATA = 64
_LEN_HOST_DATA = 32
_LEN_REPORT_ID = 32


class ReportError(Exception):
    """Base class for attestation report problems."""


class ReportMalformed(ReportError):
    """The file is not a well-formed ATTESTATION_REPORT."""


class ReportUnsupportedVersion(ReportError):
    """The report declares a version whose layout we have not validated."""


@dataclass(frozen=True)
class TcbVersion:
    """Decoded SNP TCB_VERSION — the same four values ``snphost ok`` prints."""

    bootloader: int
    tee: int
    snp: int
    microcode: int

    @classmethod
    def from_bytes(cls, raw: bytes) -> TcbVersion:
        # byte 0 BOOT_LOADER, byte 1 TEE, bytes 2-5 reserved,
        # byte 6 SNP, byte 7 MICROCODE.
        return cls(bootloader=raw[0], tee=raw[1], snp=raw[6], microcode=raw[7])

    def __str__(self) -> str:
        return (
            f"bootloader={self.bootloader} tee={self.tee} "
            f"snp={self.snp} microcode={self.microcode}"
        )


@dataclass(frozen=True)
class AttestationReport:
    """The fields of an ATTESTATION_REPORT that we read."""

    version: int
    guest_svn: int
    policy: int
    family_id: bytes
    image_id: bytes
    vmpl: int
    report_data: bytes
    measurement: bytes
    host_data: bytes
    id_key_digest: bytes
    author_key_digest: bytes
    report_id: bytes
    reported_tcb: TcbVersion
    #: (family, model, stepping) — v3+ only, None on older reports.
    cpuid: tuple[int, int, int] | None

    @property
    def id_block_used(self) -> bool:
        """True when ID_KEY_DIGEST is set, i.e. the guest launched with an ID block."""
        return any(self.id_key_digest)


def parse(data: bytes) -> AttestationReport:
    """Parse raw report bytes.

    Raises:
        ReportMalformed: wrong size.
        ReportUnsupportedVersion: layout not validated for that version.
    """
    if len(data) != REPORT_SIZE:
        raise ReportMalformed(
            f"expected a {REPORT_SIZE}-byte ATTESTATION_REPORT, got {len(data)} bytes"
        )

    (version,) = struct.unpack_from("<I", data, _OFF_VERSION)
    if version not in KNOWN_VERSIONS:
        raise ReportUnsupportedVersion(
            f"report declares VERSION {version}; this parser has been validated "
            f"for {sorted(KNOWN_VERSIONS)}"
        )

    (guest_svn,) = struct.unpack_from("<I", data, _OFF_GUEST_SVN)
    (policy,) = struct.unpack_from("<Q", data, _OFF_POLICY)
    (vmpl,) = struct.unpack_from("<I", data, _OFF_VMPL)

    def field(off: int, length: int) -> bytes:
        return data[off:off + length]

    cpuid = None
    if version >= 3:
        cpuid = (
            data[_OFF_CPUID_FAM],
            data[_OFF_CPUID_FAM + 1],
            data[_OFF_CPUID_FAM + 2],
        )

    return AttestationReport(
        version=version,
        guest_svn=guest_svn,
        policy=policy,
        family_id=field(_OFF_FAMILY_ID, _LEN_ID),
        image_id=field(_OFF_IMAGE_ID, _LEN_ID),
        vmpl=vmpl,
        report_data=field(_OFF_REPORT_DATA, _LEN_REPORT_DATA),
        measurement=field(_OFF_MEASUREMENT, _LEN_MEASUREMENT),
        host_data=field(_OFF_HOST_DATA, _LEN_HOST_DATA),
        id_key_digest=field(_OFF_ID_KEY_DIGEST, _LEN_DIGEST),
        author_key_digest=field(_OFF_AUTHOR_KEY_DIGEST, _LEN_DIGEST),
        report_id=field(_OFF_REPORT_ID, _LEN_REPORT_ID),
        reported_tcb=TcbVersion.from_bytes(field(_OFF_REPORTED_TCB, 8)),
        cpuid=cpuid,
    )


def read(path: Path) -> AttestationReport:
    """Read and parse an ATTESTATION_REPORT file.

    Raises:
        ReportMalformed: file missing or wrong size.
        ReportUnsupportedVersion: layout not validated for that version.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise ReportMalformed(f"{path.name} not found") from exc
    return parse(data)
