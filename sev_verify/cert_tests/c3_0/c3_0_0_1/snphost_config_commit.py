"""snphost config/commit: Verify TCB config changes in guest attestation.

Mixed-scope test: exercises ``snphost commit`` and ``snphost config
set/reset`` on the host and verifies that the configured TCB values are
reflected in attestation reports obtained from a guest VM via
``snpguest report``.

``snphost commit`` is always exercised.  It takes no level argument and
commits ``CurrentTcb``; the step runs after ``config reset`` so the
platform is back at baseline, where ``CommittedTcb`` normally already
equals ``CurrentTcb``.  **This is a permanent, irreversible change** to the
platform's committed floor (it does not reset on reboot).  The step is
``required`` (a commit failure fails the test) and records Committed TCB
before and after by reading the guest attestation report.

Two scenarios are covered with a single VM:

* **Fresh VM** — booted *after* ``config set`` lowers TCB; its first
  attestation report must carry the lowered Reported TCB (matching the
  host) while Current TCB still reflects the platform values.  The report
  is additionally checked to be *signed by the VCEK for that lowered TCB*:
  we fetch the alternate VCEK from the KDS (its URL is derived from the
  report's Reported TCB) and run a signature-only attestation check.
* **Live VM** — while the VM is still running, ``config reset`` restores
  TCB on the host; the VM's next attestation report must reflect the
  restored values in both Reported and Current TCB.

Only the lowered (alternate) VCEK is fetched from the KDS.  The restored
report reuses the platform's baseline TCB, whose VCEK is fetched heavily
by the ``3.0.0-0`` attestation test, so re-fetching it here would risk KDS
rate-limiting for no added coverage.

The test attempts to leave TCB in a reset/clean state via a final ``info``
step that runs even when earlier ``required`` steps fail. The only ``setup``
step (``show-tcb``) precedes the TCB mutation, so any failure after that point
still runs teardown; a ``setup`` failure skips teardown but leaves TCB untouched.
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

# Core TCB fields (excludes FMC, which is not affected by config set)
_CORE_TCB_FIELDS = ("Boot Loader", "TEE", "SNP", "Microcode")


# ── TCB parsing (shared by steps() and verify CLI) ──────────────


def _parse_tcb_sections(output: str) -> dict[str, dict[str, str]]:
    """Parse TCB output into ``{section: {field: value}}``.

    Works with both ``snphost show tcb`` (sections: Reported, Platform)
    and ``snpguest display report`` (sections: Current, Reported).

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
    proc = _run_snphost_tcb()
    if proc.returncode != 0:
        return StepHandlerResult(
            exit_code=1,
            stderr=f"snphost show tcb failed: {proc.stderr.strip()}",
        )

    sections = _parse_tcb_sections(proc.stdout)
    reported = sections.get("Reported", {})
    platform = sections.get("Platform", {})

    match = all(reported.get(f) == platform.get(f) for f in _CORE_TCB_FIELDS)

    if mode == "verify-match" and not match:
        lines = [
            "FAIL: Reported TCB should match Platform after reset",
            f"  Reported: {reported}",
            f"  Platform: {platform}",
        ]
        return StepHandlerResult(exit_code=1, stderr="\n".join(lines))
    if mode == "verify-differ" and match:
        return StepHandlerResult(
            exit_code=1,
            stderr="FAIL: Reported TCB should differ from Platform after config set",
        )
    return StepHandlerResult(exit_code=0)


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

    Guest Reported TCB is compared to host Reported TCB; guest Current TCB
    is compared to host Platform TCB (the host's actual running versions).

    When *expect_match_reported* is ``True`` (after ``config reset``), host
    Reported and Platform TCB must also match.  When ``False`` (after
    ``config set``), host Reported must differ from Platform while guest
    Reported tracks the lowered host Reported value.
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

    guest_reported_matches_host = all(
        guest_reported.get(f) == host_reported.get(f) for f in _CORE_TCB_FIELDS
    )
    guest_current_matches_platform = all(
        guest_current.get(f) == host_platform.get(f) for f in _CORE_TCB_FIELDS
    )
    reported_matches_platform = all(
        host_reported.get(f) == host_platform.get(f) for f in _CORE_TCB_FIELDS
    )

    errors: list[str] = []

    if not guest_reported_matches_host:
        errors.append(
            "FAIL: guest Reported TCB does not match host Reported TCB"
        )
    if not guest_current_matches_platform:
        errors.append(
            "FAIL: guest Current TCB does not match host Platform TCB"
        )

    if expect_match_reported and not reported_matches_platform:
        errors.append(
            "FAIL: host Reported TCB should match Platform after reset"
        )
    elif not expect_match_reported and reported_matches_platform:
        errors.append(
            "FAIL: host Reported TCB should differ from Platform after config set"
        )

    if errors:
        errors.extend([
            f"  Guest Reported TCB: {guest_reported}",
            f"  Guest Current TCB: {guest_current}",
            f"  Host Reported TCB: {host_reported}",
            f"  Host Platform TCB: {host_platform}",
        ])
        return StepHandlerResult(exit_code=1, stderr="\n".join(errors))

    label = "matches" if expect_match_reported else "differs from"
    return StepHandlerResult(
        exit_code=0,
        stdout=(
            f"Guest report TCB matches host TCB "
            f"(host Reported {label} Platform) as expected\n"
            f"  Guest Reported TCB: {guest_reported}\n"
            f"  Guest Current TCB: {guest_current}\n"
            f"  Host Reported TCB: {host_reported}\n"
            f"  Host Platform TCB: {host_platform}"
        ),
    )


def verify_guest_report_lowered(ctx: StepContext) -> StepHandlerResult:
    """Verify the fresh-VM report carries the lowered TCB values.

    Guest Reported TCB must match the host's lowered Reported TCB; guest
    Current TCB must still match the host's unchanged Platform TCB.
    """
    report_path = ctx.artifact_dir / "report.bin"
    if not report_path.exists():
        return StepHandlerResult(exit_code=1, stderr=f"report not found: {report_path}")
    return _verify_guest_tcb(str(report_path), expect_match_reported=False)


def verify_guest_report_restored(ctx: StepContext) -> StepHandlerResult:
    """Verify the live-VM report carries the restored (original) TCB values.

    After ``config reset``, guest Reported and Current TCB must match the
    host's Reported and Platform TCB (which are equal again).
    """
    report_path = ctx.artifact_dir / "report_after_reset.bin"
    if not report_path.exists():
        return StepHandlerResult(exit_code=1, stderr=f"report not found: {report_path}")
    return _verify_guest_tcb(str(report_path), expect_match_reported=True)


# ── VCEK signature verification ─────────────────────────────────


def verify_lowered_report_signature(ctx: StepContext) -> StepHandlerResult:
    """Verify the fresh-VM report is signed by the VCEK for its lowered TCB.

    The lowered Reported TCB produces a *different* VCEK than the platform
    baseline.  We fetch that alternate VCEK from the KDS — ``snpguest fetch
    vcek`` derives the request URL from the report's Reported TCB — into a
    dedicated directory, then run a signature-only attestation check
    (``snpguest verify attestation --signature``), which needs only the
    VCEK present.

    Only this alternate VCEK is fetched; the restored/baseline VCEK is
    exercised by the ``3.0.0-0`` attestation test, so re-fetching it here
    would just add KDS load and risk rate-limiting.
    """
    report_path = ctx.artifact_dir / "report.bin"
    if not report_path.exists():
        return StepHandlerResult(exit_code=1, stderr=f"report not found: {report_path}")

    certs_dir = ctx.artifact_dir / "vcek_lowered"
    certs_dir.mkdir(parents=True, exist_ok=True)

    fetch = subprocess.run(
        ["snpguest", "fetch", "vcek", "pem", str(certs_dir), str(report_path)],
        capture_output=True, text=True, timeout=60,
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
        capture_output=True, text=True, timeout=60,
    )
    if verify.returncode != 0:
        return StepHandlerResult(
            exit_code=1,
            stdout=verify.stdout,
            stderr=(
                "FAIL: lowered-TCB report not signed by its VCEK: "
                f"{verify.stderr.strip()}"
            ),
        )

    return StepHandlerResult(
        exit_code=0,
        stdout=(
            "Lowered-TCB report signature verified against its VCEK\n"
            f"  {verify.stdout.strip()}"
        ),
    )


# ── Commit (guarded no-op) ─────────────────────────────────────


def commit_current_tcb(ctx: StepContext) -> StepHandlerResult:
    """Always run ``snphost commit`` and record Committed TCB before/after.

    ``snphost commit`` takes no level argument — it commits the platform's
    *Current* TCB, ratcheting ``CommittedTcb`` up to ``CurrentTcb``.  This is
    a **permanent, irreversible** change to the platform's committed floor
    (it does not reset on reboot).

    Run at this point in the flow (after ``config reset``) the platform is
    back at baseline, so ``CommittedTcb`` normally already equals
    ``CurrentTcb`` and the commit lands on the level that is already
    committed.  We read Committed and Current from the guest attestation
    report and record both, then state the post-commit Committed value
    (which the firmware sets to Current), so the info log shows exactly what
    the committed floor was before and after.
    """
    report_path = ctx.artifact_dir / "report_after_reset.bin"
    if not report_path.exists():
        return StepHandlerResult(exit_code=1, stderr=f"report not found: {report_path}")

    try:
        sections = _parse_report_tcb_sections(str(report_path))
    except RuntimeError as e:
        return StepHandlerResult(exit_code=1, stderr=str(e))

    committed_before = sections["Committed"]
    current = sections["Current"]

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

    # Commit sets Committed := Current, so the post-commit floor is Current.
    committed_after = {f: current.get(f) for f in _CORE_TCB_FIELDS}
    advanced = any(
        committed_before.get(f) != committed_after.get(f) for f in _CORE_TCB_FIELDS
    )
    note = (
        "committed floor ADVANCED to Current TCB"
        if advanced
        else "no change (Committed already equalled Current)"
    )

    return StepHandlerResult(
        exit_code=0,
        stdout=(
            f"snphost commit succeeded — {note}\n"
            f"  Committed TCB before: {committed_before}\n"
            f"  Committed TCB after:  {committed_after}\n"
            f"  (Current TCB: {current})"
        ),
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
            "Cannot run test: all TCB fields (Boot Loader, TEE, SNP, Microcode) are 0. "
            "At least one field must be non-zero to perform config-set-lower test."
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
        # 3. Host-side check: Reported ≠ Platform
        Step.for_callable(
            name="verify-differ after config-set-lower",
            type="required",
            handler="verify_differ",
            timeout=30,
        ),
        # 4. Boot a fresh VM (TCB was lowered before boot)
        Step.for_vm_launch(
            name="Launch SEV-SNP guest",
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
            timeout=130,
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
        # 14. Commit TCB — always runs `snphost commit` (commits Current
        #     TCB; after reset that normally equals Committed already). This
        #     is a permanent, irreversible change to the committed floor; the
        #     handler records Committed TCB before/after in its output.
        Step.for_callable(
            name="commit-current-tcb",
            type="required",
            handler="commit_current_tcb",
            timeout=60,
        ),
        # 15. Stop VM
        Step.for_vm_stop(
            name="Stop VM",
            type="info",
            timeout=60,
        ),
        # 16. Final config-reset teardown — ensure TCB is clean for next test
        Step.for_host(
            name="teardown-config-reset",
            type="info",
            command="snphost config reset",
        ),
    ]


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("verify-match", "verify-differ"):
        print(f"usage: python3 -m {_THIS_MODULE} verify-match|verify-differ", file=sys.stderr)
        sys.exit(2)
    sys.exit(_verify_cli(sys.argv[1]))
