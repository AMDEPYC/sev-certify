"""snphost config/commit: Test SNP_CONFIG and SNP_COMMIT host commands.

Exercises snphost config set/reset and commit against the platform's
SEV-SNP firmware via /dev/sev.  All steps are host-side.

TCB values are read from ``snphost show tcb`` at step-definition time
so each command is a concrete ``snphost config set <args>`` invocation.

After config changes, :func:`verify_match` / :func:`verify_differ` compare
Reported vs Platform TCB (same checks as ``python3 -m <this_module> verify-*``).
"""

import re
import subprocess
import sys

from sev_verify.models import BaseStep, Step, StepContext, StepHandlerResult

_THIS_MODULE = __name__  # sev_verify.cert_tests.c3_0.c3_0_0_1.snphost_config_commit

# Core TCB fields (excludes FMC, which is not affected by config set)
_CORE_TCB_FIELDS = ("Boot Loader", "TEE", "SNP", "Microcode")


# ── TCB parsing (shared by steps() and verify CLI) ──────────────


def _parse_tcb_sections(output: str) -> dict[str, dict[str, str]]:
    """Parse snphost show tcb output into {section: {field: value}}.

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


def _read_platform_tcb() -> dict[str, str]:
    """Read Platform TCB fields from snphost."""
    proc = _run_snphost_tcb()
    if proc.returncode != 0:
        raise RuntimeError(f"snphost show tcb failed: {proc.stderr.strip()}")
    sections = _parse_tcb_sections(proc.stdout)
    if "Platform" not in sections:
        raise RuntimeError("no Platform TCB section in snphost show tcb output")
    return sections["Platform"]


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

    return [
        Step.for_host(
            name="show-tcb",
            type="setup",
            command="snphost show tcb",
        ),
        Step.for_host(
            name="config-set-lower",
            type="required",
            command=_config_set(lo_bl, lo_tee, lo_snp, lo_ucode, fmc, 0),
        ),
        Step.for_callable(
            name="verify-differ after config-set-lower",
            type="required",
            handler="verify_differ",
        ),
        Step.for_host(
            name="config-reset",
            type="required",
            command="snphost config reset",
        ),
        Step.for_callable(
            name="verify-match after config-reset",
            type="required",
            handler="verify_match",
        ),
        Step.for_host(
            name="config-set-mask-chip-id",
            type="required",
            command=_config_set(bl, tee, snp, ucode, fmc, 1),
        ),
        Step.for_host(
            name="config-set-mask-chip-key",
            type="required",
            command=_config_set(bl, tee, snp, ucode, fmc, 2),
        ),
        Step.for_host(
            name="config-set-mask-both",
            type="required",
            command=_config_set(bl, tee, snp, ucode, fmc, 3),
        ),
        Step.for_host(
            name="config-reset-masks",
            type="required",
            command="snphost config reset",
        ),
        Step.for_callable(
            name="verify-match after config-reset-masks",
            type="required",
            handler="verify_match",
        ),
        Step.for_host(
            name="commit",
            type="required",
            command="snphost commit",
        ),
    ]


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("verify-match", "verify-differ"):
        print(f"usage: python3 -m {_THIS_MODULE} verify-match|verify-differ", file=sys.stderr)
        sys.exit(2)
    sys.exit(_verify_cli(sys.argv[1]))
