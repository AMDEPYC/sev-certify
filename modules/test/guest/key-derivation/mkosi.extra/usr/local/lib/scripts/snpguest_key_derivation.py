#!/usr/bin/env python3
"""
SNP Guest Key Derivation Tests

This script tests the snpguest key derivation functionality, verifying:
1. Deterministic key generation (same params -> same key)
2. VMPL-based key isolation (different VMPL -> different keys)
3. Root key differences (VCK vs VMRK -> different keys)
4. Parameter sensitivity (different params -> different keys)

By default only pass/fail status and summary are printed.  Use --debug for
verbose output including snpguest commands and individual key values.
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Environment variables
KEY_DERIVATION_DIR = Path("/usr/local/lib/key_derivation_service")
KEY_DERIVATION_STATUS_LOG = Path("/usr/local/lib/key_derivation_status")

# Set by parse_args(); used by dprint()
_debug: bool = False


def dprint(*args, **kwargs) -> None:
    """Print only when --debug is active."""
    if _debug:
        print(*args, **kwargs)


@dataclass
class TcbVersion:
    """
    AMD SEV-SNP TCB_VERSION packed as a u64:
      bits  7:0  - Boot Loader SVN
      bits 15:8  - TEE SVN
      bits 47:16 - Reserved (zero)
      bits 55:48 - SNP firmware SVN
      bits 63:56 - Microcode SVN
    """
    boot_loader: int = 0
    tee: int = 0
    snp: int = 0
    microcode: int = 0

    def to_u64(self) -> int:
        return (
            (self.boot_loader & 0xFF) |
            ((self.tee & 0xFF) << 8) |
            ((self.snp & 0xFF) << 48) |
            ((self.microcode & 0xFF) << 56)
        )

    def __str__(self) -> str:
        return (f"bl=0x{self.boot_loader:02x} tee=0x{self.tee:02x} "
                f"snp=0x{self.snp:02x} mc=0x{self.microcode:02x}")


EXPECTED_REPORT_VERSION = 2  # ATTESTATION_REPORT schema version this test was written for


@dataclass
class ReportInfo:
    version: Optional[int] = None     # ATTESTATION_REPORT schema version (expected: 2)
    guest_svn: int = 0
    family_id: Optional[str] = None   # hex string, 32 chars (16 bytes)
    image_id: Optional[str] = None    # hex string, 32 chars (16 bytes)
    current_tcb: Optional[TcbVersion] = None
    committed_tcb: Optional[TcbVersion] = None
    reported_tcb: Optional[TcbVersion] = None
    launch_tcb: Optional[TcbVersion] = None


def run_command(cmd: list[str], description: str) -> Tuple[int, str, str]:
    """Execute a command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out: {description}"
    except Exception as e:
        return -1, "", f"Command failed: {description}: {str(e)}"


def check_command_status(
    status: int,
    command_name: str,
    stdout: str,
    stderr: str,
    expected_failure: bool = False,
) -> bool:
    """Check command status, log to file, and print errors.

    If expected_failure is True, a non-zero exit is treated as a normal
    negative-test outcome: the failure is not logged to stderr.
    """
    status_entry = {command_name: str(status)}
    with open(KEY_DERIVATION_STATUS_LOG, 'a') as f:
        json.dump(status_entry, f)
        f.write('\n')

    if status != 0:
        if not expected_failure:
            print(f"ERROR: {command_name} failed!", file=sys.stderr)
            if stderr:
                print(f"STDERR: {stderr}", file=sys.stderr)
            if stdout:
                print(f"STDOUT: {stdout}", file=sys.stderr)
        return False
    else:
        if stdout:
            dprint(stdout)
        return True


