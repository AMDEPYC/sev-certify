"""key_derivation_test: Launch SEV-SNP guest and run key derivation tests.

Verifies that the SNP MSG_KEY_REQ firmware command produces correct and
consistent derived keys. The guest-side script exercises:
  - Deterministic key generation (same params -> same key)
  - VMPL-based key isolation (different VMPL -> different keys)
  - Root key differences (VCEK vs VMRK)
  - Guest SVN sensitivity and ID-block bound enforcement
  - TCB version sensitivity and committed-TCB bound enforcement
  - Guest Field Select (GFS) sensitivity and per-bit field mixing

When sev_verify.id_block is available (from the ID block PR), the guest
is launched with an ID block, giving the key derivation tests richer
coverage (non-zero guest SVN, family_id, image_id).
"""

from sev_verify.cert_tests.c3_0.c3_0_0_0.attestation_test import calculate_measurement  # noqa: F401
from sev_verify.models import BaseStep, Step
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
        Step.for_guest(
            name="Run key derivation tests",
            type="required",
            command=f"python3 {_KEY_DERIVATION_SCRIPT}",
            timeout=600,
        ),
        Step.for_vm_stop(
            name="Stop VM",
            type="info",
            timeout=60,
        ),
    ]
