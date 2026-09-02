"""snphost config/commit: Verify TCB config changes in guest attestation.

Mixed-scope test: exercises ``snphost config set/reset`` and ``snphost
commit`` on the host and verifies the effect in guest attestation reports
(via ``snpguest report``). See ``docs/features/tcb-config-commit.md`` for
the full feature/spec background.
"""

import re
import subprocess
import sys

from sev_verify.models import BaseStep, Step, StepContext, StepHandlerResult
from sev_verify.vm_profile import VMProfile

_THIS_MODULE = __name__

vm_profile = VMProfile(
    image_path="",
    memory_mb=2048,
)

# Core TCB fields, present on every generation.
_CORE_TCB_FIELDS = ("Boot Loader", "TEE", "SNP", "Microcode")
# FMC is a Turin-only TCB component (Family 1Ah, bits 7:0 of TCB_VERSION);
# absent / "None" on Milan/Genoa. Compared only when present on both sides.
_ALL_TCB_FIELDS = _CORE_TCB_FIELDS + ("FMC",)


def _tcb_fields_match(a: dict[str, str], b: dict[str, str]) -> bool:
    """True iff every TCB field present on both dicts is equal.

    Core fields must exist on both sides — ``in`` rather than ``.get()`` so a
    field missing from both (``None == None``) does not read as a match, since
    a missing core field means a parse/format problem, not agreement.

    FMC is compared only when present on both sides: it is a real, independently
    mutable component on Turin (so an FMC-only divergence must not read as a
    match), but absent on older parts where there is nothing to compare.
    """
    if not all(f in a and f in b for f in _CORE_TCB_FIELDS):
        return False
    return all(a[f] == b[f] for f in _ALL_TCB_FIELDS if f in a and f in b)


# ── TCB parsing (shared by steps() and verify CLI) ──────────────


def _parse_tcb_sections(output: str) -> dict[str, dict[str, str]]:
    """Parse TCB output into ``{section: {field: value}}``.

    Works with both ``snphost show tcb`` (sections: Reported, Platform)
    and ``snpguest display report`` (sections: Current, Committed,
    Reported).

    Expected format::

        Reported TCB: TCB Version:
          Microcode:   25
          SNP:         27
        Platform TCB: TCB Version:
          ...
    """
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in output.splitlines():
        if m := re.match(r"(\w[\w ]*?) TCB:", line):
            current = {}
            sections[m.group(1)] = current
        elif current is not None and (m := re.match(r"\s+(.+?):\s+(\S+)", line)):
            current[m.group(1)] = m.group(2)
    return sections


def _run_snphost_tcb() -> subprocess.CompletedProcess:
    """Run snphost show tcb and return the completed process."""
    return subprocess.run(
        ["snphost", "show", "tcb"],
        capture_output=True, text=True, timeout=10,
    )


def _read_host_tcb() -> dict[str, dict[str, str]]:
    """Read all TCB sections (Reported + Platform) from snphost."""
    proc = _run_snphost_tcb()
    if proc.returncode != 0:
        raise RuntimeError(f"snphost show tcb failed: {proc.stderr.strip()}")
    sections = _parse_tcb_sections(proc.stdout)
    for name in ("Reported", "Platform"):
        if name not in sections:
            raise RuntimeError(f"no {name} TCB section in snphost show tcb output")
    return sections


def _read_platform_tcb() -> dict[str, str]:
    """Read Platform TCB fields from snphost."""
    return _read_host_tcb()["Platform"]


def _parse_report_tcb_sections(report_path: str) -> dict[str, dict[str, str]]:
    """Parse TCB sections from a guest attestation report binary.

    Runs ``snpguest display report <path>`` on the host and extracts the
    "Current TCB", "Committed TCB", and "Reported TCB" sections.
    """
    proc = subprocess.run(
        ["snpguest", "display", "report", report_path],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"snpguest display report failed: {proc.stderr.strip()}"
        )
    sections = _parse_tcb_sections(proc.stdout)
    for name in ("Current", "Committed", "Reported"):
        if name not in sections:
            raise RuntimeError(
                f"no {name} TCB section in snpguest display report output"
            )
    return {name: sections[name] for name in ("Current", "Committed", "Reported")}


# ── Host-side verification ──────────────────────────────────────