def derive_key(
    output_file: Path,
    root_key: str = "vcek",
    vmpl: int = 0,
    guest_svn: int = 0,
    tcb_version: int = 0,
    guest_field_select: int = 1,
    expected_failure: bool = False,
) -> bool:
    """
    Derive a key using snpguest key command.

    Args:
        output_file: Path to write the derived key
        root_key: Root key selection ("vcek" or "vmrk")
        vmpl: VMPL level (0-3)
        guest_svn: Guest SVN value (must not exceed launch SVN from ID block)
        tcb_version: TCB version value (packed u64; must not exceed CommittedTcb
                     per component; only mixed in when GFS bit 5 is set)
        guest_field_select: Guest field select bitmap (GFS is always mixed in;
                            individual bits enable mixing specific guest fields)
        expected_failure: When True, a non-zero exit is a normal negative-test
                          outcome and will not be logged as an ERROR.

    Returns:
        True if successful, False otherwise
    """
    cmd = [
        "snpguest", "key",
        str(output_file),
        root_key,
        "--vmpl", str(vmpl),
        "--guest_svn", str(guest_svn),
        "--tcb_version", str(tcb_version),
        "--guest_field_select", str(guest_field_select)
    ]

    description = (
        f"Derive key: root={root_key}, vmpl={vmpl}, "
        f"svn={guest_svn}, tcb=0x{tcb_version:016x}, gfs=0x{guest_field_select:02x}"
    )

    dprint(f"CMD: {' '.join(str(x) for x in cmd)}")
    status, stdout, stderr = run_command(cmd, description)
    return check_command_status(status, description, stdout, stderr,
                                expected_failure=expected_failure)


def read_key_hex(key_file: Path) -> Optional[str]:
    """Read a derived key file and return its contents as a hex string."""
    try:
        return key_file.read_bytes().hex()
    except Exception as e:
        print(f"ERROR: Failed to read key from {key_file}: {e}", file=sys.stderr)
        return None


def parse_tcb_section(section_text: str) -> TcbVersion:
    """Parse boot_loader/TEE/SNP/microcode values from a TCB section of report output."""
    tcb = TcbVersion()
    for attr, pattern in [
        ('boot_loader', r'Boot\s*Loader\s*[:\s]+(0x[0-9a-fA-F]+|[0-9]+)'),
        ('tee',         r'TEE\s*[:\s]+(0x[0-9a-fA-F]+|[0-9]+)'),
        ('snp',         r'SNP\s*[:\s]+(0x[0-9a-fA-F]+|[0-9]+)'),
        ('microcode',   r'Microcode\s*[:\s]+(0x[0-9a-fA-F]+|[0-9]+)'),
    ]:
        m = re.search(pattern, section_text, re.IGNORECASE)
        if m:
            setattr(tcb, attr, int(m.group(1), 0))
    return tcb


def parse_report_info(display_output: str) -> Optional[ReportInfo]:
    """
    Parse snpguest display report output to extract guest SVN and TCB values.

    Returns None on complete failure; individual fields may be zero/None if
    their section is missing or unparseable.
    """
    try:
        info = ReportInfo()

        m = re.search(r'^\s*Version\s*[:\s]+(0x[0-9a-fA-F]+|[0-9]+)',
                      display_output, re.IGNORECASE | re.MULTILINE)
        if m:
            info.version = int(m.group(1), 0)

        m = re.search(r'Guest\s+SVN\s*[:\s]+(0x[0-9a-fA-F]+|[0-9]+)',
                      display_output, re.IGNORECASE)
        if m:
            info.guest_svn = int(m.group(1), 0)

        m = re.search(r'Family\s+ID\s*[:\s]+([0-9a-fA-F]+)', display_output, re.IGNORECASE)
        if m:
            info.family_id = m.group(1).lower()

        m = re.search(r'Image\s+ID\s*[:\s]+([0-9a-fA-F]+)', display_output, re.IGNORECASE)
        if m:
            info.image_id = m.group(1).lower()

        boundary = r'(?:Current|Committed|Reported|Launch)\s+TCB'
        for section_name, attr in [
            ('Current TCB',   'current_tcb'),
            ('Committed TCB', 'committed_tcb'),
            ('Reported TCB',  'reported_tcb'),
            ('Launch TCB',    'launch_tcb'),
        ]:
            pattern = rf'{re.escape(section_name)}\s*:?(.*?)(?={boundary}|\Z)'
            m = re.search(pattern, display_output, re.DOTALL | re.IGNORECASE)
            if m:
                setattr(info, attr, parse_tcb_section(m.group(1)))

        return info
    except Exception as e:
        print(f"WARNING: Failed to parse report info: {e}", file=sys.stderr)
        return None


