"""Shared callables for ID block generation in sev_verify test modules.

A test module that requires an ID block includes these steps in its steps()
list, in order, before vm_launch:

    Step.for_callable(name="Calculate measurement", type="setup",
                      handler="calculate_measurement", timeout=60),
    Step.for_callable(name="Generate ID block", type="setup",
                      handler="generate_id_block", timeout=30),

The calculate_measurement step writes guest_measurement.txt to ctx.artifact_dir.
The generate_id_block step reads it, generates ephemeral P-384 key pairs, calls
snpguest to produce id-block.b64 and id-auth.b64, and updates ctx.profile so
that the subsequent vm_launch step passes the ID block to QEMU.

Both steps follow the additive principle: if OVMF is absent (no measurement
possible), calculate_measurement returns a non-zero exit code and — because it
is typed "setup" — the remaining steps are skipped cleanly.
"""

from __future__ import annotations

import string
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

# may need to change this library
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from .models import StepContext, StepHandlerResult
from .vm_profile import VMProfile, VMProfileError

_MEASUREMENT_FILE = "guest_measurement.txt"
_ID_BLOCK_FILE = "id-block.b64"
_ID_AUTH_FILE = "id-auth.b64"

DEFAULT_FAMILY_ID = "sev-certify-fam0"
DEFAULT_IMAGE_ID = "sev-certify-img0"
DEFAULT_GUEST_SVN = "48"
DEFAULT_POLICY = "0xb0000"

# The SNP attestation report MEASUREMENT field is 48 bytes, so the hex form is
# 96 characters.  Fixed by the SNP spec, not by configuration.
MEASUREMENT_HEX_LEN = 96


class MeasurementError(Exception):
    """Base class for problems reading guest_measurement.txt."""


class MeasurementMissing(MeasurementError):
    """guest_measurement.txt does not exist."""


class MeasurementMalformed(MeasurementError):
    """guest_measurement.txt exists but does not hold a 48-byte hex digest."""


def read_measurement(artifact_dir: Path) -> str:
    """Read guest_measurement.txt and return the bare (unprefixed) hex digest.

    Validation lives here, at the point of use, rather than in
    calculate_measurement.  A check at write time says nothing about what a
    later step is about to read: the steps are separated in time, so the file
    can change (or be replaced) in between.

    snpguest writes the digest 0x-prefixed under ``--output-format hex``.  The
    prefix is stripped here so callers operate on a bare body; re-add it with
    ``f"0x{...}"`` when handing the value back to snpguest, which decodes an
    unprefixed string as base64 rather than hex.

    Raises:
        MeasurementMissing: the file is absent.
        MeasurementMalformed: the file is present but not a 48-byte hex digest.
    """
    measurement_file = artifact_dir / _MEASUREMENT_FILE
    try:
        raw = measurement_file.read_text().strip()
    except FileNotFoundError as exc:
        raise MeasurementMissing(f"{_MEASUREMENT_FILE} not found") from exc

    body = raw[2:] if raw[:2].lower() == "0x" else raw
    if len(body) != MEASUREMENT_HEX_LEN:
        raise MeasurementMalformed(
            f"{_MEASUREMENT_FILE}: expected a {MEASUREMENT_HEX_LEN}-character hex "
            f"digest (48 bytes), got {len(body)} characters"
        )
    if not all(c in string.hexdigits for c in body):
        raise MeasurementMalformed(
            f"{_MEASUREMENT_FILE}: contains non-hex characters"
        )
    return body


def calculate_measurement(ctx: StepContext) -> StepHandlerResult:
    """Calculate the expected guest launch measurement via snpguest.

    Resolves the OVMF path from ctx.profile, runs snpguest generate
    measurement against the guest image, and writes the result to
    guest_measurement.txt in ctx.artifact_dir.
    """
    try:
        ovmf_path = Path(ctx.profile.resolved_ovmf_path())
    except VMProfileError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

    measurement_file = ctx.artifact_dir / _MEASUREMENT_FILE
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
    measurement = measurement_file.read_text().strip()
    return StepHandlerResult(
        exit_code=0,
        stdout=f"Measurement: {measurement}",
    )


def generate_id_block(ctx: StepContext) -> StepHandlerResult:
    """Generate an ID block and auth block for the current guest measurement.

    Reads guest_measurement.txt from ctx.artifact_dir (written by
    calculate_measurement). Generates two ephemeral P-384 key pairs, invokes
    snpguest generate id-block, and updates ctx.profile with the resulting
    id_block and id_auth values so that vm_launch passes them to QEMU.

    ID block metadata is read from environment variables with the same defaults
    used by the generate-id-block systemd service:
      ID_BLOCK_FAMILY_ID, ID_BLOCK_IMAGE_ID, ID_BLOCK_GUEST_SVN, ID_BLOCK_POLICY

    If guest_measurement.txt is absent (calculate_measurement was skipped or
    failed), this step exits 0 and leaves ctx.profile unchanged, so vm_launch
    proceeds without an ID block.

    A file that is present but malformed is a different case and fails the
    step: absence is an expected configuration, corruption is not.
    """
    import os

    try:
        measurement = read_measurement(ctx.artifact_dir)
    except MeasurementMissing as exc:
        return StepHandlerResult(
            exit_code=0,
            stdout=f"INFO: {exc} — skipping ID block generation",
        )
    except MeasurementMalformed as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

    family_id = os.environ.get("ID_BLOCK_FAMILY_ID", DEFAULT_FAMILY_ID)
    image_id = os.environ.get("ID_BLOCK_IMAGE_ID", DEFAULT_IMAGE_ID)
    guest_svn = os.environ.get("ID_BLOCK_GUEST_SVN", DEFAULT_GUEST_SVN)
    policy = os.environ.get("ID_BLOCK_POLICY", DEFAULT_POLICY)

    id_key = ec.generate_private_key(ec.SECP384R1())
    auth_key = ec.generate_private_key(ec.SECP384R1())

    id_block_file = ctx.artifact_dir / _ID_BLOCK_FILE
    id_auth_file = ctx.artifact_dir / _ID_AUTH_FILE

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
                str(id_key_path),
                str(auth_key_path),
                f"0x{measurement}",
                "--family-id", family_id,
                "--image-id", image_id,
                "--svn", guest_svn,
                "--policy", policy,
                "--id-file", str(id_block_file),
                "--auth-file", str(id_auth_file),
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

    id_block_b64 = id_block_file.read_text().strip()
    id_auth_b64 = id_auth_file.read_text().strip()

    ctx.profile = replace(ctx.profile, id_block=id_block_b64, id_auth=id_auth_b64, policy=policy)

    return StepHandlerResult(
        exit_code=0,
        stdout=(
            f"Generated ID block for measurement {measurement[:16]}...\n"
            f"  family_id={family_id} image_id={image_id} svn={guest_svn} policy={policy}"
        ),
    )
