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

import os
import string
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from .models import StepContext, StepHandlerResult
# from .vm_profile import VMProfile, VMProfileError
from .vm_profile import VMProfileError

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

# FAMILY_ID and IMAGE_ID are 16-byte fields in both the ID block and the
# attestation report.  Fixed by the SNP spec, not by configuration.
ID_FIELD_SIZE = 16


class IdBlockMetadataError(Exception):
    """An ID_BLOCK_* environment variable holds a value that cannot be used."""


@dataclass(frozen=True)
class IdBlockMetadata:
    """The ID block's identifying fields, validated and in usable form.

    Read once and shared between the step that builds an ID block and the step
    that checks the resulting report, so the two cannot disagree about what was
    asked for.  Deriving expectations separately from the environment would let
    the check pass against values the ID block was never built with.
    """

    family_id: str
    image_id: str
    guest_svn: int
    policy: int

    @property
    def family_id_bytes(self) -> bytes:
        """FAMILY_ID as it appears in the report: ASCII, NUL-padded to 16 bytes."""
        return self.family_id.encode("ascii").ljust(ID_FIELD_SIZE, b"\x00")

    @property
    def image_id_bytes(self) -> bytes:
        """IMAGE_ID as it appears in the report: ASCII, NUL-padded to 16 bytes."""
        return self.image_id.encode("ascii").ljust(ID_FIELD_SIZE, b"\x00")


def _read_id_field(var: str, default: str) -> str:
    """Read a 16-byte ID field, rejecting values that cannot encode into one.

    ``ljust`` pads but never truncates, so an over-long value would otherwise
    produce an expectation longer than the report field and fail to match every
    time, with a byte-diff that does not say why.
    """
    value = os.environ.get(var, default)
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise IdBlockMetadataError(
            f"{var}: must be ASCII; {value!r} is not ({exc})"
        ) from exc
    if len(encoded) > ID_FIELD_SIZE:
        raise IdBlockMetadataError(
            f"{var}: must be at most {ID_FIELD_SIZE} bytes to fit the SNP field; "
            f"{value!r} is {len(encoded)}"
        )
    return value


def _read_int(var: str, default: str, *, base: int) -> int:
    """Read an integer-valued variable, failing with the variable's name."""
    raw = os.environ.get(var, default)
    try:
        parsed = int(raw, base)
    except (TypeError, ValueError) as exc:
        raise IdBlockMetadataError(
            f"{var}: expected an integer, got {raw!r}"
        ) from exc
    if parsed < 0:
        raise IdBlockMetadataError(f"{var}: must not be negative, got {parsed}")
    return parsed


def read_id_block_metadata() -> IdBlockMetadata:
    """Read and validate the ID_BLOCK_* environment variables.

    Raises:
        IdBlockMetadataError: a variable is set to something unusable. Raised
            rather than allowed to surface as a ValueError or UnicodeEncodeError
            from deep in a handler, so the step fails with a message naming the
            variable at fault.
    """
    return IdBlockMetadata(
        family_id=_read_id_field("ID_BLOCK_FAMILY_ID", DEFAULT_FAMILY_ID),
        image_id=_read_id_field("ID_BLOCK_IMAGE_ID", DEFAULT_IMAGE_ID),
        guest_svn=_read_int("ID_BLOCK_GUEST_SVN", DEFAULT_GUEST_SVN, base=10),
        # base=0 so 0x-prefixed, decimal and octal forms are all accepted.
        policy=_read_int("ID_BLOCK_POLICY", DEFAULT_POLICY, base=0),
    )


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

    ID block metadata is read from environment variables, falling back to the
    DEFAULT_* constants in this module:
      ID_BLOCK_FAMILY_ID, ID_BLOCK_IMAGE_ID, ID_BLOCK_GUEST_SVN, ID_BLOCK_POLICY

    If guest_measurement.txt is absent (calculate_measurement was skipped or
    failed), this step exits 0 and leaves ctx.profile unchanged, so vm_launch
    proceeds without an ID block.

    A file that is present but malformed is a different case and fails the
    step: absence is an expected configuration, corruption is not.
    """
    try:
        meta = read_id_block_metadata()
    except IdBlockMetadataError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

    try:
        measurement = read_measurement(ctx.artifact_dir)
    except MeasurementMissing as exc:
        return StepHandlerResult(
            exit_code=0,
            stdout=f"INFO: {exc} — skipping ID block generation",
        )
    except MeasurementMalformed as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

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
                "--family-id", meta.family_id,
                "--image-id", meta.image_id,
                "--svn", str(meta.guest_svn),
                "--policy", hex(meta.policy),
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

    ctx.profile = replace(
        ctx.profile, id_block=id_block_b64, id_auth=id_auth_b64, policy=meta.policy
    )

    return StepHandlerResult(
        exit_code=0,
        stdout=(
            f"Generated ID block for measurement {measurement[:16]}...\n"
            f"  family_id={meta.family_id} image_id={meta.image_id} "
            f"svn={meta.guest_svn} policy={hex(meta.policy)}"
        ),
    )