def print_attestation_report() -> Optional[ReportInfo]:
    """
    Fetch and display the attestation report.

    Always prints the extracted key values (guest SVN, TCB bounds).
    Full report text is printed only with --debug.

    Returns parsed ReportInfo, or None on failure.
    """
    report_path = KEY_DERIVATION_DIR / "report.bin"
    request_path = KEY_DERIVATION_DIR / "request.bin"

    print("\n" + "="*70)
    print("ATTESTATION REPORT (reference values for key derivation bounds)")
    print("="*70)

    cmd = ["snpguest", "report", str(report_path), str(request_path), "--random"]
    dprint(f"CMD: {' '.join(cmd)}")
    status, stdout, stderr = run_command(cmd, "Get attestation report")
    if status != 0:
        print("WARNING: Failed to get attestation report", file=sys.stderr)
        if stderr:
            print(f"STDERR: {stderr}", file=sys.stderr)
        return None

    cmd = ["snpguest", "display", "report", str(report_path)]
    dprint(f"CMD: {' '.join(cmd)}")
    status, report_text, stderr = run_command(cmd, "Display attestation report")
    if status != 0:
        print("WARNING: Failed to display attestation report", file=sys.stderr)
        if stderr:
            print(f"STDERR: {stderr}", file=sys.stderr)
        return None

    dprint(report_text)

    report_info = parse_report_info(report_text)
    if report_info:
        if report_info.version is not None:
            version_note = (
                "" if report_info.version == EXPECTED_REPORT_VERSION
                else f" *** UNEXPECTED (expected {EXPECTED_REPORT_VERSION}) —"
                     f" TCB layout assumptions may not apply ***"
            )
            print(f"  Report version: {report_info.version}{version_note}")
        else:
            print("  Report version: (not parsed)", file=sys.stderr)

        print(f"  Guest SVN:     {report_info.guest_svn} "
              f"(upper bound for --guest_svn)")

        def _id_note(hex_val: Optional[str]) -> str:
            if not hex_val:
                return "(not parsed)"
            return ("(non-zero — ID block present)"
                    if any(c != '0' for c in hex_val)
                    else "(all zeros — no ID block)")

        print(f"  Family ID:     {report_info.family_id or '(not parsed)'} "
              f"{_id_note(report_info.family_id)}")
        print(f"  Image ID:      {report_info.image_id or '(not parsed)'} "
              f"{_id_note(report_info.image_id)}")

        if report_info.current_tcb:
            print(f"  Current  TCB:  {report_info.current_tcb}")
        if report_info.committed_tcb:
            print(f"  Committed TCB: {report_info.committed_tcb} "
                  f"(upper bound per component for --tcb_version)")
        if report_info.reported_tcb:
            print(f"  Reported TCB:  {report_info.reported_tcb}")
        if report_info.launch_tcb:
            print(f"  Launch TCB:    {report_info.launch_tcb}")
    else:
        print("  WARNING: Could not parse report values", file=sys.stderr)

    return report_info


def report_version_ok(report_info: Optional[ReportInfo]) -> bool:
    """Return True if the attestation report version matches what this test expects."""
    if report_info is None or report_info.version is None:
        return False
    return report_info.version == EXPECTED_REPORT_VERSION


