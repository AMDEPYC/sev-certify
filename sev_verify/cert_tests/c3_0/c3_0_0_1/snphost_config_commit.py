"""snphost config/commit: Test SNP_CONFIG and SNP_COMMIT host commands.

Exercises snphost config set/reset and commit against the platform's
SEV-SNP firmware via /dev/sev.  All steps are host-side.

TCB values are read from ``snphost show tcb`` at step-definition time
so each command is a concrete ``snphost config set <args>`` invocation.

Steps that modify config verify the change by invoking this module as a
script:  ``python3 -m <this_module> verify-match|verify-differ``
"""

import re
import subprocess
import sys

from sev_verify.models import Step

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


# ── Verify CLI (called from step commands) ──────────────────────


def _verify(mode: str) -> int:
    """Compare Reported vs Platform TCB. Returns 0 on success."""
    proc = _run_snphost_tcb()
    if proc.returncode != 0:
        print(f"snphost show tcb failed: {proc.stderr.strip()}", file=sys.stderr)
        return 1

    sections = _parse_tcb_sections(proc.stdout)
    reported = sections.get("Reported", {})
    platform = sections.get("Platform", {})

    match = all(reported.get(f) == platform.get(f) for f in _CORE_TCB_FIELDS)

    if mode == "verify-match" and not match:
        print("FAIL: Reported TCB should match Platform after reset", file=sys.stderr)
        print(f"  Reported: {reported}", file=sys.stderr)
        print(f"  Platform: {platform}", file=sys.stderr)
        return 1
    if mode == "verify-differ" and match:
        print("FAIL: Reported TCB should differ from Platform after config set", file=sys.stderr)
        return 1
    return 0


# ── Step definitions ────────────────────────────────────────────


def _config_set(bl: int, tee: int, snp: int, ucode: int,
                fmc: int | None, mask: int) -> str:
    """Build a ``snphost config set`` command string."""
    args = f"{bl} {tee} {snp} {ucode} {mask}"
    if fmc is not None:
        args += f" {fmc}"
    return f"snphost config set {args}"


def _verify_cmd(mode: str) -> str:
    return f"python3 -m {_THIS_MODULE} {mode}"


def steps() -> list[Step]:
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
        Step(name="show-tcb", type="setup", kind="host",
             command="snphost show tcb",
             expected_result="exit_code:0", timeout=10),
        Step(name="config-set-lower", type="required", kind="host",
             command=f"{_config_set(lo_bl, lo_tee, lo_snp, lo_ucode, fmc, 0)} && {_verify_cmd('verify-differ')}",
             expected_result="exit_code:0", timeout=10),
        Step(name="config-reset", type="required", kind="host",
             command=f"snphost config reset && {_verify_cmd('verify-match')}",
             expected_result="exit_code:0", timeout=10),
        Step(name="config-set-mask-chip-id", type="required", kind="host",
             command=_config_set(bl, tee, snp, ucode, fmc, 1),
             expected_result="exit_code:0", timeout=10),
        Step(name="config-set-mask-chip-key", type="required", kind="host",
             command=_config_set(bl, tee, snp, ucode, fmc, 2),
             expected_result="exit_code:0", timeout=10),
        Step(name="config-set-mask-both", type="required", kind="host",
             command=_config_set(bl, tee, snp, ucode, fmc, 3),
             expected_result="exit_code:0", timeout=10),
        Step(name="config-reset-masks", type="required", kind="host",
             command=f"snphost config reset && {_verify_cmd('verify-match')}",
             expected_result="exit_code:0", timeout=10),
        Step(name="commit", type="required", kind="host",
             command="snphost commit",
             expected_result="exit_code:0", timeout=10),
    ]


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("verify-match", "verify-differ"):
        print(f"usage: python3 -m {_THIS_MODULE} verify-match|verify-differ", file=sys.stderr)
        sys.exit(2)
    sys.exit(_verify(sys.argv[1]))
