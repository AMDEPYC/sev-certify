"""key_derivation_test: Launch SEV-SNP guest and run key derivation tests.

Verifies that the SNP MSG_KEY_REQ firmware command produces correct and
consistent derived keys. The guest-side script exercises:
  - Deterministic key generation (same params -> same key)
  - VMPL-based key isolation (different VMPL -> different keys)
  - Root key differences (VCEK vs VMRK)
  - Guest SVN sensitivity and ID-block bound enforcement
  - TCB version sensitivity and committed-TCB bound enforcement
  - Guest Field Select (GFS) sensitivity and per-bit field mixing
"""

from sev_verify.models import BaseStep, Step
from sev_verify.vm_profile import VMProfile

vm_profile = VMProfile(
    image_path="",
    memory_mb=2048,
)

_KEY_DERIVATION_SCRIPT = "/usr/local/lib/scripts/snpguest_key_derivation.py"


def steps() -> list[BaseStep]:
    return [
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
