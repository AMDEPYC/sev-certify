"""id_block_test: Verify ID block acceptance, report field binding, and rejection.

Positive path: launch an SEV-SNP guest with a valid ID block, fetch the
attestation report, and verify that the hardware report reflects the ID block
fields (guest_svn, policy, family_id, image_id).

Negative path: attempt three launches that must fail:
  1. ID block with a corrupted measurement (digest mismatch)
  2. Policy incompatible with the platform (SMT=0 on an SMT-active host)
  3. Impossibly high ABI major version (ABI_MAJOR=255)
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from sev_verify import attestation_report
from sev_verify.cvm_props import (
    DEFAULT_FAMILY_ID,
    DEFAULT_GUEST_SVN,
    DEFAULT_IMAGE_ID,
    DEFAULT_POLICY,
    MeasurementError,
    calculate_measurement,
    generate_id_block,
    read_measurement,
)
from sev_verify.models import BaseStep, Step, StepContext, StepHandlerResult
from sev_verify.vm_profile import VMProfile

vm_profile = VMProfile(
    image_path="",
    memory_mb=2048,
)


# ── Report field verification ─────────────────────────────────────────────────


def verify_id_block_fields(ctx: StepContext) -> StepHandlerResult:
    """Compare ID block fields in the hardware attestation report to expectations.

    Reads report.bin directly (see :mod:`sev_verify.attestation_report`) rather
    than parsing ``snpguest display report`` output, so the check does not
    depend on a CLI's human-readable formatting.
    """
    try:
        report = attestation_report.read(ctx.artifact_dir / "report.bin")
    except attestation_report.ReportError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

    family_id = os.environ.get("ID_BLOCK_FAMILY_ID", DEFAULT_FAMILY_ID)
    image_id = os.environ.get("ID_BLOCK_IMAGE_ID", DEFAULT_IMAGE_ID)
    guest_svn = int(os.environ.get("ID_BLOCK_GUEST_SVN", DEFAULT_GUEST_SVN))
    policy_int = int(os.environ.get("ID_BLOCK_POLICY", DEFAULT_POLICY), 0)

    expected_family = family_id.encode("ascii").ljust(16, b"\x00")
    expected_image = image_id.encode("ascii").ljust(16, b"\x00")

    errors = []
    if report.guest_svn != guest_svn:
        errors.append(f"guest_svn: expected {guest_svn}, got {report.guest_svn}")
    if report.policy != policy_int:
        errors.append(f"policy: expected {hex(policy_int)}, got {hex(report.policy)}")
    if report.family_id != expected_family:
        errors.append(
            f"family_id: expected {expected_family.hex()}, got {report.family_id.hex()}"
        )
    if report.image_id != expected_image:
        errors.append(
            f"image_id: expected {expected_image.hex()}, got {report.image_id.hex()}"
        )
    # An all-zero ID_KEY_DIGEST means the guest launched without an ID block at
    # all. The four comparisons above would then all fail with zeros, which is
    # a confusing way to report "no ID block was used".
    if not report.id_block_used:
        errors.append(
            "id_key_digest is all zero — the guest launched without an ID block"
        )

    if errors:
        return StepHandlerResult(exit_code=1, stderr="\n".join(errors))
    return StepHandlerResult(
        exit_code=0,
        stdout=(
            f"All ID block fields match: svn={guest_svn} policy={hex(policy_int)} "
            f"family_id={family_id!r} image_id={image_id!r}\n"
            f"  report v{report.version} vmpl={report.vmpl} "
            f"cpuid={report.cpuid} tcb=({report.reported_tcb})\n"
            f"  id_key_digest={report.id_key_digest.hex()[:32]}..."
        ),
    )


# ── Negative-test profile mutation helpers ────────────────────────────────────


def _regenerate_id_block(
    ctx: StepContext, measurement: str, policy: str,
) -> StepHandlerResult:
    """Generate a fresh ID block with the given measurement and policy, update ctx.profile.

    ``measurement`` must be in snpguest's input form — 0x-prefixed hex.  An
    unprefixed string is decoded as base64, not hex.
    """
    family_id = os.environ.get("ID_BLOCK_FAMILY_ID", DEFAULT_FAMILY_ID)
    image_id = os.environ.get("ID_BLOCK_IMAGE_ID", DEFAULT_IMAGE_ID)
    guest_svn = os.environ.get("ID_BLOCK_GUEST_SVN", DEFAULT_GUEST_SVN)

    id_key = ec.generate_private_key(ec.SECP384R1())
    auth_key = ec.generate_private_key(ec.SECP384R1())

    id_block_file = ctx.artifact_dir / "neg-id-block.b64"
    id_auth_file = ctx.artifact_dir / "neg-id-auth.b64"

    with tempfile.TemporaryDirectory() as tmpdir:
        id_key_path = Path(tmpdir) / "id-key.pem"
        auth_key_path = Path(tmpdir) / "auth-key.pem"
        id_key_path.write_bytes(
            id_key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
        )
        auth_key_path.write_bytes(
            auth_key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
        )

        result = subprocess.run(
            [
                "snpguest", "generate", "id-block",
                str(id_key_path), str(auth_key_path),
                measurement,
                "--family-id", family_id,
                "--image-id", image_id,
                "--svn", guest_svn,
                "--policy", policy,
                "--id-file", str(id_block_file),
                "--auth-file", str(id_auth_file),
            ],
            capture_output=True, text=True, check=False,
        )

    if result.returncode != 0:
        return StepHandlerResult(
            exit_code=1,
            stderr=f"snpguest generate id-block failed:\n{result.stderr}",
        )

    ctx.profile = replace(
        ctx.profile,
        id_block=id_block_file.read_text().strip(),
        id_auth=id_auth_file.read_text().strip(),
        policy=policy,
    )
    return StepHandlerResult(exit_code=0)


def set_bad_measurement(ctx: StepContext) -> StepHandlerResult:
    """Regenerate the ID block with a corrupted measurement to cause digest mismatch."""
    try:
        real = read_measurement(ctx.artifact_dir)
    except MeasurementError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

    # Flip the first byte of the digest
    flipped_byte = "00" if real[:2].lower() != "00" else "ff"
    flipped = flipped_byte + real[2:]

    policy = os.environ.get("ID_BLOCK_POLICY", DEFAULT_POLICY)
    hr = _regenerate_id_block(ctx, f"0x{flipped}", policy)
    if hr.exit_code != 0:
        return hr
    return StepHandlerResult(
        exit_code=0,
        stdout=f"Set bad measurement: {flipped[:16]}... (real: {real[:16]}...)",
    )


def set_incompatible_policy(ctx: StepContext) -> StepHandlerResult:
    """Regenerate the ID block with a policy the platform cannot satisfy.

    Checks whether SMT is active on the host.  If so, regenerates the ID block
    (and QEMU launch policy) with SMT=0 — the firmware must reject because the
    platform cannot guarantee single-threaded execution.
    """
    smt_path = Path("/sys/devices/system/cpu/smt/active")
    if not smt_path.exists():
        return StepHandlerResult(
            exit_code=1,
            stderr="Cannot determine SMT status: /sys/devices/system/cpu/smt/active not found",
        )
    smt_active = smt_path.read_text().strip() == "1"
    if not smt_active:
        return StepHandlerResult(
            exit_code=1,
            stderr="SMT is not active on this host; cannot test SMT policy incompatibility",
        )

    try:
        measurement = read_measurement(ctx.artifact_dir)
    except MeasurementError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

    policy = os.environ.get("ID_BLOCK_POLICY", DEFAULT_POLICY)
    policy_int = int(policy, 0)
    # Clear SMT bit (16) — guest demands no SMT, but host has SMT active
    incompatible_policy = hex(policy_int & ~(1 << 16))

    hr = _regenerate_id_block(ctx, f"0x{measurement}", incompatible_policy)
    if hr.exit_code != 0:
        return hr
    return StepHandlerResult(
        exit_code=0,
        stdout=f"Set incompatible policy {incompatible_policy} (SMT=0, host SMT active)",
    )


def set_bad_abi_version(ctx: StepContext) -> StepHandlerResult:
    """Regenerate the ID block with an impossibly high ABI major version.

    The policy's ABI_MAJOR field (bits 15:8) specifies the minimum firmware
    ABI version required.  Setting it to 255 guarantees the firmware cannot
    satisfy the requirement on any current platform.
    """
    try:
        measurement = read_measurement(ctx.artifact_dir)
    except MeasurementError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

    policy = os.environ.get("ID_BLOCK_POLICY", DEFAULT_POLICY)
    policy_int = int(policy, 0)
    # Set ABI_MAJOR (bits 15:8) to 255
    bad_policy = (policy_int & ~0xFF00) | (0xFF << 8)
    bad_policy_hex = hex(bad_policy)

    hr = _regenerate_id_block(ctx, f"0x{measurement}", bad_policy_hex)
    if hr.exit_code != 0:
        return hr
    return StepHandlerResult(
        exit_code=0,
        stdout=f"Set policy {bad_policy_hex} (ABI_MAJOR=255)",
    )


# ── Steps ─────────────────────────────────────────────────────────────────────


def steps() -> list[BaseStep]:
    return [
        # ── Positive: launch with valid ID block, verify report fields ──
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
            name="Launch with valid ID block",
            type="setup",
            timeout=300,
        ).add_hint(
            "Address already in use",
            "A previous VM may still be running. "
            "Try: sudo kill $(pgrep -f 'qemu.*guest-cid')",
        ),
        Step.for_guest(
            name="Get attestation report",
            type="required",
            command="snpguest report report.bin request.bin --random",
            timeout=60,
        ),
        Step.for_guest_pull(
            name="Pull attestation report",
            type="required",
            guest_src="report.bin",
            host_dest="report.bin",
            timeout=120,
        ),
        Step.for_vm_stop(
            name="Stop VM",
            type="info",
            timeout=60,
        ),
        Step.for_callable(
            name="Verify ID block fields in report",
            type="required",
            handler="verify_id_block_fields",
            timeout=30,
        ),

        # ── Negative: bad measurement (digest mismatch) ──
        Step.for_callable(
            name="Set bad measurement in ID block",
            type="required",
            handler="set_bad_measurement",
            timeout=30,
        ),
        Step.for_vm_launch(
            name="Launch with bad measurement (expect rejection)",
            type="required",
            expected_result="exit_code:1",
            timeout=300,
        ),
        Step.for_vm_stop(
            name="Stop VM (after bad measurement)",
            type="info",
            timeout=60,
        ),

        # ── Negative: incompatible policy (SMT=0 on SMT-active host) ──
        Step.for_callable(
            name="Set incompatible policy (SMT)",
            type="required",
            handler="set_incompatible_policy",
            timeout=30,
        ),
        Step.for_vm_launch(
            name="Launch with SMT-incompatible policy (expect rejection)",
            type="required",
            expected_result="exit_code:1",
            timeout=300,
        ),
        Step.for_vm_stop(
            name="Stop VM (after SMT policy)",
            type="info",
            timeout=60,
        ),

        # ── Negative: impossible ABI version ──
        Step.for_callable(
            name="Set impossible ABI version",
            type="required",
            handler="set_bad_abi_version",
            timeout=30,
        ),
        Step.for_vm_launch(
            name="Launch with impossible ABI version (expect rejection)",
            type="required",
            expected_result="exit_code:1",
            timeout=300,
        ),
        Step.for_vm_stop(
            name="Stop VM (after ABI version)",
            type="info",
            timeout=60,
        ),
    ]
