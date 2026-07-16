"""key_derivation_test: Launch SEV-SNP guest and run key derivation tests.

Verifies that the SNP MSG_KEY_REQ firmware command produces correct and
consistent derived keys. The guest-side script exercises:
  - Deterministic key generation (same params -> same key)
  - VMPL-based key isolation (different VMPL -> different keys)
  - Root key differences (VCEK vs VMRK)
  - Guest SVN sensitivity and ID-block bound enforcement
  - TCB version sensitivity and committed-TCB bound enforcement
  - Guest Field Select (GFS) sensitivity and per-bit field mixing

Additionally, cross-CVM determinism is verified: a key derived in the
first CVM matches the same key derived in a second independent CVM on
the same platform, proving the key is bound to platform identity rather
than transient VM state.

When sev_verify.id_block is available (from the ID block PR), the guest
is launched with an ID block, giving the key derivation tests richer
coverage (non-zero guest SVN, family_id, image_id).
"""

from pathlib import Path

from sev_verify.cert_tests.c3_0.c3_0_0_0.attestation_test import calculate_measurement  # noqa: F401
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

_KEY_DERIVATION_SCRIPT = "/usr/local/lib/scripts/snpguest_key_derivation.py"
_CROSS_CVM_KEY_FILE = "cross_cvm_key.bin"


def compare_cross_cvm_keys(ctx: StepContext) -> StepHandlerResult:
    """Compare keys derived in two independent CVMs.

    Reads cross_cvm_key_1.bin and cross_cvm_key_2.bin from artifact_dir
    and verifies they are identical, proving platform-bound determinism
    across separate CVM lifetimes.
    """
    key1 = (ctx.artifact_dir / "cross_cvm_key_1.bin").read_bytes()
    key2 = (ctx.artifact_dir / "cross_cvm_key_2.bin").read_bytes()

    if key1 == key2:
        return StepHandlerResult(
            exit_code=0,
            stdout="Keys match across two independent CVMs — platform-bound derivation confirmed",
        )
    return StepHandlerResult(
        exit_code=1,
        stderr="Keys differ across CVMs — derived key is not stable across CVM lifetimes",
    )


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
        pre.append(
            Step.for_callable(
                name="Generate ID block",
                type="setup",
                handler="generate_id_block",
                timeout=30,
            ),
        )
    launch_hint = ("Address already in use",
                   "A previous VM may still be running. "
                   "Try: sudo kill $(pgrep -f 'qemu.*guest-cid')")
    return pre + [
        # ── First CVM ────────────────────────────────────────────────────────
        Step.for_vm_launch(
            name="Launch first CVM",
            type="setup",
            timeout=300,
        ).add_hint(*launch_hint),
        Step.for_guest(
            name="Run key derivation tests",
            type="required",
            command=f"python3 {_KEY_DERIVATION_SCRIPT}",
            timeout=600,
        ),
        Step.for_guest(
            name="Derive cross-CVM reference key (CVM 1)",
            type="required",
            command=f"snpguest key {_CROSS_CVM_KEY_FILE} vcek --vmpl 0",
            timeout=30,
        ),
        Step.for_guest_pull(
            name="Pull reference key from CVM 1",
            type="required",
            guest_src=_CROSS_CVM_KEY_FILE,
            host_dest="cross_cvm_key_1.bin",
            timeout=30,
        ),
        Step.for_vm_stop(
            name="Stop first CVM",
            type="info",
            timeout=60,
        ),
        # ── Second CVM ───────────────────────────────────────────────────────
        Step.for_vm_launch(
            name="Launch second CVM",
            type="setup",
            timeout=300,
        ).add_hint(*launch_hint),
        Step.for_guest(
            name="Derive cross-CVM reference key (CVM 2)",
            type="required",
            command=f"snpguest key {_CROSS_CVM_KEY_FILE} vcek --vmpl 0",
            timeout=30,
        ),
        Step.for_guest_pull(
            name="Pull reference key from CVM 2",
            type="required",
            guest_src=_CROSS_CVM_KEY_FILE,
            host_dest="cross_cvm_key_2.bin",
            timeout=30,
        ),
        Step.for_vm_stop(
            name="Stop second CVM",
            type="info",
            timeout=60,
        ),
        # ── Compare ──────────────────────────────────────────────────────────
        Step.for_callable(
            name="Compare keys across CVMs",
            type="required",
            handler="compare_cross_cvm_keys",
            timeout=10,
        ),
    ]
