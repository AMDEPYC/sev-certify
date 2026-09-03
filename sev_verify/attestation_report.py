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

**TCB_VERSION is the exception, and it is not version-dependent but
*generation*-dependent.** The ``sev`` crate decodes it two different ways
(``from_legacy_bytes`` vs ``from_turin_bytes``), and the layouts are
incompatible and indistinguishable from the eight bytes alone:

===========  =====================  ====================
byte         Milan / Genoa          Turin / Venice
===========  =====================  ====================
0            BOOT_LOADER            FMC
1            TEE                    BOOT_LOADER
2            --                     TEE
3            --                     SNP
6            SNP                    --
7            MICROCODE              MICROCODE
===========  =====================  ====================

Decoding therefore requires knowing the processor generation. The authoritative
source is the **host's** CPUID — a report parsed here was produced by a guest on
this machine, and unlike the report's own CPUID copy it is present regardless of
report version. :func:`host_generation` reads it; :func:`parse` takes the result
as an argument so the parser itself stays a pure function of its input and can
be unit-tested without hardware.

When the report is v3+ it also carries a CPUID copy, which :func:`parse` uses as
a cross-check — but only when it can be resolved. Firmware does not always fill
it in: SEV firmware 1.55 build 38 leaves all three bytes zero in version-3
reports, and build 39 populates them. A report like that is perfectly decodable
using the host's generation, so it is decoded, and the failed cross-check is
recorded in ``cpuid_note`` rather than raised. Refusing it would reject a usable
report over a field the platform declined to fill.

The error is reserved for the case that actually indicates a problem: both the
host's and the report's CPUID resolve to validated generations, and they
disagree. Then the report did not come from this machine and neither layout can
be trusted for it. Where no generation is supplied at all, the report's own
CPUID is used if it resolves; if it does not, TCB_VERSION is left undecoded,
matching what a v2 report — which carries no CPUID — already does.

An unrecognised processor still raises when it is the *only* source, since
guessing a layout would produce plausible-looking but wrong values with no error.

Report *versions* are treated more leniently than processors, deliberately. A
newer version is decoded with the newest validated offsets and the assumption
recorded in ``version_note``; only versions older than the validated range are
refused. The asymmetry is the point: misreading a generation corrupts values
silently, whereas an unrecognised version at worst leaves new fields unread,
and the version — unlike the generation — is stated in the report itself.

Offsets are confirmed against real reports rather than read off a spec. The
first such validation used a v3 report from an EPYC 9654 (Genoa, CPUID
19h/11h), cross-checked against independently known values:
GUEST_SVN/POLICY/FAMILY_ID/IMAGE_ID against the values the ID block was built
with, REPORTED_TCB against ``snphost ok``, AUTHOR_KEY_DIGEST against the known
all-zero author key, and CPUID against the CPU model.

The second used a **version 5** report from an EPYC 9575F (Turin, CPUID
1Ah/02h), which validated the Turin TCB layout for the first time. Its
REPORTED_TCB bytes were ``0103020600000062``, decoding under the Turin layout to
``bootloader=3 tee=2 snp=6 microcode=98 fmc=1`` — matching ``snphost show tcb``
exactly. Decoded under the legacy layout the same bytes give
``bootloader=1 tee=3 snp=0 microcode=98`` with no FMC: plausible values, silently
wrong, which is precisely the failure this generation gate exists to prevent.
That report also confirmed v5 moved none of the fields read here.

As further processors are exercised, extend :data:`SUPPORTED_GENERATIONS` and
record the validation here.

.. note::

   This module is a **short-term stand-in**. The right long-term source is
   ``snpguest``, which already parses reports correctly and generation-aware via
   the ``sev`` crate but currently exposes them only as human-readable text
   (``println!("{}", att_report)``). Once snpguest gains machine-readable
   output, this module should be replaced by consuming that output rather than
   extended to cover further processor generations.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

#: ATTESTATION_REPORT is a fixed-size structure.
REPORT_SIZE = 1184