def _verify_result(mode: str) -> StepHandlerResult:
    """Compare Reported vs Platform TCB; used by callable steps and the CLI."""
    cmd = "snphost show tcb"
    proc = _run_snphost_tcb()
    if proc.returncode != 0:
        return StepHandlerResult(
            exit_code=1,
            stderr=f"snphost show tcb failed: {proc.stderr.strip()}",
            command=cmd,
        )

    sections = _parse_tcb_sections(proc.stdout)
    reported = sections.get("Reported", {})
    platform = sections.get("Platform", {})

    match = _tcb_fields_match(reported, platform)

    if mode == "verify-match" and not match:
        lines = [
            "FAIL: Reported should match Platform after reset",
            f"  Reported: {reported}",
            f"  Platform: {platform}",
        ]
        return StepHandlerResult(exit_code=1, stderr="\n".join(lines), command=cmd)
    if mode == "verify-differ" and match:
        return StepHandlerResult(
            exit_code=1,
            stderr="FAIL: Reported should differ from Platform after config set",
            command=cmd,
        )
    return StepHandlerResult(exit_code=0, command=cmd)


def verify_match(_ctx: StepContext) -> StepHandlerResult:
    """After reset: Reported TCB must match Platform."""
    return _verify_result("verify-match")


def verify_differ(_ctx: StepContext) -> StepHandlerResult:
    """After config set: Reported TCB must differ from Platform."""
    return _verify_result("verify-differ")


# ── Guest report verification ──────────────────────────────────


def _verify_guest_tcb(
    report_path: str,
    expect_match_reported: bool,
) -> StepHandlerResult:
    """Compare guest report TCB sections against the corresponding host values.

    Check Guest Reported TCB == host Reported TCB
    Check Guest Current TCB == host Platform TCB
    """
    try:
        guest = _parse_report_tcb_sections(report_path)
        host = _read_host_tcb()
    except RuntimeError as e:
        return StepHandlerResult(exit_code=1, stderr=str(e))

    guest_current = guest["Current"]
    guest_reported = guest["Reported"]
    host_reported = host["Reported"]
    host_platform = host["Platform"]

    guest_reported_matches_host = _tcb_fields_match(guest_reported, host_reported)
    guest_current_matches_platform = _tcb_fields_match(guest_current, host_platform)
    reported_matches_platform = _tcb_fields_match(host_reported, host_platform)

    errors: list[str] = []

    if not guest_reported_matches_host:
        errors.append("FAIL: guest Reported != host Reported")
    if not guest_current_matches_platform:
        errors.append("FAIL: guest Current != host Platform")

    if expect_match_reported and not reported_matches_platform:
        errors.append("FAIL: host Reported should match Platform after reset")
    elif not expect_match_reported and reported_matches_platform:
        errors.append("FAIL: host Reported should differ from Platform after config set")

    dump = [
        f"  guest Reported: {guest_reported}",
        f"  guest Current:  {guest_current}",
        f"  host Reported:  {host_reported}",
        f"  host Platform:  {host_platform}",
    ]
    if errors:
        return StepHandlerResult(exit_code=1, stderr="\n".join(errors + dump))

    label = "matches" if expect_match_reported else "differs from"
    return StepHandlerResult(
        exit_code=0,
        stdout="\n".join(
            [f"Guest TCB matches host (Reported {label} Platform)"] + dump
        ),
    )


def verify_guest_report_lowered(ctx: StepContext) -> StepHandlerResult:
    """Verify the fresh-VM report carries the lowered TCB values.
    """
    report_path = ctx.artifact_dir / "report.bin"
    if not report_path.exists():
        return StepHandlerResult(exit_code=1, stderr=f"report not found: {report_path}")
    return _verify_guest_tcb(str(report_path), expect_match_reported=False)


def verify_guest_report_restored(ctx: StepContext) -> StepHandlerResult:
    """Verify the live-VM report carries the restored (original) TCB values.
    """
    report_path = ctx.artifact_dir / "report_after_reset.bin"
    if not report_path.exists():
        return StepHandlerResult(exit_code=1, stderr=f"report not found: {report_path}")
    return _verify_guest_tcb(str(report_path), expect_match_reported=True)


# ── VCEK signature verification ─────────────────────────────────


def verify_lowered_report_signature(ctx: StepContext) -> StepHandlerResult:
    """Verify the fresh-VM report is signed by the VCEK for its lowered TCB.
    """
    report_path = ctx.artifact_dir / "report.bin"
    if not report_path.exists():
        return StepHandlerResult(exit_code=1, stderr=f"report not found: {report_path}")

    certs_dir = ctx.artifact_dir / "vcek_lowered"
    certs_dir.mkdir(parents=True, exist_ok=True)

    fetch = subprocess.run(
        ["snpguest", "fetch", "vcek", "pem", str(certs_dir), str(report_path)],
        capture_output=True, text=True, timeout=120,
    )
    if fetch.returncode != 0:
        stderr = fetch.stderr.strip()
        hint = " (KDS rate-limited; re-run in a minute)" if "429" in stderr else ""
        return StepHandlerResult(
            exit_code=1,
            stderr=f"snpguest fetch vcek failed{hint}: {stderr}",
        )

    verify = subprocess.run(
        [
            "snpguest", "verify", "attestation", "--signature",
            str(certs_dir), str(report_path),
        ],
        capture_output=True, text=True, timeout=120,
    )
    if verify.returncode != 0:
        return StepHandlerResult(
            exit_code=1,
            stdout=verify.stdout,
            stderr=f"FAIL: lowered-TCB report not signed by its VCEK: {verify.stderr.strip()}",
        )

    return StepHandlerResult(
        exit_code=0,
        stdout=f"Lowered-TCB report signature verified\n  {verify.stdout.strip()}",
    )