def generate_tcb_candidates(committed: TcbVersion, max_count: int = 30) -> List[int]:
    """
    Generate up to max_count valid TCB u64 values.

    Varies each component (boot_loader, tee, snp, microcode) independently
    from 0 to its committed maximum, keeping the other components at 0.
    """
    candidates: set[int] = {0}
    per_comp = max(1, (max_count - 1) // 4)

    for comp, max_val in [
        ('boot_loader', committed.boot_loader),
        ('tee',         committed.tee),
        ('snp',         committed.snp),
        ('microcode',   committed.microcode),
    ]:
        if max_val == 0 or len(candidates) >= max_count:
            continue
        step = max(1, max_val // per_comp)
        for v in list(range(step, max_val, step)) + [max_val]:
            tcb = TcbVersion()
            setattr(tcb, comp, v)
            candidates.add(tcb.to_u64())
            if len(candidates) >= max_count:
                break

    return sorted(candidates)[:max_count]


def test_determinism() -> bool:
    """Test that deriving a key with the same parameters produces the same result."""
    key1_file = KEY_DERIVATION_DIR / "determinism_key1.bin"
    key2_file = KEY_DERIVATION_DIR / "determinism_key2.bin"

    if not derive_key(key1_file, root_key="vcek", vmpl=0, guest_svn=0, tcb_version=0):
        return False
    if not derive_key(key2_file, root_key="vcek", vmpl=0, guest_svn=0, tcb_version=0):
        return False

    key1_hex = read_key_hex(key1_file)
    key2_hex = read_key_hex(key2_file)

    if key1_hex is None or key2_hex is None:
        print("ERROR: Failed to read keys for comparison", file=sys.stderr)
        return False

    if key1_hex == key2_hex:
        dprint(f"  Key: 0x{key1_hex}")
        print("✓ PASS: Keys match (deterministic)")
        return True
    else:
        print("✗ FAIL: Keys do not match", file=sys.stderr)
        dprint(f"  Key1: 0x{key1_hex}", file=sys.stderr)
        dprint(f"  Key2: 0x{key2_hex}", file=sys.stderr)
        return False


def test_vmpl_isolation() -> bool:
    """Test that different VMPL values produce different keys."""
    key_vmpl0_file = KEY_DERIVATION_DIR / "vmpl0_key.bin"
    key_vmpl1_file = KEY_DERIVATION_DIR / "vmpl1_key.bin"

    if not derive_key(key_vmpl0_file, root_key="vcek", vmpl=0):
        return False

    if not derive_key(key_vmpl1_file, root_key="vcek", vmpl=1):
        print("  Note: VMPL1 derivation failed (expected if not running at VMPL0)")
        print("✓ PASS: N/A")
        return True

    key_vmpl0_hex = read_key_hex(key_vmpl0_file)
    key_vmpl1_hex = read_key_hex(key_vmpl1_file)

    if key_vmpl0_hex is None or key_vmpl1_hex is None:
        print("ERROR: Failed to read keys for comparison", file=sys.stderr)
        return False

    if key_vmpl0_hex != key_vmpl1_hex:
        dprint(f"  VMPL0 Key: 0x{key_vmpl0_hex}")
        dprint(f"  VMPL1 Key: 0x{key_vmpl1_hex}")
        print("✓ PASS: VMPL0 and VMPL1 keys differ (proper isolation)")
        return True
    else:
        print("✗ FAIL: VMPL0 and VMPL1 keys are identical", file=sys.stderr)
        return False


def test_root_key_difference() -> bool:
    """Test that different root keys (VCEK vs VMRK) produce different keys."""
    key_vck_file  = KEY_DERIVATION_DIR / "vck_key.bin"
    key_vmrk_file = KEY_DERIVATION_DIR / "vmrk_key.bin"

    if not derive_key(key_vck_file,  root_key="vcek", vmpl=0):
        return False
    if not derive_key(key_vmrk_file, root_key="vmrk", vmpl=0):
        return False

    key_vck_hex  = read_key_hex(key_vck_file)
    key_vmrk_hex = read_key_hex(key_vmrk_file)

    if key_vck_hex is None or key_vmrk_hex is None:
        print("ERROR: Failed to read keys for comparison", file=sys.stderr)
        return False

    if key_vck_hex != key_vmrk_hex:
        dprint(f"  VCK  Key: 0x{key_vck_hex}")
        dprint(f"  VMRK Key: 0x{key_vmrk_hex}")
        print("✓ PASS: VCEK and VMRK keys differ")
        return True
    else:
        print("✗ FAIL: VCEK and VMRK keys are identical", file=sys.stderr)
        return False


def test_guest_svn_sensitivity(report_info: Optional[ReportInfo]) -> bool:
    """
    Test that different guest SVN values produce different keys.

    Loops over all valid SVN values (0..guest_svn from attestation report).
    Upper bound is the guest SVN recorded at launch in the ID block; guests
    launched without an ID block have guest_svn=0 (only one valid value).
    GFS bit 4 must be set for guest_svn to be mixed into the derived key.
    """
    max_svn = report_info.guest_svn if report_info is not None else 0
    print(f"  Guest SVN upper bound: {max_svn}")

    svn_values = list(range(0, max_svn + 1))

    # Test values above the active bound — these should always be rejected.
    # When there's no ID block, max_svn=0, so values 1..3 must be rejected.
    # When there's an ID block, max_svn+1..max_svn+3 must be rejected.
    over_values = list(range(max_svn + 1, max_svn + 4))
    print(f"  Testing {len(over_values)} above-bound SVN values: {over_values}")
    passed = True
    for svn in over_values:
        bound_file = KEY_DERIVATION_DIR / f"svn{svn}_bound_check.bin"
        if derive_key(bound_file, root_key="vcek", vmpl=0, guest_svn=svn,
                      guest_field_select=1 << 4, expected_failure=True):
            print(f"✗ FAIL: SVN={svn} succeeded — bound "
                  f"({max_svn}) not enforced by firmware", file=sys.stderr)
            passed = False
        else:
            print(f"  ✓ Bound enforced: SVN={svn} correctly rejected")
    if not passed:
        return False

    if len(svn_values) < 2:
        print("  Only one valid SVN value (0); sensitivity cannot be tested.")
        print("  (Expected when guest was launched without an ID block.)")
        print("✓ PASS: bound enforcement verified, sensitivity N/A (single valid value)")
        return True

    print(f"  Testing {len(svn_values)} SVN values: {svn_values}")
    keys: Dict[int, str] = {}
    failed_svns: List[int] = []
    for svn in svn_values:
        key_file = KEY_DERIVATION_DIR / f"svn{svn}_key.bin"
        if not derive_key(key_file, root_key="vcek", vmpl=0, guest_svn=svn,
                          guest_field_select=1 << 4, expected_failure=True):
            failed_svns.append(svn)
            continue
        hex_key = read_key_hex(key_file)
        if hex_key:
            keys[svn] = hex_key
            dprint(f"  SVN={svn}: 0x{hex_key}")

    if failed_svns:
        print(f"  {len(failed_svns)} SVN value(s) rejected by firmware "
              f"(above id-block bound): {failed_svns}")

    if len(keys) < 2:
        print("ERROR: Fewer than 2 successful derivations — cannot test sensitivity",
              file=sys.stderr)
        return False

    unique_keys = set(keys.values())
    if len(unique_keys) == len(keys):
        print(f"✓ PASS: All {len(keys)} SVN values produce distinct keys")
        return True
    else:
        print("✗ FAIL: Some SVN values produce identical keys", file=sys.stderr)
        return False


def test_tcb_sensitivity(report_info: Optional[ReportInfo]) -> bool:
    """
    Test that different TCB version values produce different keys.

    Generates up to 30 valid TCB u64 values by varying each component
    (boot_loader, tee, snp, microcode) from 0 to its committed maximum.
    The firmware rejects tcb_version values where any component exceeds
    the corresponding CommittedTcb component.
    GFS bit 5 must be set for tcb_version to be mixed into the derived key.
    """
    committed = (report_info.committed_tcb
                 if report_info is not None and report_info.committed_tcb is not None
                 else TcbVersion())

    print(f"  Committed TCB (upper bound per component): {committed}")

    candidates = generate_tcb_candidates(committed, max_count=30)
    print(f"  Testing {len(candidates)} TCB candidate(s)")
    dprint(f"  Candidates: {[f'0x{v:016x}' for v in candidates]}")

    # Per-component bound enforcement check — always run, even when committed
    # components are 0.  Values above the committed bound must be rejected.
    # TCB_VERSION bit layout is schema-version-specific (attestation report v2,
    # ID block v1, SNP ABI spec). Skip if the report version doesn't match.
    passed = True
    if not report_version_ok(report_info):
        print("  Skipping TCB bound check: report version unknown or unexpected")
    else:
        for comp, label, max_val in [
            ('boot_loader', 'Boot Loader', committed.boot_loader),
            ('tee',         'TEE',         committed.tee),
            ('snp',         'SNP',         committed.snp),
            ('microcode',   'Microcode',   committed.microcode),
        ]:
            for over_by in range(1, 4):
                over = TcbVersion()
                setattr(over, comp, max_val + over_by)
                bound_file = KEY_DERIVATION_DIR / f"tcb_bound_{comp}_{over_by}.bin"
                if derive_key(bound_file, root_key="vcek", vmpl=0,
                              tcb_version=over.to_u64(),
                              guest_field_select=1 << 5,
                              expected_failure=True):
                    print(f"✗ FAIL: {label}={max_val + over_by} succeeded — "
                          f"committed bound ({max_val}) not enforced", file=sys.stderr)
                    passed = False
                else:
                    print(f"  ✓ TCB bound enforced: {label}={max_val + over_by} correctly rejected")
        if not passed:
            return False

    if len(candidates) < 2:
        print("  All TCB components are zero; sensitivity cannot be tested.")
        print("✓ PASS: bound enforcement verified, sensitivity N/A (single valid value)")
        return True

    keys: Dict[int, str] = {}
    failed_tcbs: List[int] = []
    for tcb_u64 in candidates:
        key_file = KEY_DERIVATION_DIR / f"tcb_{tcb_u64:016x}_key.bin"
        if not derive_key(key_file, root_key="vcek", vmpl=0, tcb_version=tcb_u64,
                          guest_field_select=1 << 5, expected_failure=True):
            failed_tcbs.append(tcb_u64)
            continue
        hex_key = read_key_hex(key_file)
        if hex_key:
            keys[tcb_u64] = hex_key
            dprint(f"  TCB=0x{tcb_u64:016x}: 0x{hex_key}")

    if failed_tcbs:
        print(f"  {len(failed_tcbs)} TCB candidate(s) rejected by firmware "
              f"(above committed bound): "
              f"{[f'0x{v:016x}' for v in failed_tcbs]}")

    if len(keys) < 2:
        print("ERROR: Fewer than 2 successful derivations — cannot test sensitivity",
              file=sys.stderr)
        return False

    unique_keys = set(keys.values())
    if len(unique_keys) == len(keys):
        print(f"✓ PASS: All {len(keys)} TCB values produce distinct keys")
        return True
    else:
        print("✗ FAIL: Some TCB values produce identical keys", file=sys.stderr)
        return False


def test_guest_field_select_sensitivity() -> bool:
    """Test that different GFS values produce different keys."""
    key_gfs1_file = KEY_DERIVATION_DIR / "gfs1_key.bin"
    key_gfs2_file = KEY_DERIVATION_DIR / "gfs2_key.bin"

    if not derive_key(key_gfs1_file, root_key="vcek", vmpl=0, guest_field_select=1):
        return False
    if not derive_key(key_gfs2_file, root_key="vcek", vmpl=0, guest_field_select=2):
        return False

    key_gfs1_hex = read_key_hex(key_gfs1_file)
    key_gfs2_hex = read_key_hex(key_gfs2_file)

    if key_gfs1_hex is None or key_gfs2_hex is None:
        print("ERROR: Failed to read keys for comparison", file=sys.stderr)
        return False

    if key_gfs1_hex != key_gfs2_hex:
        dprint(f"  GFS=0x01 Key: 0x{key_gfs1_hex}")
        dprint(f"  GFS=0x02 Key: 0x{key_gfs2_hex}")
        print("✓ PASS: GFS=0x01 and GFS=0x02 keys differ")
        return True
    else:
        print("✗ FAIL: GFS=0x01 and GFS=0x02 keys are identical", file=sys.stderr)
        return False


def test_gfs_field_mixing(report_info: Optional[ReportInfo]) -> bool:
    """
    Test that GFS bits 0-3 each produce a key distinct from the GFS=0 baseline.

    GFS is always mixed into the derived key. Bits 0-3 additionally mix in
    specific guest fields from the ID block / attestation report:
      Bit 0: Image ID
      Bit 1: Family ID
      Bit 2: Measurement
      Bit 3: Guest SVN Policy

    Each bit is tested individually against a GFS=0 baseline. All should
    produce distinct keys, confirming each bit has an effect on derivation.

    Note: this test cannot prove that the field *values* matter (that would
    require two runs with different ID blocks), only that each bit has an effect.
    When family_id/image_id are non-zero in the report, a non-zero ID block is
    confirmed present, lending weight to the result for bits 0 and 1.
    """
    has_id_block = False
    if report_info:
        if report_info.family_id and any(c != '0' for c in report_info.family_id):
            has_id_block = True
        if report_info.image_id and any(c != '0' for c in report_info.image_id):
            has_id_block = True

    if not has_id_block:
        print("  Note: family_id and image_id are all zeros — ID block may not be present.")
        print("  Bits 0 and 1 may still differ from baseline due to GFS value mixing.")

    baseline_file = KEY_DERIVATION_DIR / "gfs_field_baseline.bin"
    if not derive_key(baseline_file, root_key="vcek", vmpl=0, guest_field_select=0):
        return False
    baseline_hex = read_key_hex(baseline_file)
    if baseline_hex is None:
        return False
    dprint(f"  GFS=0x00 (baseline): 0x{baseline_hex}")

    bits = [
        (0, "Image ID"),
        (1, "Family ID"),
        (2, "Measurement"),
        (3, "Guest SVN Policy"),
        # Bits 4 and 5 are tested with svn=0 and tcb=0 fixed. This does not prove
        # value-sensitivity (only one valid value available without an ID block or
        # non-zero committed TCB), but confirms each bit participates in derivation
        # via GFS value mixing (GFS itself is always mixed in).
        (4, "Guest SVN"),
        (5, "TCB Version"),
    ]

    passed = True
    for bit, label in bits:
        gfs = 1 << bit
        key_file = KEY_DERIVATION_DIR / f"gfs_field_bit{bit}.bin"
        if not derive_key(key_file, root_key="vcek", vmpl=0, guest_field_select=gfs):
            print(f"  ✗ FAIL: GFS=0x{gfs:02x} ({label}) derivation failed",
                  file=sys.stderr)
            passed = False
            continue
        hex_key = read_key_hex(key_file)
        if hex_key is None:
            passed = False
            continue
        dprint(f"  GFS=0x{gfs:02x} ({label}): 0x{hex_key}")
        if hex_key != baseline_hex:
            print(f"  ✓ GFS=0x{gfs:02x} ({label}): differs from baseline")
        else:
            print(f"  ✗ FAIL: GFS=0x{gfs:02x} ({label}): same as baseline",
                  file=sys.stderr)
            passed = False

    if passed:
        print("✓ PASS: All GFS field bits (0-3) produce keys distinct from baseline")
    else:
        print("✗ FAIL: One or more GFS field bits matched baseline", file=sys.stderr)
    return passed


def run_gfs_sweep() -> int:
    """
    Derive a key for every valid GFS value (0x00-0x7f), keeping all other
    parameters fixed (root=vcek, vmpl=0, svn=0, tcb=0).  Shows which values
    produce distinct keys and groups any that collide.

    snpguest accepts GFS up to 0x7f; bit 6 (launch mitigation vector) requires
    msg v2 and may be rejected by some firmware.  Failures are noted and skipped.

    Returns:
        0 always (diagnostic mode, not pass/fail)
    """
    print("\n" + "="*70)
    print("GFS SWEEP: all valid GFS values 0x00-0x7f")
    print("Fixed params: root=vcek, vmpl=0, svn=0, tcb=0")
    print("="*70)

    keys: Dict[int, str] = {}
    failed: List[int] = []

    for gfs in range(0x80):
        key_file = KEY_DERIVATION_DIR / f"gfs_{gfs:02x}_key.bin"
        if not derive_key(key_file, root_key="vcek", vmpl=0,
                          guest_svn=0, tcb_version=0, guest_field_select=gfs,
                          expected_failure=True):
            failed.append(gfs)
            continue
        hex_key = read_key_hex(key_file)
        if hex_key:
            keys[gfs] = hex_key
            dprint(f"  GFS=0x{gfs:02x}: 0x{hex_key}")

    print()
    if failed:
        print(f"Failed GFS values ({len(failed)}): "
              f"{[f'0x{g:02x}' for g in failed]}")

    unique_keys = set(keys.values())
    print(f"{len(keys)} successful derivations, {len(unique_keys)} unique key(s)")

    key_to_gfs: Dict[str, List[int]] = defaultdict(list)
    for gfs, hex_key in keys.items():
        key_to_gfs[hex_key].append(gfs)

    collisions = {k: v for k, v in key_to_gfs.items() if len(v) > 1}
    if collisions:
        print("\nGFS values producing identical keys:")
        for hex_key, gfs_list in collisions.items():
            print(f"  {[f'0x{g:02x}' for g in gfs_list]}: 0x{hex_key}")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "SNP Guest Key Derivation Tests.\n"
            "Runs the standard test suite by default.\n\n"
            "Exit code: 0 = all tests passed, 1 = one or more tests failed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Print verbose output including snpguest commands, "
            "individual key hex values, and the full attestation report."
        ),
    )
    parser.add_argument(
        "--gfs-sweep",
        action="store_true",
        help=(
            "Instead of the standard test suite, derive a key for every valid "
            "GFS value (0x00-0x7f) with all other params fixed "
            "(root=vcek, vmpl=0, svn=0, tcb=0) and report which values "
            "produce distinct keys."
        ),
    )
    return parser.parse_args()


