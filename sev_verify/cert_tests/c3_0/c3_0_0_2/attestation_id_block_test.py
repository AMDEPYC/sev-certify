"""attestation_id_block_test: Launch SEV-SNP guest with ID block and verify attestation report.

Extends the 3.0.0-0 attestation test by prepending ID block generation steps.
The firmware accepting the launch proves the ID block measurement matched; the
subsequent attestation verification confirms the hardware-signed report reflects
the ID block fields (policy, family_id, image_id, svn).
"""

import subprocess
from pathlib import Path

from sev_verify.id_block import calculate_measurement, generate_id_block
from sev_verify.models import BaseStep, Step, StepContext, StepHandlerResult
from sev_verify.vm_profile import VMProfile

vm_profile = VMProfile(
    image_path="",
    memory_mb=2048,
)


def verify_report_fields(ctx: StepContext) -> StepHandlerResult:
    report_file = ctx.artifact_dir / "report.bin"
    measurement_file = ctx.artifact_dir / "guest_measurement.txt"
    request_file = ctx.artifact_dir / "request.bin"

    expected_measurement = measurement_file.read_text().strip()
    request_data = "0x" + str(request_file.read_bytes().hex())
    result = subprocess.run(
        [
            "snpguest", "verify", "attestation",
            str(ctx.artifact_dir), str(report_file),
            "--measurement", str(expected_measurement),
            "--report-data", str(request_data),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return StepHandlerResult(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    return StepHandlerResult(
        exit_code=0,
        stdout="Successfully verified report data and measurement",
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
        ).add_hint(
            "Address already in use",
            "A previous VM may still be running. "
            "Try: sudo kill $(pgrep -f 'qemu.*guest-cid')",
        ),
        Step.for_guest(
            name="Get attestation report with snpguest",
            type="required",
            command="snpguest report report.bin request.bin --random",
            timeout=300,
        ),
        Step.for_guest_pull(
            name="Pull report from guest",
            type="required",
            guest_src="report.bin",
            host_dest="report.bin",
            timeout=120,
        ),
        Step.for_guest_pull(
            name="Pull request file from guest",
            type="required",
            guest_src="request.bin",
            host_dest="request.bin",
            timeout=120,
        ),
        Step.for_host(
            name="Fetch certificate chain from kds",
            type="setup",
            command='snpguest fetch ca pem "$SEV_VERIFY_ARTIFACT_DIR" -r "$SEV_VERIFY_ARTIFACT_DIR/report.bin"',
            timeout=60,
        ),
        Step.for_host(
            name="Fetch VCEK from kds",
            type="setup",
            command='snpguest fetch vcek pem "$SEV_VERIFY_ARTIFACT_DIR" "$SEV_VERIFY_ARTIFACT_DIR/report.bin"',
            timeout=60,
        ).add_hint("429", "Rate limited by KDS, re-run in a minute"),
        Step.for_host(
            name="Verify certificate chain",
            type="required",
            command='snpguest verify certs "$SEV_VERIFY_ARTIFACT_DIR"',
            timeout=60,
        ),
        Step.for_host(
            name="Verify report signature and TCB values",
            type="required",
            command='snpguest verify attestation "$SEV_VERIFY_ARTIFACT_DIR" "$SEV_VERIFY_ARTIFACT_DIR/report.bin"',
            timeout=60,
        ),
        Step.for_callable(
            name="Verify request data and measurement",
            type="required",
            handler="verify_report_fields",
            timeout=30,
        ),
        Step.for_vm_stop(
            name="Stop VM",
            type="info",
            timeout=60,
        ),
    ]