#: Report versions whose layout we read. v3 and v5 are verified on hardware;
#: v2 shares the same layout for every field below 0x188.
#:
#: Membership here means *validated*, not *accepted*. A newer version is still
#: decoded — with these offsets, and a note on the result recording the
#: assumption — because versions have only ever appended fields, so the cost of
#: being wrong is missing something new rather than misreading something old.
#: Only versions *older* than this set are refused, where fields may genuinely
#: not exist. See :func:`parse` for why that is a weaker stance than the one
#: :data:`SUPPORTED_GENERATIONS` takes.
#:
#: Note this is an axis independent of processor generation. The version decides
#: which fields exist and where; the generation decides how TCB_VERSION's eight
#: bytes are ordered. A v3 report can come from either a legacy-layout or a
#: Turin-layout processor, so both gates are needed and neither implies the
#: other.
#:
#: v5 was added after a real v5 report from an EPYC 9575F decoded correctly at
#: these offsets — REPORTED_TCB matched ``snphost show tcb`` and CPUID matched
#: the host's, confirming its additions moved nothing we read. v4 has never been
#: seen; it decodes on the append-only assumption and says so. To promote a
#: version to validated:
#:
#:   1. Check whether it shares framing with a version already listed. The
#:      ``sev`` crate's ``ReportVariant`` mapping groups versions by layout —
#:      currently ``2 => V2``, ``3 | 4 => V3``, ``_ => V5`` — so a version
#:      sharing a variant with one listed here reads identically. That makes v4
#:      the cheap case and v5 the one needing real scrutiny.
#:   2. Remember additions are not always new offsets. v5 adds
#:      ``page_swap_disabled`` to GuestPolicy and SEV-TIO to PlatformInfo, which
#:      are new *bits in existing fields* and move nothing.
#:   3. Parse a real report of that version and check decoded values against
#:      independently known ones, as the module docstring records for v3.
#:   4. Add the version here and record the validation in the docstring.
KNOWN_VERSIONS = frozenset({2, 3, 5})

#: TCB_VERSION byte layouts. See the module docstring for the two orderings.
TCB_LAYOUT_LEGACY = "legacy"
TCB_LAYOUT_TURIN = "turin"

#: Processor generations this module has been **validated against**, keyed by
#: CPUID family and an inclusive model range.
#:
#: This is deliberately a record of what has been exercised on real hardware,
#: not of what we believe we could decode. A certification harness reporting a
#: pass on silicon it has never run on is the failure this gate exists to
#: prevent, so an unrecognised processor raises rather than being decoded on
#: the assumption that a transcribed layout is right.
#:
#: Keyed on family/model *pairs*, not family/model/stepping triples: AMD scopes
#: SEV firmware images by family and model only — ``amd_sev_fam19h_model1xh``,
#: ``amd_sev_fam1ah_model0xh`` — and stepping appears nowhere in that
#: partitioning. snpguest's ``get_processor_model`` (``src/fetch.rs``) splits
#: the same way for VCEK lookup.
#:
#: To add a generation: run the ID block test on that hardware, confirm the
#: decoded fields against ``snphost show tcb`` and the values the ID block was
#: built with, then add the entry and note the validation in the docstring.
#: The layouts for generations not yet exercised here, taken from the ``sev``
#: crate, are:
#:
#:     0x19 / 0x00-0x0F  Milan          legacy
#:     0x19 / 0xA0-0xAF  Bergamo/Siena  legacy
SUPPORTED_GENERATIONS: tuple[tuple[int, range, str, str], ...] = (
    # (cpuid_family, model range, name, TCB layout)
    (0x19, range(0x10, 0x20), "Genoa", TCB_LAYOUT_LEGACY),  # EPYC 9654, v3 reports
    (0x1A, range(0x00, 0x12), "Turin", TCB_LAYOUT_TURIN),   # EPYC 9575F, v5 reports
)

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


class ReportUnsupportedCpu(ReportError):
    """The report comes from a processor this module has not been validated on.

    Raised rather than decoding on the assumption that a transcribed layout is
    correct — TCB_VERSION in particular is laid out differently on Turin, so a
    wrong guess yields plausible values rather than an error.
    """


