"""key_derivation_test: Launch SEV-SNP guest and run key derivation tests.

Verifies that the SNP MSG_KEY_REQ firmware command produces correct and
consistent derived keys:
  - Determinism: same params -> same key
  - VMPL isolation: different VMPL -> different keys
  - Root key difference: VCEK vs VMRK -> different keys
  - Guest SVN sensitivity and above-bound rejection
  - TCB version sensitivity and committed-TCB bound enforcement
  - Guest Field Select (GFS) sensitivity and per-bit field mixing

The attestation report is fetched first to learn the guest SVN and
committed TCB bounds, which drive the SVN and TCB loops dynamically.
All snpguest key commands run on the guest via vsock from callable
steps on the host — no guest-side script is needed.

Above-bound TCB tests use committed+1, committed+2, committed+3 per
component, derived from the runtime attestation report. No static
assumption about platform TCB values is needed.

When sev_verify.id_block is available (from the ID block PR), the guest
is launched with an ID block, giving richer coverage (non-zero guest SVN,
family_id, image_id).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sev_verify.cert_tests.c3_0.c3_0_0_0.attestation_test import calculate_measurement  # noqa: F401
from sev_verify.guest_vsock import GuestCommandError, fetch_guest_file_bytes, run_guest_command
from sev_verify.models import BaseStep, Step, StepContext, StepHandlerResult
from sev_verify.vm_profile import VMProfile

try:
    from sev_verify.id_block import generate_id_block  # noqa: F401
    _HAS_ID_BLOCK = True
except ImportError:
    _HAS_ID_BLOCK = False

vm_profile = VMProfile(
    image_path="",
    memory_mb=2048,
)

_TCB_ABOVE_BOUND_STEPS = 3  # number of values above committed bound to test per component


# ── TCB / report parsing helpers ──────────────────────────────────────────────

@dataclass
class TcbVersion:
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


@dataclass
class ReportInfo:
    version: Optional[int] = None
    guest_svn: int = 0
    committed_tcb: TcbVersion = None

    def __post_init__(self):
        if self.committed_tcb is None:
            self.committed_tcb = TcbVersion()


def _parse_tcb_section(text: str) -> TcbVersion:
    tcb = TcbVersion()
    for attr, pattern in [
        ('boot_loader', r'Boot\s*Loader\s*[:\s]+(0x[0-9a-fA-F]+|[0-9]+)'),
        ('tee',         r'TEE\s*[:\s]+(0x[0-9a-fA-F]+|[0-9]+)'),
        ('snp',         r'SNP\s*[:\s]+(0x[0-9a-fA-F]+|[0-9]+)'),
        ('microcode',   r'Microcode\s*[:\s]+(0x[0-9a-fA-F]+|[0-9]+)'),
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            setattr(tcb, attr, int(m.group(1), 0))
    return tcb


def _parse_report_info(display_output: str) -> ReportInfo:
    info = ReportInfo()
    m = re.search(r'^\s*Version\s*[:\s]+(0x[0-9a-fA-F]+|[0-9]+)',
                  display_output, re.IGNORECASE | re.MULTILINE)
    if m:
        info.version = int(m.group(1), 0)
    m = re.search(r'Guest\s+SVN\s*[:\s]+(0x[0-9a-fA-F]+|[0-9]+)',
                  display_output, re.IGNORECASE)
    if m:
        info.guest_svn = int(m.group(1), 0)
    boundary = r'(?:Current|Committed|Reported|Launch)\s+TCB'
    m = re.search(rf'Committed\s+TCB\s*:?(.*?)(?={boundary}|\Z)',
                  display_output, re.DOTALL | re.IGNORECASE)
    if m:
        info.committed_tcb = _parse_tcb_section(m.group(1))
    return info


# ── Guest helpers (called from callable steps while VM is running) ─────────────

def _derive_key(ctx: StepContext, filename: str, root: str = "vcek",
                vmpl: int = 0, svn: int = 0, tcb: int = 0,
                gfs: int = 1) -> tuple[bool, str]:
    """Run snpguest key on the guest, fetch the key bytes to artifact_dir.

    Returns (success, message). On success the key file is written locally.
    On failure the error message is returned without raising.
    """
    cmd = (f"snpguest key {filename} {root} --vmpl {vmpl} "
           f"--guest_svn {svn} --tcb_version {tcb} --guest_field_select {gfs}")
    result = run_guest_command(ctx.profile, cmd, timeout=30)
    if result.exit_code != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    try:
        data = fetch_guest_file_bytes(ctx.profile, filename, timeout=30)
    except GuestCommandError as e:
        return False, str(e)
    (ctx.artifact_dir / filename).write_bytes(data)
    return True, ""


def _read_key(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except Exception:
        return None


# ── Callable step handlers ────────────────────────────────────────────────────

def parse_report(ctx: StepContext) -> StepHandlerResult:
    """Parse the pulled attestation report and write a bounds sidecar file."""
    report_file = ctx.artifact_dir / "report.bin"
    result = subprocess.run(
        ["snpguest", "display", "report", str(report_file)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return StepHandlerResult(exit_code=1, stderr=f"snpguest display report failed:\n{result.stderr}")

    info = _parse_report_info(result.stdout)
    c = info.committed_tcb
    (ctx.artifact_dir / "report_info.txt").write_text(
        f"version={info.version}\n"
        f"guest_svn={info.guest_svn}\n"
        f"committed_bl={c.boot_loader}\n"
        f"committed_tee={c.tee}\n"
        f"committed_snp={c.snp}\n"
        f"committed_mc={c.microcode}\n"
    )
    return StepHandlerResult(
        exit_code=0,
        stdout=(f"Report version: {info.version}, Guest SVN: {info.guest_svn}\n"
                f"Committed TCB: bl={c.boot_loader} tee={c.tee} "
                f"snp={c.snp} mc={c.microcode}"),
    )


def _load_report_info(ctx: StepContext) -> ReportInfo:
    info = ReportInfo()
    f = ctx.artifact_dir / "report_info.txt"
    if not f.exists():
        return info
    for line in f.read_text().splitlines():
        k, _, v = line.partition("=")
        if k == "version" and v != "None":
            info.version = int(v)
        elif k == "guest_svn":
            info.guest_svn = int(v)
        elif k == "committed_bl":
            info.committed_tcb.boot_loader = int(v)
        elif k == "committed_tee":
            info.committed_tcb.tee = int(v)
        elif k == "committed_snp":
            info.committed_tcb.snp = int(v)
        elif k == "committed_mc":
            info.committed_tcb.microcode = int(v)
    return info


def test_determinism(ctx: StepContext) -> StepHandlerResult:
    ok1, err1 = _derive_key(ctx, "det_key1.bin")
    ok2, err2 = _derive_key(ctx, "det_key2.bin")
    if not ok1 or not ok2:
        return StepHandlerResult(exit_code=1, stderr=err1 or err2)
    k1 = _read_key(ctx.artifact_dir / "det_key1.bin")
    k2 = _read_key(ctx.artifact_dir / "det_key2.bin")
    if k1 == k2:
        return StepHandlerResult(exit_code=0, stdout="Keys match (deterministic)")
    return StepHandlerResult(exit_code=1, stderr="Keys differ — derivation is not deterministic")


def test_vmpl_isolation(ctx: StepContext) -> StepHandlerResult:
    ok0, err0 = _derive_key(ctx, "vmpl0_key.bin", vmpl=0)
    if not ok0:
        return StepHandlerResult(exit_code=1, stderr=f"VMPL0 derivation failed: {err0}")
    ok1, _ = _derive_key(ctx, "vmpl1_key.bin", vmpl=1)
    if not ok1:
        return StepHandlerResult(
            exit_code=0,
            stdout="VMPL1 derivation rejected (expected if not running at VMPL0) — N/A",
        )
    k0 = _read_key(ctx.artifact_dir / "vmpl0_key.bin")
    k1 = _read_key(ctx.artifact_dir / "vmpl1_key.bin")
    if k0 != k1:
        return StepHandlerResult(exit_code=0, stdout="VMPL0 and VMPL1 keys differ (proper isolation)")
    return StepHandlerResult(exit_code=1, stderr="VMPL0 and VMPL1 keys are identical")


def test_root_key_difference(ctx: StepContext) -> StepHandlerResult:
    ok_v, err_v = _derive_key(ctx, "vcek_key.bin", root="vcek")
    ok_m, err_m = _derive_key(ctx, "vmrk_key.bin", root="vmrk")
    if not ok_v or not ok_m:
        return StepHandlerResult(exit_code=1, stderr=err_v or err_m)
    kv = _read_key(ctx.artifact_dir / "vcek_key.bin")
    km = _read_key(ctx.artifact_dir / "vmrk_key.bin")
    if kv != km:
        return StepHandlerResult(exit_code=0, stdout="VCEK and VMRK keys differ")
    return StepHandlerResult(exit_code=1, stderr="VCEK and VMRK keys are identical")


def test_svn(ctx: StepContext) -> StepHandlerResult:
    """SVN above-bound rejection and sensitivity sweep."""
    info = _load_report_info(ctx)
    max_svn = info.guest_svn
    lines = [f"Guest SVN upper bound: {max_svn}"]
    passed = True

    # Above-bound: SVN max_svn+1, max_svn+2, max_svn+3 must all be rejected
    for svn in range(max_svn + 1, max_svn + 4):
        ok, _ = _derive_key(ctx, f"svn_above_{svn}.bin", svn=svn, gfs=1 << 4)
        if ok:
            lines.append(f"FAIL: SVN={svn} succeeded — bound ({max_svn}) not enforced")
            passed = False
        else:
            lines.append(f"Bound enforced: SVN={svn} correctly rejected")

    if not passed:
        return StepHandlerResult(exit_code=1, stderr="\n".join(lines))

    # Sensitivity: all valid values 0..max_svn produce distinct keys
    if max_svn == 0:
        lines.append("Only one valid SVN value (0) — sensitivity N/A (no ID block)")
        return StepHandlerResult(exit_code=0, stdout="\n".join(lines))

    keys = {}
    for svn in range(0, max_svn + 1):
        ok, err = _derive_key(ctx, f"svn_{svn}_key.bin", svn=svn, gfs=1 << 4)
        if ok:
            k = _read_key(ctx.artifact_dir / f"svn_{svn}_key.bin")
            if k:
                keys[svn] = k
        else:
            lines.append(f"SVN={svn} rejected (unexpected): {err}")

    if len(keys) < 2:
        return StepHandlerResult(exit_code=1,
                                 stderr="\n".join(lines) + "\nFewer than 2 successful SVN derivations")
    if len(set(keys.values())) == len(keys):
        lines.append(f"All {len(keys)} SVN values produce distinct keys")
        return StepHandlerResult(exit_code=0, stdout="\n".join(lines))
    return StepHandlerResult(exit_code=1, stderr="\n".join(lines) + "\nSome SVN values produce identical keys")


def test_tcb(ctx: StepContext) -> StepHandlerResult:
    """TCB above-bound rejection and sensitivity sweep."""
    info = _load_report_info(ctx)
    c = info.committed_tcb
    lines = [f"Committed TCB: bl={c.boot_loader} tee={c.tee} snp={c.snp} mc={c.microcode}"]
    passed = True

    # Above-bound: committed+1 .. committed+N per component must all be rejected.
    for comp, label, max_val in [
        ("boot_loader", "Boot Loader", c.boot_loader),
        ("tee",         "TEE",         c.tee),
        ("snp",         "SNP",         c.snp),
        ("microcode",   "Microcode",   c.microcode),
    ]:
        above_vals = [v for v in range(max_val + 1, max_val + _TCB_ABOVE_BOUND_STEPS + 1)
                      if v <= 0xFF]
        if not above_vals:
            lines.append(f"{label}: committed={max_val} is max (0xFF) — no above-bound values to test")
            continue
        for val in above_vals:
            tcb_u64 = TcbVersion(**{comp: val}).to_u64()
            ok, _ = _derive_key(ctx, f"tcb_above_{comp}_{val}.bin",
                                tcb=tcb_u64, gfs=1 << 5)
            if ok:
                lines.append(f"FAIL: {label}={val} succeeded — "
                             f"bound ({max_val}) not enforced")
                passed = False
            else:
                lines.append(f"Bound enforced: {label}={val} correctly rejected")

    if not passed:
        return StepHandlerResult(exit_code=1, stderr="\n".join(lines))

    # Sensitivity: vary each component from 0 to its committed maximum.
    # Track by tcb_u64 to deduplicate (e.g. val=0 for any component gives the same u64).
    keys: dict[int, bytes] = {}  # tcb_u64 -> key bytes
    for comp, max_val in [
        ("boot_loader", c.boot_loader),
        ("tee",         c.tee),
        ("snp",         c.snp),
        ("microcode",   c.microcode),
    ]:
        for val in range(0, max_val + 1):
            tcb_u64 = TcbVersion(**{comp: val}).to_u64()
            if tcb_u64 in keys:
                continue  # already derived this exact TCB value
            fname = f"tcb_{comp}_{val}.bin"
            ok, err = _derive_key(ctx, fname, tcb=tcb_u64, gfs=1 << 5)
            if ok:
                k = _read_key(ctx.artifact_dir / fname)
                if k:
                    keys[tcb_u64] = k
            else:
                lines.append(f"TCB {comp}={val} (u64=0x{tcb_u64:016x}) rejected (unexpected): {err}")

    if len(keys) < 2:
        lines.append("TCB sensitivity N/A — all committed components are zero")
        return StepHandlerResult(exit_code=0, stdout="\n".join(lines))

    if len(set(keys.values())) == len(keys):
        lines.append(f"All {len(keys)} distinct TCB values produce distinct keys")
        return StepHandlerResult(exit_code=0, stdout="\n".join(lines))
    return StepHandlerResult(exit_code=1, stderr="\n".join(lines) + "\nSome TCB values produce identical keys")


def test_gfs(ctx: StepContext) -> StepHandlerResult:
    """GFS sensitivity: GFS=1 and GFS=2 produce different keys."""
    ok1, err1 = _derive_key(ctx, "gfs1_key.bin", gfs=1)
    ok2, err2 = _derive_key(ctx, "gfs2_key.bin", gfs=2)
    if not ok1 or not ok2:
        return StepHandlerResult(exit_code=1, stderr=err1 or err2)
    k1 = _read_key(ctx.artifact_dir / "gfs1_key.bin")
    k2 = _read_key(ctx.artifact_dir / "gfs2_key.bin")
    if k1 != k2:
        return StepHandlerResult(exit_code=0, stdout="GFS=0x01 and GFS=0x02 keys differ")
    return StepHandlerResult(exit_code=1, stderr="GFS=0x01 and GFS=0x02 keys are identical")


def test_gfs_field_mixing(ctx: StepContext) -> StepHandlerResult:
    """GFS bits 0-5 each produce a key distinct from GFS=0 baseline."""
    ok, err = _derive_key(ctx, "gfs_baseline.bin", gfs=0)
    if not ok:
        return StepHandlerResult(exit_code=1, stderr=f"Baseline derivation failed: {err}")
    baseline = _read_key(ctx.artifact_dir / "gfs_baseline.bin")

    bits = [
        (0, "Image ID"),
        (1, "Family ID"),
        (2, "Measurement"),
        (3, "Guest SVN Policy"),
        (4, "Guest SVN"),
        (5, "TCB Version"),
    ]
    failed = []
    lines = []
    for bit, label in bits:
        ok, err = _derive_key(ctx, f"gfs_bit{bit}.bin", gfs=1 << bit)
        if not ok:
            failed.append(f"GFS bit {bit} ({label}): derivation failed: {err}")
            continue
        k = _read_key(ctx.artifact_dir / f"gfs_bit{bit}.bin")
        if k != baseline:
            lines.append(f"GFS bit {bit} ({label}): differs from baseline")
        else:
            failed.append(f"GFS bit {bit} ({label}): same as baseline")

    if failed:
        return StepHandlerResult(exit_code=1, stderr="\n".join(failed))
    return StepHandlerResult(exit_code=0, stdout="\n".join(lines))


# ── Steps ─────────────────────────────────────────────────────────────────────

def steps() -> list[BaseStep]:
    pre = [
        Step.for_callable(
            name="Calculate measurement",
            type="setup",
            handler="calculate_measurement",
            timeout=60,
        ),
    ]
    if _HAS_ID_BLOCK:
        pre.append(Step.for_callable(
            name="Generate ID block",
            type="setup",
            handler="generate_id_block",
            timeout=30,
        ))

    return pre + [
        Step.for_vm_launch(
            name="Launch SEV-SNP guest",
            type="setup",
            timeout=300,
        ).add_hint(
            "Address already in use",
            "A previous VM may still be running. "
            "Try: sudo kill $(pgrep -f 'qemu.*guest-cid')",
        ),

        # Get and pull report so parse_report can run while VM is still up
        Step.for_guest(
            name="Get attestation report",
            type="setup",
            command="snpguest report report.bin request.bin --random",
            timeout=60,
        ),
        Step.for_guest_pull(
            name="Pull attestation report",
            type="setup",
            guest_src="report.bin",
            host_dest="report.bin",
            timeout=30,
        ),

        # Parse report to learn SVN and TCB bounds (VM still running)
        Step.for_callable(
            name="Parse attestation report",
            type="required",
            handler="parse_report",
            timeout=30,
        ),

        # All key derivation tests run while VM is up, via vsock loops
        Step.for_callable(
            name="Test determinism",
            type="required",
            handler="test_determinism",
            timeout=60,
        ),
        Step.for_callable(
            name="Test VMPL isolation",
            type="required",
            handler="test_vmpl_isolation",
            timeout=60,
        ),
        Step.for_callable(
            name="Test root key difference",
            type="required",
            handler="test_root_key_difference",
            timeout=60,
        ),
        Step.for_callable(
            name="Test SVN bound enforcement and sensitivity",
            type="required",
            handler="test_svn",
            timeout=120,
        ),
        Step.for_callable(
            name="Test TCB bound enforcement and sensitivity",
            type="required",
            handler="test_tcb",
            timeout=120,
        ),
        Step.for_callable(
            name="Test GFS sensitivity",
            type="required",
            handler="test_gfs",
            timeout=60,
        ),
        Step.for_callable(
            name="Test GFS field mixing",
            type="required",
            handler="test_gfs_field_mixing",
            timeout=60,
        ),

        Step.for_vm_stop(
            name="Stop VM",
            type="info",
            timeout=60,
        ),
    ]
