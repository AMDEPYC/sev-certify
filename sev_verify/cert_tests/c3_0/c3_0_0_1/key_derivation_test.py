"""key_derivation_test: Launch SEV-SNP guest and run key derivation tests.

Verifies that the SNP MSG_KEY_REQ firmware command produces correct and
consistent derived keys:
  - Determinism: same params -> same key
  - VMPL isolation: different VMPL -> different keys
  - Root key difference: VCEK vs VMRK -> different keys
  - Guest SVN sensitivity and above-bound rejection
  - TCB version sensitivity and launch-TCB bound enforcement
  - Guest Field Select (GFS) sensitivity and per-bit field mixing

The attestation report is fetched first and read directly as bytes via
:mod:`sev_verify.attestation_report` — see that module for why binary
parsing is used in place of ``snpguest display report`` text, and for how
TCB_VERSION's generation-dependent byte layout is resolved. The bounds are the
values the guest was launched with: LAUNCH_TCB (the TCB captured at launch,
fixed for the life of the VM) bounds the TCB loop, and GUEST_SVN (the SVN from
the ID block) bounds the SVN loop. VMPL drives the third.

Above-bound TCB tests use launch+1, launch+2, launch+3 per component,
derived from the runtime attestation report. No static
assumption about platform TCB values is needed.

The guest is always launched with an ID block (sev_verify.cvm_props), so
the report carries a non-zero guest SVN, family_id and image_id; the values
come from the ID_BLOCK_* environment variables.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from sev_verify import attestation_report
# Step handlers are resolved by name from this module's globals, so these
# imports are used even though nothing here calls them directly.
from sev_verify.cvm_props import calculate_measurement, generate_id_block  # noqa: F401
from sev_verify.guest_vsock import fetch_guest_file_bytes, run_guest_command
from sev_verify.models import BaseStep, Step, StepContext, StepHandlerResult
from sev_verify.vm_profile import VMProfile

vm_profile = VMProfile(
    image_path="",
    memory_mb=2048,
)

_TCB_ABOVE_BOUND_STEPS = 3  # number of values above the launch bound to test per component


# ── Report helpers ─────────────────────────────────────────────────────────────

def _load_report(ctx: StepContext) -> attestation_report.AttestationReport:
    """Re-read report.bin (an artifact from the "Pull attestation report" step).

    Cheap enough to call from every step that needs a field off the report,
    rather than threading parsed values through a sidecar file.
    """
    return attestation_report.read(
        ctx.artifact_dir / "report.bin",
        generation=attestation_report.host_generation(),
    )


def _make_tcb(layout: str, **overrides: int) -> attestation_report.TcbVersion:
    """Build a TcbVersion with all components zeroed except *overrides*.

    Used to test one TCB component's bound in isolation — the other
    components are left at 0 (below their own bound), so a rejection can
    only be attributed to the component under test. ``fmc`` is included
    only for the Turin layout, which is the only one that has it.
    """
    fields = dict(bootloader=0, tee=0, snp=0, microcode=0)
    if layout == attestation_report.TCB_LAYOUT_TURIN:
        fields["fmc"] = 0
    fields.update(overrides)
    return attestation_report.TcbVersion(**fields)


# ── Guest helpers (called from callable steps while VM is running) ─────────────

def _current_vmpl(ctx: StepContext) -> int:
    """The guest's running VMPL, as recorded by the "Detect running VMPL" step."""
    return int((ctx.artifact_dir / "vmpl").read_text())


def _derive_key(ctx: StepContext, filename: str, root: str = "vcek",
                vmpl: Optional[int] = None, svn: int = 0, tcb: int = 0,
                gfs: int = 1) -> tuple[bool, str]:
    """Run snpguest key on the guest, fetch the key bytes to artifact_dir.

    *vmpl* defaults to the guest's running VMPL: the firmware rejects a
    request below the caller's own VMPL, so a hardcoded 0 would fail for a
    guest running at VMPL 1 or higher.

    Returns (success, message). On success the key file is written locally.
    A non-zero exit from snpguest (the firmware rejected the request) is
    returned as (False, message). A failure to fetch the key after snpguest
    succeeded is *not* a rejection, so it raises GuestCommandError instead of
    being folded into the same return value: callers that read False as
    "bound enforced" would otherwise report PASS for a request the firmware
    accepted.
    """
    if vmpl is None:
        vmpl = _current_vmpl(ctx)
    cmd = (f"snpguest key {filename} {root} --vmpl {vmpl} "
           f"--guest_svn {svn} --tcb_version {tcb} --guest_field_select {gfs}")
    result = run_guest_command(ctx.profile, cmd, timeout=30)
    if result.exit_code != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    data = fetch_guest_file_bytes(ctx.profile, filename, timeout=30)
    (ctx.artifact_dir / filename).write_bytes(data)
    return True, ""


