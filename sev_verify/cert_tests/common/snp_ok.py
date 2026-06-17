"""snp-ok: Verify SNP is enabled and functional on the host and the right components are present.

OVMF is checked via ``--ovmf`` when passed to the harness, otherwise common
distro paths (see :attr:`sev_verify.models.StepContext.cli_ovmf_path`).
"""

from pathlib import Path

from sev_verify.models import Step, StepContext, StepHandlerResult
from sev_verify.vm_profile import DEFAULT_OVMF_CANDIDATES


def verify_ovmf(ctx: StepContext) -> StepHandlerResult:
    """Require ``--ovmf`` file to exist, or find OVMF under default host paths."""
    if ctx.cli_ovmf_path:
        o = Path(ctx.cli_ovmf_path)
        if not o.is_file():
            return StepHandlerResult(
                exit_code=1,
                stderr=f"CLI OVMF image not found: {o}",
            )
        return StepHandlerResult(exit_code=0, stdout=f"CLI OVMF: {o}")

    for path in DEFAULT_OVMF_CANDIDATES:
        if Path(path).is_file():
            return StepHandlerResult(
                exit_code=0,
                stdout=f"OVMF on host: {path} (override with --ovmf if needed)",
            )

    return StepHandlerResult(
        exit_code=1,
        stderr=(
            "No OVMF found. Install an AMD SEV OVMF package or pass --ovmf PATH.\n"
            "Checked:\n" + "\n".join(DEFAULT_OVMF_CANDIDATES)
        ),
    )


def steps() -> list[Step]:
    '''
    Steps to verify SNP is enabled correctly in the system
    1. Run snphost ok
    2. Show any guests
    3. Verify a valid OVMF is present.
    '''
    return [
        Step(
            name="snphost ok",
            type="required",
            kind="host",
            command="snphost ok",
            expected_result="exit_code:0",
            timeout=30,
        ),
        Step(
            name="snphost show guests",
            type="info",
            kind="host",
            command="snphost show guests",
            expected_result="exit_code:0",
            timeout=10,
        ),
        Step(
            name="verify OVMF",
            type="info",
            kind="callable",
            handler="verify_ovmf",
            expected_result="exit_code:0",
            timeout=10,
        ),
    ]