# ── Commit precondition + commit ────────────────────────────────


def verify_committed_equals_current(ctx: StepContext) -> StepHandlerResult:
    """Precondition gate for the commit: require ``CommittedTcb == CurrentTcb``.

    A ``setup`` step (see module docstring): if Committed does not already
    equal Current, committing would advance the floor and bless provisional
    firmware, so this fails and the runner halts before the commit step —
    unless ``--allow-host-changes`` (``ctx.allow_host_changes``) is set, which
    downgrades it to a warning and lets the commit proceed.
    """
    report_path = ctx.artifact_dir / "report_after_reset.bin"
    if not report_path.exists():
        return StepHandlerResult(exit_code=1, stderr=f"report not found: {report_path}")

    try:
        sections = _parse_report_tcb_sections(str(report_path))
    except RuntimeError as e:
        return StepHandlerResult(exit_code=1, stderr=str(e))

    committed = sections["Committed"]
    current = sections["Current"]

    dump = f"  Committed: {committed}\n  Current:   {current}"

    if _tcb_fields_match(committed, current):
        return StepHandlerResult(
            exit_code=0,
            stdout=f"Committed == Current - commit is a no-op on the floor.\n{dump}",
        )

    # Committed < Current: provisional firmware.  Committing would advance the
    # floor.  Allow it only when host-level changes are permitted.
    if getattr(ctx, "allow_host_changes", False):
        warning = (
            "WARNING: provisional firmware (Committed < Current). --allow-host-changes "
            f"set; commit will ADVANCE the floor this boot (resets on reboot).\n{dump}"
        )
        # stderr so the operator sees it live, not just in artifacts.
        print(warning, file=sys.stderr)
        return StepHandlerResult(exit_code=0, stdout=warning)

    return StepHandlerResult(
        exit_code=1,
        stderr=(
            "FAIL: provisional firmware (Committed < Current). Committing would "
            "advance the floor and bless the provisional image, blocking rollback. "
            f"Pass --allow-host-changes for a host that will be rebooted.\n{dump}"
        ),
    )