def _reason(err: str) -> str:
    """First line of a rejection message, for the per-value log lines."""
    return err.splitlines()[0] if err else "no message"


def _sweep_values(max_val: int) -> list[int]:
    """Values 0..max_val to derive keys for in a sensitivity sweep.

    Every value when there are few (max_val < 16); otherwise the values around
    each end of the range and around its middle, since deriving all of them
    can take hundreds of requests per component.
    """
    if max_val < 16:
        return list(range(max_val + 1))
    mid = max_val // 2
    return sorted({0, 1, 2, mid - 1, mid, mid + 1, max_val - 2, max_val - 1, max_val})


def _read_key(path: Path) -> bytes:
    """Read a derived key from artifact_dir.

    Raises on failure rather than returning None: callers compare keys
    directly, and two failed reads would otherwise compare equal.
    """
    return path.read_bytes()


# ── Callable step handlers ────────────────────────────────────────────────────

def parse_report(ctx: StepContext) -> StepHandlerResult:
    """Read the pulled attestation report and surface the bounds it carries.

    Reads report.bin directly rather than parsing ``snpguest display report``
    text (see :mod:`sev_verify.attestation_report`). Failing fast here — before
    any of the sensitivity/bound tests run — turns an unparseable report or an
    unrecognised processor generation into one clear error instead of a
    cascade of unrelated-looking failures below.
    """
    try:
        report = _load_report(ctx)
    except attestation_report.ReportError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))
    return StepHandlerResult(
        exit_code=0,
        stdout=(f"Report version: {report.version}, Guest SVN: {report.guest_svn}\n"
                f"Generation: {report.generation}\n"
                f"Launch TCB: {report.launch_tcb}\n"
                f"Reported TCB: {report.reported_tcb}"),
    )