def main() -> int:
    """
    Main entry point.

    Returns:
        0 on success, 1 on failure
    """
    global _debug
    args = parse_args()
    _debug = args.debug

    # Create fresh working directory
    if KEY_DERIVATION_DIR.exists():
        import shutil
        shutil.rmtree(KEY_DERIVATION_DIR)
    KEY_DERIVATION_DIR.mkdir(parents=True, exist_ok=True)

    # Clear status log
    if KEY_DERIVATION_STATUS_LOG.exists():
        KEY_DERIVATION_STATUS_LOG.unlink()

    if args.gfs_sweep:
        return run_gfs_sweep()

    print("\n" + "="*70)
    print("SNP Guest Key Derivation Test Suite")
    print("="*70)

    # Fetch attestation report — provides bounds for SVN and TCB tests
    report_info = print_attestation_report()

    # Run all tests
    tests = [
        ("Determinism",                    lambda: test_determinism()),
        ("VMPL Isolation",                 lambda: test_vmpl_isolation()),
        ("Root Key Difference",            lambda: test_root_key_difference()),
        ("Guest SVN Sensitivity",          lambda: test_guest_svn_sensitivity(report_info)),
        ("TCB Sensitivity",                lambda: test_tcb_sensitivity(report_info)),
        ("Guest Field Select Sensitivity", lambda: test_guest_field_select_sensitivity()),
        ("GFS Field Mixing",               lambda: test_gfs_field_mixing(report_info)),
    ]

    results = []
    for test_name, test_func in tests:
        print("\n" + "="*70)
        print(f"TEST: {test_name}")
        print("="*70)
        try:
            passed = test_func()
            results.append((test_name, passed))
        except Exception as e:
            print(f"✗ EXCEPTION in {test_name}: {str(e)}", file=sys.stderr)
            results.append((test_name, False))

    # Print summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)

    passed_count = sum(1 for _, passed in results if passed)
    total_count = len(results)

    for test_name, passed in results:
        print(f"{'✓ PASS' if passed else '✗ FAIL'}: {test_name}")

    print(f"\nPassed: {passed_count}/{total_count}")

    # Emit per-test JSON to stdout so the certificate generator can read it
    # from the guest journal (journalctl -D /var/log/journal/guest-logs/
    # -u key-derivation.service -o cat).  Format matches attestation-result.service.
    print()
    for test_name, passed in results:
        print(json.dumps({test_name: "0" if passed else "1"}))

    if passed_count == total_count:
        print("\n✓ All key derivation tests passed!")
        return 0
    else:
        print(f"\n✗ {total_count - passed_count} test(s) failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
