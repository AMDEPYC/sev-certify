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
    IdBlockMetadataError,
    MeasurementError,
    calculate_measurement,
    generate_id_block,
    read_id_block_metadata,
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

    The host's processor generation is passed in so the parser can cross-check
    it against the CPUID the report carries, and so TCB_VERSION — whose byte
    layout differs by generation — is never decoded on a guess.
    """
    try:
        report = attestation_report.read(
            ctx.artifact_dir / "report.bin",
            generation=attestation_report.host_generation(),
        )
    except attestation_report.ReportError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

    # Read through the same helper the generator used, so the expectations here
    # cannot drift from the values the ID block was actually built with.
    try:
        meta = read_id_block_metadata()
    except IdBlockMetadataError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

    errors = []
    if report.guest_svn != meta.guest_svn:
        errors.append(f"guest_svn: expected {meta.guest_svn}, got {report.guest_svn}")
    if report.policy != meta.policy:
        errors.append(
            f"policy: expected {hex(meta.policy)}, got {hex(report.policy)}"
        )
    if report.family_id != meta.family_id_bytes:
        errors.append(
            f"family_id: expected {meta.family_id_bytes.hex()}, "
            f"got {report.family_id.hex()}"
        )
    if report.image_id != meta.image_id_bytes:
        errors.append(
            f"image_id: expected {meta.image_id_bytes.hex()}, "
            f"got {report.image_id.hex()}"
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
            f"All ID block fields match: svn={meta.guest_svn} "
            f"policy={hex(meta.policy)} family_id={meta.family_id!r} "
            f"image_id={meta.image_id!r}\n"
            f"  report v{report.version} vmpl={report.vmpl} "
            f"cpuid={report.cpuid} gen={report.generation} "
            f"tcb=({report.reported_tcb})\n"
            f"  id_key_digest={report.id_key_digest.hex()[:32]}..."
        ),
    )


# ── Negative-test profile mutation helpers ────────────────────────────────────


def _regenerate_id_block(
    ctx: StepContext, measurement: str, policy: int,
) -> StepHandlerResult:
    """Generate a fresh ID block with the given measurement and policy, update ctx.profile.

    ``measurement`` must be in snpguest's input form — 0x-prefixed hex.  An
    unprefixed string is decoded as base64, not hex.

    Only the policy varies between the negative cases; the identifying fields
    come from the same validated source the original ID block was built from.
    """
    try:
        meta = read_id_block_metadata()
    except IdBlockMetadataError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

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
                "--family-id", meta.family_id,
                "--image-id", meta.image_id,
                "--svn", str(meta.guest_svn),
                "--policy", hex(policy),
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

    try:
        meta = read_id_block_metadata()
    except IdBlockMetadataError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

    # Flip the first byte of the digest
    flipped_byte = "00" if real[:2].lower() != "00" else "ff"
    flipped = flipped_byte + real[2:]

    hr = _regenerate_id_block(ctx, f"0x{flipped}", meta.policy)
    if hr.exit_code != 0:
        return hr
    return StepHandlerResult(
        exit_code=0,
        stdout=f"Set bad measurement: {flipped[:16]}... (real: {real[:16]}...)",
    )


_SMT_ACTIVE = Path("/sys/devices/system/cpu/smt/active")
_SMT_CONTROL = Path("/sys/devices/system/cpu/smt/control")

#: smt/control values that explain *why* SMT is inactive.  The first two mean it
#: was switched off, the last two that the capability is absent — a distinction
#: worth preserving in the report, since only the former could have been on.
_SMT_CONTROL_REASONS = {
    "off": "SMT is disabled on this host",
    "forceoff": "SMT is force-disabled and cannot be re-enabled without a reboot",
    "notsupported": "this processor does not support SMT",
    "notimplemented": "this architecture does not implement SMT runtime control",
}


def _read_sysfs(path: Path) -> str | None:
    """Return the stripped contents of *path*, or None if it cannot be read."""
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _smt_status() -> tuple[bool, str]:
    """Return whether host SMT is active, with a reason suitable for reporting.

    ``smt/active`` decides: it reports whether sibling threads are online right
    now.  ``smt/control`` is consulted only to explain why they are not, and is
    deliberately not used as the decision — besides the four names above it can
    also read as a thread count on architectures with partial SMT states.
    """
    active = _read_sysfs(_SMT_ACTIVE)
    control = _read_sysfs(_SMT_CONTROL)

    if active == "1":
        return True, "SMT is active on this host"

    reason = _SMT_CONTROL_REASONS.get(control or "")
    if reason is not None:
        return False, reason
    if active is None:
        return False, (
            f"{_SMT_ACTIVE} is not present, so SMT state cannot be determined"
        )
    return False, "SMT is not active on this host"


def smt_case_not_applicable(ctx: StepContext) -> StepHandlerResult:
    """Record that the SMT policy case was left out of this run.

    Emitted in place of the SMT steps when the host cannot exercise them, so
    that the omission appears in the results rather than the case simply being
    absent.
    """
    _, reason = _smt_status()
    return StepHandlerResult(
        exit_code=0,
        stdout=f"SMT policy rejection case not run: {reason}",
    )


def set_incompatible_policy(ctx: StepContext) -> StepHandlerResult:
    """Regenerate the ID block with a policy the platform cannot satisfy.

    Regenerates the ID block (and QEMU launch policy) with SMT=0 — the firmware
    must reject because the platform cannot guarantee single-threaded execution.

    Only reached on an SMT-active host; steps() omits this case otherwise.  The
    check is repeated here so the handler is correct on its own terms rather
    than relying on the caller.
    """
    smt_active, reason = _smt_status()
    if not smt_active:
        return StepHandlerResult(
            exit_code=1,
            stderr=f"Cannot test SMT policy incompatibility: {reason}",
        )

    try:
        measurement = read_measurement(ctx.artifact_dir)
    except MeasurementError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

    try:
        meta = read_id_block_metadata()
    except IdBlockMetadataError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

    # Clear SMT bit (16) — guest demands no SMT, but host has SMT active
    incompatible_policy = meta.policy & ~(1 << 16)

    hr = _regenerate_id_block(ctx, f"0x{measurement}", incompatible_policy)
    if hr.exit_code != 0:
        return hr
    return StepHandlerResult(
        exit_code=0,
        stdout=f"Set incompatible policy {hex(incompatible_policy)} (SMT=0, host SMT active)",
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

    try:
        meta = read_id_block_metadata()
    except IdBlockMetadataError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

    # Set ABI_MAJOR (bits 15:8) to 255
    bad_policy = (meta.policy & ~0xFF00) | (0xFF << 8)

    hr = _regenerate_id_block(ctx, f"0x{measurement}", bad_policy)
    if hr.exit_code != 0:
        return hr
    return StepHandlerResult(
        exit_code=0,
        stdout=f"Set policy {hex(bad_policy)} (ABI_MAJOR=255)",
    )


# ── Steps ─────────────────────────────────────────────────────────────────────


def steps() -> list[BaseStep]:
    smt_active, _ = _smt_status()

    steps_list: list[BaseStep] = [
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
            # Assert the firmware's own reason, not merely that something
            # failed: exit_code:1 alone is also satisfied by a boot timeout, so
            # it cannot distinguish a real rejection from a hung guest.
            expected_result="stdout_contains:Bad measurement",
            timeout=300,
        ),
        Step.for_vm_stop(
            name="Stop VM (after bad measurement)",
            type="info",
            timeout=60,
        ),
    ]

    # ── Negative: incompatible policy (SMT=0 on SMT-active host) ──
    #
    # Clearing the SMT bit only produces a rejection on a host where SMT is
    # actually active, so elsewhere there is nothing to assert.  The case is
    # left out of the step list rather than run and failed, with an info step
    # in its place: a case that vanishes silently is indistinguishable from one
    # that passed.  Reporting it as "pass" does overload that outcome — a
    # first-class per-step "not applicable on this platform" result would say
    # so plainly, and is the better home for this once one exists.
    if smt_active:
        steps_list += [
            Step.for_callable(
                name="Set incompatible policy (SMT)",
                type="required",
                handler="set_incompatible_policy",
                timeout=30,
            ),
            Step.for_vm_launch(
                name="Launch with SMT-incompatible policy (expect rejection)",
                type="required",
                # This one is refused by KVM before the firmware sees it
                # (SNP_LAUNCH_START ret=-22 fw_error=0 ''), so there is no
                # firmware string to match. Assert the rejection happened at
                # launch-start, which still rules out a boot timeout.
                expected_result="stdout_contains:SNP_LAUNCH_START",
                timeout=300,
            ),
            Step.for_vm_stop(
                name="Stop VM (after SMT policy)",
                type="info",
                timeout=60,
            ),
        ]
    else:
        steps_list.append(
            Step.for_callable(
                name="SMT policy case not applicable",
                type="info",
                handler="smt_case_not_applicable",
                timeout=10,
            )
        )

    steps_list += [
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
            # Firmware rejects this one: SNP_LAUNCH_START fw_error=7.
            expected_result="stdout_contains:Policy is not allowed",
            timeout=300,
        ),
        Step.for_vm_stop(
            name="Stop VM (after ABI version)",
            type="info",
            timeout=60,
        ),
    ]

    return steps_list