def detect_vmpl(ctx: StepContext) -> StepHandlerResult:
    """Find the guest's running VMPL and record it for the other steps.

    The report's VMPL field is the VMPL *requested* in the report message, not
    the one the guest runs at. The firmware rejects a key request whose VMPL
    is below the caller's own, so the lowest VMPL in 0-3 that is accepted is
    the running VMPL. If none is accepted something else is wrong, and this
    fails rather than guessing.
    """
    errors = []
    for vmpl in range(4):
        ok, err = _derive_key(ctx, f"vmpl_probe_{vmpl}.bin", vmpl=vmpl)
        if ok:
            (ctx.artifact_dir / "vmpl").write_text(str(vmpl))
            return StepHandlerResult(exit_code=0, stdout=f"Running VMPL: {vmpl}")
        errors.append(f"VMPL{vmpl}: {err}")
    return StepHandlerResult(
        exit_code=1,
        stderr="No VMPL 0-3 accepted for key derivation:\n" + "\n".join(errors),
    )


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
    cur = _current_vmpl(ctx)
    hi = cur + 1
    ok_cur, err_cur = _derive_key(ctx, f"vmpl{cur}_key.bin", vmpl=cur)
    if not ok_cur:
        return StepHandlerResult(exit_code=1, stderr=f"VMPL{cur} derivation failed: {err_cur}")
    if hi > 3:
        return StepHandlerResult(
            exit_code=0,
            stdout=f"Running at VMPL3 — no higher VMPL to compare against (N/A)",
        )
    ok_hi, err_hi = _derive_key(ctx, f"vmpl{hi}_key.bin", vmpl=hi)
    if not ok_hi:
        return StepHandlerResult(
            exit_code=1,
            stderr=f"VMPL{hi} derivation failed after VMPL{cur} succeeded: {err_hi}",
        )
    k_cur = _read_key(ctx.artifact_dir / f"vmpl{cur}_key.bin")
    k_hi = _read_key(ctx.artifact_dir / f"vmpl{hi}_key.bin")
    if k_cur != k_hi:
        return StepHandlerResult(exit_code=0, stdout=f"VMPL{cur} and VMPL{hi} keys differ (proper isolation)")
    return StepHandlerResult(exit_code=1, stderr=f"VMPL{cur} and VMPL{hi} keys are identical")


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
    try:
        report = _load_report(ctx)
    except attestation_report.ReportError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))
    max_svn = report.guest_svn
    lines = [f"Guest SVN upper bound (launch value, from the ID block): {max_svn}"]
    passed = True

    # Control: the same request at the bound must succeed, otherwise a
    # rejection above the bound says nothing about the bound.
    ok, err = _derive_key(ctx, "svn_at_bound.bin", svn=max_svn, gfs=1 << 4)
    if not ok:
        return StepHandlerResult(
            exit_code=1,
            stderr=f"SVN={max_svn} (the bound) was rejected, so rejections above it "
                   f"cannot be attributed to the bound: {_reason(err)}",
        )

    # Above-bound: SVN max_svn+1, max_svn+2, max_svn+3 must all be rejected
    for svn in range(max_svn + 1, max_svn + 4):
        ok, err = _derive_key(ctx, f"svn_above_{svn}.bin", svn=svn, gfs=1 << 4)
        if ok:
            lines.append(f"FAIL: SVN={svn} succeeded — bound ({max_svn}) not enforced")
            passed = False
        else:
            lines.append(f"Bound enforced: SVN={svn} rejected ({_reason(err)})")

    if not passed:
        return StepHandlerResult(exit_code=1, stderr="\n".join(lines))

    # Sensitivity: sampled valid values 0..max_svn produce distinct keys
    if max_svn == 0:
        lines.append("Only one valid SVN value (0) — sensitivity N/A (ID_BLOCK_GUEST_SVN is 0)")
        return StepHandlerResult(exit_code=0, stdout="\n".join(lines))

    keys = {}
    for svn in _sweep_values(max_svn):
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
    try:
        report = _load_report(ctx)
    except attestation_report.ReportError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))
    c = report.launch_tcb
    if c is None:
        return StepHandlerResult(
            exit_code=1,
            stderr=f"LAUNCH_TCB could not be decoded (report version {report.version}, "
                   f"generation {report.generation}): it needs a version 3 or later "
                   f"report from a recognised processor generation "
                   f"(see attestation_report.py)",
        )
    _, layout = attestation_report.host_generation()
    lines = [f"Launch TCB: {c}"]
    passed = True

    # Component list is generation-dependent: fmc only exists on Turin+.
    components = [
        ("bootloader", "Boot Loader", c.bootloader),
        ("tee",        "TEE",         c.tee),
        ("snp",        "SNP",         c.snp),
        ("microcode",  "Microcode",   c.microcode),
    ]
    if c.fmc is not None:
        components.append(("fmc", "FMC", c.fmc))

    # Control: the launch TCB itself must be accepted, otherwise a
    # rejection above the bound says nothing about the bound.
    launch_u64 = _make_tcb(
        layout, **{comp: val for comp, _label, val in components}
    ).to_u64(layout)
    ok, err = _derive_key(ctx, "tcb_at_bound.bin", tcb=launch_u64, gfs=1 << 5)
    if not ok:
        return StepHandlerResult(
            exit_code=1,
            stderr=f"Launch TCB (u64=0x{launch_u64:016x}) was rejected, so "
                   f"rejections above it cannot be attributed to the bound: {_reason(err)}",
        )

    # Above-bound: launch+1 .. launch+N per component must all be rejected.
    for comp, label, max_val in components:
        above_vals = [v for v in range(max_val + 1, max_val + _TCB_ABOVE_BOUND_STEPS + 1)
                      if v <= 0xFF]
        if not above_vals:
            lines.append(f"{label}: launch={max_val} is max (0xFF) — no above-bound values to test")
            continue
        for val in above_vals:
            tcb_u64 = _make_tcb(layout, **{comp: val}).to_u64(layout)
            ok, err = _derive_key(ctx, f"tcb_above_{comp}_{val}.bin",
                                  tcb=tcb_u64, gfs=1 << 5)
            if ok:
                lines.append(f"FAIL: {label}={val} succeeded — "
                             f"bound ({max_val}) not enforced")
                passed = False
            else:
                lines.append(f"Bound enforced: {label}={val} rejected ({_reason(err)})")

    if not passed:
        return StepHandlerResult(exit_code=1, stderr="\n".join(lines))

    # Sensitivity: vary each component over sampled values up to its launch value.
    # Track by tcb_u64 to deduplicate (e.g. val=0 for any component gives the same u64).
    keys: dict[int, bytes] = {}  # tcb_u64 -> key bytes
    attempted: set[int] = set()  # tcb_u64 values we tried to derive
    for comp, _label, max_val in components:
        for val in _sweep_values(max_val):
            tcb_u64 = _make_tcb(layout, **{comp: val}).to_u64(layout)
            if tcb_u64 in attempted:
                continue  # already derived this exact TCB value
            attempted.add(tcb_u64)
            fname = f"tcb_{comp}_{val}.bin"
            ok, err = _derive_key(ctx, fname, tcb=tcb_u64, gfs=1 << 5)
            if ok:
                k = _read_key(ctx.artifact_dir / fname)
                if k:
                    keys[tcb_u64] = k
            else:
                lines.append(f"TCB {comp}={val} (u64=0x{tcb_u64:016x}) rejected (unexpected): {err}")

    if len(attempted) < 2:
        lines.append("TCB sensitivity N/A — all launch TCB components are zero")
        return StepHandlerResult(exit_code=0, stdout="\n".join(lines))
    if len(keys) < 2:
        return StepHandlerResult(
            exit_code=1,
            stderr="\n".join(lines) + "\nFewer than 2 successful TCB derivations",
        )

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
        (0, "Guest Policy"),
        (1, "Image ID"),
        (2, "Family ID"),
        (3, "Measurement"),
        (4, "Guest SVN"),
        (5, "TCB Version")
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


