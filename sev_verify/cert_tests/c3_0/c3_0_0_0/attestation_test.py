"""attestation_test: Launch SEV-SNP guest and verify attestation report.

Guest steps run on the VM over AF_VSOCK (see :mod:`sev_verify.guest_vsock`).

**QEMU and OVMF paths** — Do not hard-code them in :func:`vm_profile` unless a
test needs a fixed pin. Prefer running the harness with global overrides::

    python3 -m sev_verify GUEST.EFI --qemu-binary /path/to/qemu-system-x86_64 \\
        --ovmf /path/to/OVMF.fd -v 3.0.0-0

Those flags override ``VMProfile.qemu_binary`` and ``VMProfile.ovmf_path`` after
``vm_profile()`` is merged. Callable steps can read the same paths from
:class:`~sev_verify.models.StepContext` ``cli_qemu_binary`` / ``cli_ovmf_path``.

The CLI ``path_to_guest`` always sets the bootable guest image (``image_path``).
Pulled files and analysis output for this test go under ``ctx.artifact_dir``
(e.g. ``./artifacts/3.0/3.0.0-0/vm_launch_attest/``).
"""

import subprocess
from pathlib import Path

from sev_verify.models import BaseStep, Step, StepContext, StepHandlerResult
from sev_verify.vm_profile import VMProfile, DEFAULT_OVMF_CANDIDATES

vm_profile = VMProfile(
    image_path="",
    memory_mb=2048,
)

def calculate_measurement(ctx: StepContext) -> StepHandlerResult:
    """
    Calculate expected measurement using ``snpguest generate measurement``.

    Searches for an AMD SEV-compatible OVMF binary and runs snpguest to
    produce a hex measurement of the guest image, stored in
    ``ctx.expected_measurement`` for later attestation comparison.
    """
    measurement_file = ctx.artifact_dir / "guest_measurement.txt"
    ovmf_path = None

    if ctx.cli_ovmf_path:
        o = Path(ctx.cli_ovmf_path)
        if not o.is_file():
            return StepHandlerResult(
                exit_code=1,
                stderr=f"CLI OVMF image not found: {o}",
            )
        ovmf_path = o
    else:
        for path in DEFAULT_OVMF_CANDIDATES:
            if Path(path).is_file():
                ovmf_path = path

    if not ovmf_path:
        return StepHandlerResult(
            exit_code=1,
            stderr=(
                "No OVMF found. Install an AMD SEV OVMF package or pass --ovmf PATH.\n"
                "Checked:\n" + "\n".join(DEFAULT_OVMF_CANDIDATES)
            ))

    result = subprocess.run(
        [
            "snpguest", "generate", "measurement",
            "--vcpu-type", "EPYC-v4",
            "--ovmf", str(ovmf_path),
            "--kernel", str(ctx.guest_path),
            "--output-format", "hex",
            "--measurement-file", str(measurement_file),
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

    expected_measurement = measurement_file.read_text().strip()
    return StepHandlerResult(
        exit_code=0,
        stdout=f"Calculated expected measurement: {expected_measurement}",
    )


def verify_report_fields(ctx: StepContext) -> StepHandlerResult:
    """
    Example callable step: validate ``report.bin`` after ``guest_pull``.

    Replace with real parsing (e.g. ``snpguest`` / ASN.1) and compare fields
    to values computed in earlier ``callable`` or ``host`` steps.
    """
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
    '''
    Steps to test basic launch and attestation for SEV-SNP guest

    1. Calculate expected measurement
    2. Launch VM
    3. Request report
    4. Fetch certificates
    5. Verify certificate chain
    6. Verify attestation report signature
    7. Verify attestation report fields (measurement and report data)
    '''
    return [
        Step.for_callable(
            name="Calculate measurement",
            type="setup",
            handler="calculate_measurement",
            timeout=60,
        ),
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
            name="Verify Request data and Measurement",
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