@dataclass(frozen=True)
class TcbVersion:
    """Decoded SNP TCB_VERSION — the same values ``snphost ok`` prints.

    ``fmc`` exists only on Turin and later; it is ``None`` elsewhere, matching
    the ``sev`` crate's ``Option<u8>``.
    """

    bootloader: int
    tee: int
    snp: int
    microcode: int
    fmc: int | None = None

    @classmethod
    def from_bytes(cls, raw: bytes, layout: str) -> TcbVersion:
        """Decode the 8-byte TCB_VERSION using the given generation layout."""
        if layout == TCB_LAYOUT_TURIN:
            # byte 0 FMC, 1 BOOT_LOADER, 2 TEE, 3 SNP, 7 MICROCODE.
            return cls(
                fmc=raw[0],
                bootloader=raw[1],
                tee=raw[2],
                snp=raw[3],
                microcode=raw[7],
            )
        if layout == TCB_LAYOUT_LEGACY:
            # byte 0 BOOT_LOADER, 1 TEE, bytes 2-5 reserved, 6 SNP, 7 MICROCODE.
            return cls(bootloader=raw[0], tee=raw[1], snp=raw[6], microcode=raw[7])
        raise ReportUnsupportedCpu(f"unknown TCB layout {layout!r}")

    def __str__(self) -> str:
        base = (
            f"bootloader={self.bootloader} tee={self.tee} "
            f"snp={self.snp} microcode={self.microcode}"
        )
        return base if self.fmc is None else f"fmc={self.fmc} {base}"


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
    #: ``None`` when the processor generation was unknown, since the byte
    #: layout differs between generations and cannot be guessed.
    reported_tcb: TcbVersion | None
    #: (family, model, stepping) — v3+ only, None on older reports.
    cpuid: tuple[int, int, int] | None
    #: Validated processor generation this report was decoded as, or "unknown".
    generation: str
    #: Set when the report's own CPUID could not be used and the host's was
    #: preferred — for instance when firmware leaves those bytes zero. ``None``
    #: when the report's CPUID was absent by design (v2) or agreed with the host.
    cpuid_note: str | None = None
    #: Set when the report declared a version newer than any validated here and
    #: was decoded with the newest known field offsets. ``None`` when the
    #: version was one this parser has been checked against.
    version_note: str | None = None

    @property
    def id_block_used(self) -> bool:
        """True when ID_KEY_DIGEST is set, i.e. the guest launched with an ID block."""
        return any(self.id_key_digest)


def resolve_generation(family: int, model: int) -> tuple[str, str]:
    """Return ``(name, tcb_layout)`` for a CPUID family/model pair.

    Raises:
        ReportUnsupportedCpu: the pair is not in :data:`SUPPORTED_GENERATIONS`.
    """
    for fam, models, name, layout in SUPPORTED_GENERATIONS:
        if family == fam and model in models:
            return name, layout

    validated = ", ".join(
        f"{name} (family 0x{fam:02X} model 0x{models[0]:02X}-0x{models[-1]:02X})"
        for fam, models, name, _ in SUPPORTED_GENERATIONS
    )
    raise ReportUnsupportedCpu(
        f"CPUID family 0x{family:02X} model 0x{model:02X} has not been validated "
        f"against. Validated: {validated}. TCB_VERSION is laid out differently "
        f"across processor generations, so decoding anyway would produce "
        f"plausible but wrong values. See SUPPORTED_GENERATIONS in "
        f"sev_verify/attestation_report.py."
    )


def host_generation() -> tuple[str, str]:
    """Return ``(name, tcb_layout)`` for the CPU this process is running on.

    Reads ``/proc/cpuinfo``. This is the authoritative source when parsing a
    report produced by a guest on this machine: unlike the report's CPUID copy
    it is present regardless of report version.

    Raises:
        ReportUnsupportedCpu: family/model unreadable, or not validated.
    """
    family = model = None
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                key, _, value = line.partition(":")
                key = key.strip()
                if key == "cpu family" and family is None:
                    family = int(value.strip())
                elif key == "model" and model is None:
                    model = int(value.strip())
                if family is not None and model is not None:
                    break
    except OSError as exc:
        raise ReportUnsupportedCpu(f"could not read /proc/cpuinfo: {exc}") from exc

    if family is None or model is None:
        raise ReportUnsupportedCpu(
            "could not determine CPU family/model from /proc/cpuinfo"
        )
    return resolve_generation(family, model)