def save_cross_cvm_key(ctx: StepContext) -> StepHandlerResult:
    """Derive and save a reference key from CVM 1 for cross-CVM comparison."""
    ok, err = _derive_key(ctx, "cross_cvm_key.bin")
    if not ok:
        return StepHandlerResult(exit_code=1, stderr=f"Key derivation failed: {err}")
    k = _read_key(ctx.artifact_dir / "cross_cvm_key.bin")
    # Save as cvm1 reference for comparison after second launch
    (ctx.artifact_dir / "cross_cvm_key_1.bin").write_bytes(k)
    return StepHandlerResult(exit_code=0, stdout="Reference key saved from CVM 1")


def verify_cross_cvm_key(ctx: StepContext) -> StepHandlerResult:
    """Derive the same key in CVM 2 and compare with CVM 1's key."""
    ok, err = _derive_key(ctx, "cross_cvm_key.bin")
    if not ok:
        return StepHandlerResult(exit_code=1, stderr=f"Key derivation failed in CVM 2: {err}")
    ref = ctx.artifact_dir / "cross_cvm_key_1.bin"
    if not ref.exists():
        return StepHandlerResult(exit_code=1, stderr="CVM 1 reference key not found")
    k2 = _read_key(ctx.artifact_dir / "cross_cvm_key.bin")
    k1 = _read_key(ref)
    if k1 == k2:
        return StepHandlerResult(
            exit_code=0,
            stdout="Keys match across two independent CVMs — platform-bound derivation confirmed",
        )
    return StepHandlerResult(
        exit_code=1,
        stderr="Keys differ across CVMs — derived key is not stable across CVM lifetimes",
    )


# ── Steps ─────────────────────────────────────────────────────────────────────

def steps() -> list[BaseStep]:
    return [
        Step.for_callable(
            name="Calculate measurement",
            type="setup",
            handler="calculate_measurement",
            timeout=60,
        ),
        Step.for_callable(
            name="Generate ID block",
            type="setup",
            handler="generate_id_block",
            timeout=30,
        ),
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
        Step.for_callable(
            name="Detect running VMPL",
            type="setup",
            handler="detect_vmpl",
            timeout=60,
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

        # ── Cross-CVM determinism ─────────────────────────────────────────
        Step.for_callable(
            name="Save reference key from CVM 1",
            type="required",
            handler="save_cross_cvm_key",
            timeout=30,
        ),

        Step.for_vm_stop(
            name="Stop CVM 1",
            type="info",
            timeout=60,
        ),

        Step.for_vm_launch(
            name="Launch CVM 2",
            type="setup",
            timeout=300,
        ).add_hint(
            "Address already in use",
            "A previous VM may still be running. "
            "Try: sudo kill $(pgrep -f 'qemu.*guest-cid')",
        ),

        Step.for_callable(
            name="Verify key matches across CVMs",
            type="required",
            handler="verify_cross_cvm_key",
            timeout=30,
        ),

        Step.for_vm_stop(
            name="Stop CVM 2",
            type="info",
            timeout=60,
        ),
    ]