def commit_current_tcb(_ctx: StepContext) -> StepHandlerResult:
    """Run ``snphost commit`` and check return code.

    Runs only after the ``verify-committed-equals-current`` precondition, so
    on a normal host the commit is a no-op. Force bypass with
    ``--allow-host-changes``.
    """
    proc = subprocess.run(
        ["snphost", "commit"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        return StepHandlerResult(
            exit_code=1,
            stdout=proc.stdout,
            stderr=f"snphost commit failed: {proc.stderr.strip()}",
        )

    return StepHandlerResult(
        exit_code=0,
        stdout="snphost commit succeeded",
    )


# ── CLI entry (unchanged) ──────────────────────────────────────


def _verify_cli(mode: str) -> int:
    """CLI entry: print stderr from result and return exit code."""
    r = _verify_result(mode)
    if r.stderr:
        print(r.stderr.strip(), file=sys.stderr)
    return r.exit_code


# ── Step definitions ────────────────────────────────────────────


def _config_set(bl: int, tee: int, snp: int, ucode: int,
                fmc: int | None, mask: int) -> str:
    """Build a ``snphost config set`` command string."""
    args = f"{bl} {tee} {snp} {ucode} {mask}"
    if fmc is not None:
        args += f" {fmc}"
    return f"snphost config set {args}"


def steps() -> list[BaseStep]:
    tcb = _read_platform_tcb()
    bl = int(tcb["Boot Loader"])
    tee = int(tcb["TEE"])
    snp = int(tcb["SNP"])
    ucode = int(tcb["Microcode"])

    fmc_raw = tcb.get("FMC", "")
    fmc = int(fmc_raw) if fmc_raw not in ("", "None") else None

    # Decrement one field for the config-set-lower test.
    # Priority: bl > snp > tee > ucode (decrement the first non-zero field).
    lo_bl, lo_tee, lo_snp, lo_ucode = bl, tee, snp, ucode
    if bl > 0:
        lo_bl -= 1
    elif snp > 0:
        lo_snp -= 1
    elif tee > 0:
        lo_tee -= 1
    elif ucode > 0:
        lo_ucode -= 1
    else:
        raise RuntimeError(
            "Cannot run test: all TCB fields (Boot Loader, TEE, SNP, Microcode) "
            "are 0; need a non-zero field to lower."
        )

    return [
        # 1. Read current Platform TCB
        Step.for_host(
            name="show-tcb",
            type="setup",
            command="snphost show tcb",
        ),
        # 2. Lower one TCB field
        Step.for_host(
            name="config-set-lower",
            type="required",
            command=_config_set(lo_bl, lo_tee, lo_snp, lo_ucode, fmc, 0),
        ),
        # 3. Host-side check: Reported != Platform
        Step.for_callable(
            name="verify-differ after config-set-lower",
            type="required",
            handler="verify_differ",
            timeout=30,
        ),
        # 4. Boot a fresh VM (TCB was lowered before boot)
        Step.for_vm_launch(
            name="Launch SEV-SNP guest",
            guest_id="vm-1",
            type="required",
            timeout=300,
        ).add_hint(
            "Address already in use",
            "A previous VM may still be running. "
            "Try: sudo kill $(pgrep -f 'qemu.*guest-cid')",
        ),
        # 5. Guest requests attestation report
        Step.for_guest(
            name="guest-report-after-lower",
            type="required",
            command="snpguest report report.bin request.bin --random",
            timeout=300,
        ),
        # 6. Pull report from guest
        Step.for_guest_pull(
            name="pull-report-after-lower",
            type="required",
            guest_src="report.bin",
            host_dest="report.bin",
            timeout=120,
        ),
        # 7. Verify guest report TCB reflects the lowered config
        Step.for_callable(
            name="verify-guest-report-lowered",
            type="required",
            handler="verify_guest_report_lowered",
            timeout=30,
        ),
        # 8. Verify the lowered report is signed by its (alternate) VCEK
        Step.for_callable(
            name="verify-lowered-report-signature",
            type="required",
            handler="verify_lowered_report_signature",
            # Covers both subprocesses (fetch + verify, 120s each) with headroom.
            timeout=270,
        ).add_hint("429", "Rate limited by KDS, re-run in a minute"),
        # 9. Restore TCB via config reset
        Step.for_host(
            name="config-reset",
            type="required",
            command="snphost config reset",
        ),
        # 10. Host-side check: Reported = Platform
        Step.for_callable(
            name="verify-match after config-reset",
            type="required",
            handler="verify_match",
            timeout=30,
        ),
        # 11. Same live VM requests a second attestation report
        Step.for_guest(
            name="guest-report-after-reset",
            type="required",
            command="snpguest report report_after_reset.bin request_after_reset.bin --random",
            timeout=300,
        ),
        # 12. Pull second report from guest
        Step.for_guest_pull(
            name="pull-report-after-reset",
            type="required",
            guest_src="report_after_reset.bin",
            host_dest="report_after_reset.bin",
            timeout=120,
        ),
        # 13. Verify second guest report TCB reflects restored values
        Step.for_callable(
            name="verify-guest-report-restored",
            type="required",
            handler="verify_guest_report_restored",
            timeout=30,
        ),
        # 14. Teardown: stop the VM. Placed BEFORE the commit gate below so
        #     cleanup can't be skipped — a `setup`-step failure at the gate
        #     marks every later step skipped, so anything that must always run
        #     has to precede it. Nothing in the commit path needs the guest:
        #     the gate reads the already-pulled report_after_reset.bin and
        #     commit is a host command. (The VM is also torn down in
        #     execute_test's finally; this is the explicit, visible path.)
        Step.for_vm_stop(
            name="Stop VM",
            type="info",
            timeout=60,
        ),
        # 15. Teardown: restore TCB with a final config reset. Also before the
        #     gate so it runs regardless of the commit precondition's outcome.
        #     config is already clean from step 9 and commit (step 17) does not
        #     touch ReportedTcb, so running the reset here keeps TCB clean.
        Step.for_host(
            name="teardown-config-reset",
            type="info",
            command="snphost config reset",
        ),
        # 16. Commit precondition (setup) — require Committed == Current so the
        #     commit cannot advance the floor. A failure halts before the commit
        #     below; `--allow-host-changes` downgrades it to a warning. See
        #     verify_committed_equals_current.
        Step.for_callable(
            name="verify-committed-equals-current",
            type="setup",
            handler="verify_committed_equals_current",
            timeout=30,
        ),
        # 17. Commit TCB — runs `snphost commit` and checks it returns 0. On a
        #     normal (non-provisional) host Committed == Current, so this is a
        #     no-op on the floor; that no-op success is all we can observe here.
        #     See commit_current_tcb.
        Step.for_callable(
            name="commit-current-tcb",
            type="required",
            handler="commit_current_tcb",
            timeout=60,
        ),
    ]


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("verify-match", "verify-differ"):
        print(f"usage: python3 -m {_THIS_MODULE} verify-match|verify-differ", file=sys.stderr)
        sys.exit(2)
    sys.exit(_verify_cli(sys.argv[1]))