def parse(
    data: bytes, *, generation: tuple[str, str] | None = None
) -> AttestationReport:
    """Parse raw report bytes.

    Args:
        data: the raw 1184-byte ATTESTATION_REPORT.
        generation: ``(name, tcb_layout)``, normally from :func:`host_generation`.
            Decides the TCB_VERSION byte layout. If omitted, it is taken from
            the report's own CPUID when present (v3+); a v2 report then leaves
            ``reported_tcb`` as ``None`` rather than guessing a layout.

    Raises:
        ReportMalformed: wrong size.
        ReportUnsupportedVersion: layout not validated for that version.
        ReportUnsupportedCpu: the report's CPUID disagrees with *generation*, or
            names a processor that has not been validated against.
    """
    if len(data) != REPORT_SIZE:
        raise ReportMalformed(
            f"expected a {REPORT_SIZE}-byte ATTESTATION_REPORT, got {len(data)} bytes"
        )

    (version,) = struct.unpack_from("<I", data, _OFF_VERSION)
    # An unknown version is treated far less severely than an unknown processor,
    # because the two mistakes fail differently.
    #
    # Misidentifying the generation means misreading bytes that are present:
    # TCB_VERSION decodes to plausible but wrong values with nothing in the data
    # to reveal it. A newer report version is the opposite — fields have only
    # ever been appended, so everything read here stays where it was, and the
    # cost is missing what is new rather than misreading what is old. The
    # version is also self-describing, sitting in the first four bytes and
    # always present, which is exactly what the processor generation is not.
    #
    # So a newer report is decoded with the newest validated offsets and the
    # assumption recorded. The ``sev`` crate does the same, mapping any
    # unrecognised version onto its newest variant.
    #
    # An *older* version is still refused: below the validated range fields may
    # genuinely not exist, rather than merely going unread.
    version_note: str | None = None
    if version not in KNOWN_VERSIONS:
        if version < min(KNOWN_VERSIONS):
            raise ReportUnsupportedVersion(
                f"report declares VERSION {version}, older than any layout "
                f"validated here ({sorted(KNOWN_VERSIONS)}); fields read by "
                f"this parser may not exist in that version"
            )
        version_note = (
            f"report VERSION {version} is newer than any validated here "
            f"({sorted(KNOWN_VERSIONS)}); decoded using version "
            f"{max(KNOWN_VERSIONS)} field offsets, relying on the ABI's "
            f"append-only convention"
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

    # Resolve the generation that decides the TCB_VERSION layout.
    #
    # The host's CPUID is authoritative when supplied: it describes the silicon
    # this code is running on, which is the thing the layout actually depends
    # on. The report's copy is a cross-check, and only a useful one when it can
    # be resolved — firmware does not always populate it. Observed on SEV
    # firmware 1.55 build 38, which leaves all three bytes zero in version-3
    # reports; build 39 fills them in. Refusing such a report would reject a
    # decodable one over a field the platform declined to fill.
    cpuid_note: str | None = None
    if generation is not None and cpuid is not None:
        try:
            report_gen = resolve_generation(cpuid[0], cpuid[1])
        except ReportUnsupportedCpu:
            # Unresolvable, so it contradicts nothing. Decode with the host's
            # generation and record that the cross-check could not be made.
            cpuid_note = (
                f"report CPUID family 0x{cpuid[0]:02X} model 0x{cpuid[1]:02X} "
                f"stepping 0x{cpuid[2]:02X} does not resolve to a known "
                f"generation; decoded as {generation[0]} from the host instead"
            )
        else:
            if report_gen != generation:
                # Both resolve, and disagree: the report is not from this
                # machine, and neither layout can be trusted for it.
                #
                # Note this branch is only reachable once SUPPORTED_GENERATIONS
                # holds more than one entry. With a single validated generation
                # every disagreeing CPUID is unresolvable instead, and takes the
                # branch above. That is the conservative order: a report is only
                # called foreign when both generations are ones we have actually
                # validated against.
                raise ReportUnsupportedCpu(
                    f"report CPUID family 0x{cpuid[0]:02X} model "
                    f"0x{cpuid[1]:02X} resolves to {report_gen[0]}, but this "
                    f"host is {generation[0]}. The report does not appear to "
                    f"come from this machine."
                )
    elif generation is None and cpuid is not None:
        # No host generation to fall back on. An unresolvable CPUID then leaves
        # nothing to choose a layout with, so the TCB is left undecoded — the
        # same outcome as a v2 report, which carries no CPUID at all — rather
        # than raising for a v3 report where v2 would have been tolerated.
        try:
            generation = resolve_generation(cpuid[0], cpuid[1])
        except ReportUnsupportedCpu:
            cpuid_note = (
                f"report CPUID family 0x{cpuid[0]:02X} model 0x{cpuid[1]:02X} "
                f"stepping 0x{cpuid[2]:02X} does not resolve to a known "
                f"generation and no host generation was supplied; "
                f"TCB_VERSION left undecoded"
            )

    if generation is not None:
        gen_name, tcb_layout = generation
        reported_tcb = TcbVersion.from_bytes(
            data[_OFF_REPORTED_TCB:_OFF_REPORTED_TCB + 8], tcb_layout
        )
    else:
        # v2 report and no generation supplied — the TCB layout is unknowable,
        # so leave it undecoded rather than assume one.
        gen_name, reported_tcb = "unknown", None

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
        reported_tcb=reported_tcb,
        cpuid=cpuid,
        generation=gen_name,
        cpuid_note=cpuid_note,
        version_note=version_note,
    )


def read(
    path: Path, *, generation: tuple[str, str] | None = None
) -> AttestationReport:
    """Read and parse an ATTESTATION_REPORT file.

    Args:
        path: the report file.
        generation: forwarded to :func:`parse`; see its docstring.

    Raises:
        ReportMalformed: file missing or wrong size.
        ReportUnsupportedVersion: layout not validated for that version.
        ReportUnsupportedCpu: CPUID mismatch, or processor not validated.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise ReportMalformed(f"{path.name} not found") from exc
    return parse(data, generation=generation)
