"""Derived keys test — ID block placeholder.

This module establishes the certification level and step structure for derived
keys testing. Derived key operations require non-zero family_id, image_id, and
guest_svn in the ID block so that GFS bit bindings are exercised non-trivially.

The guest step (snpguest ok) is a placeholder. It will be replaced with the
actual derived key test steps once that work is ready.

The calculate_measurement and generate_id_block callables are imported from
sev_verify.id_block, which is the shared location for any test that requires
an ID block.
"""

from sev_verify.id_block import calculate_measurement, generate_id_block
from sev_verify.models import BaseStep, Step
from sev_verify.vm_profile import VMProfile

vm_profile = VMProfile(
    image_path="",
    memory_mb=2048,
)


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
            name="Launch SEV-SNP guest with ID block",
            type="setup",
            timeout=300,
        ),
        Step.for_guest(
            name="Verify SNP guest (derived keys placeholder)",
            type="required",
            command="snpguest ok",
            timeout=60,
        ),
        Step.for_vm_stop(
            name="Stop VM",
            type="info",
            timeout=60,
        ),
    ]
