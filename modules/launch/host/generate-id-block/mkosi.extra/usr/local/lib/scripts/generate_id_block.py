#!/usr/bin/env python3
"""Generate an ID block and auth block for the current guest measurement.

Generates two ephemeral P-384 key pairs at runtime (id key and author key),
writes them to temporary files, invokes snpguest to build and sign the blocks,
then removes the temporary key files.  No private key material is persisted.

Metadata (family_id, image_id, guest_svn, policy) is read from environment
variables with defaults chosen for the benchmark test platform:
  ID_BLOCK_FAMILY_ID  — 32 hex chars (default: 0000000000000000000000000000fad0)
  ID_BLOCK_IMAGE_ID   — 32 hex chars (default: 0000000000000000000000000000aed0)
  ID_BLOCK_GUEST_SVN  — decimal integer  (default: 48)
  ID_BLOCK_POLICY     — decimal or 0x-prefixed hex (default: 0xb0000)
"""

import os
import subprocess
import sys
import tempfile

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

MEASUREMENT_FILE = "/usr/local/lib/guest-image/guest_measurement.txt"
ID_BLOCK_FILE = "/usr/local/lib/guest-image/id-block.b64"
ID_AUTH_FILE = "/usr/local/lib/guest-image/id-auth.b64"

# Metadata defaults (match the committed template values documented in DESIGN.md)
DEFAULT_FAMILY_ID = "0000000000000000000000000000fad0"
DEFAULT_IMAGE_ID = "0000000000000000000000000000aed0"
DEFAULT_GUEST_SVN = "48"
DEFAULT_POLICY = "0xb0000"


def write_pem_key(key, path):
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
    with open(path, "wb") as f:
        f.write(pem)


def main():
    # If the measurement file doesn't exist, calculate-measurement.service
    # was skipped (e.g. AMDSEV OVMF not present).  Nothing to do.
    if not os.path.exists(MEASUREMENT_FILE):
        print(f"INFO: {MEASUREMENT_FILE} not found — skipping ID block generation")
        sys.exit(0)

    # Read measurement (format: 0x<96 hex chars> = 48 raw bytes)
    with open(MEASUREMENT_FILE) as f:
        measurement_text = f.read().strip()
    hex_str = measurement_text.removeprefix("0x")
    if len(hex_str) != 96:
        print(
            f"ERROR: unexpected measurement length {len(hex_str)} hex chars (expected 96)",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        bytes.fromhex(hex_str)
    except ValueError as e:
        print(f"ERROR: measurement is not valid hex: {e}", file=sys.stderr)
        sys.exit(1)
    measurement = f"0x{hex_str}"

    # Read metadata from environment, falling back to defaults
    family_id = os.environ.get("ID_BLOCK_FAMILY_ID", DEFAULT_FAMILY_ID)
    image_id = os.environ.get("ID_BLOCK_IMAGE_ID", DEFAULT_IMAGE_ID)
    guest_svn = os.environ.get("ID_BLOCK_GUEST_SVN", DEFAULT_GUEST_SVN)
    policy = os.environ.get("ID_BLOCK_POLICY", DEFAULT_POLICY)

    # Generate two ephemeral P-384 key pairs; write to temp files for snpguest
    id_key = ec.generate_private_key(ec.SECP384R1())
    auth_key = ec.generate_private_key(ec.SECP384R1())

    with tempfile.TemporaryDirectory() as tmpdir:
        id_key_path = os.path.join(tmpdir, "id-key.pem")
        auth_key_path = os.path.join(tmpdir, "auth-key.pem")
        write_pem_key(id_key, id_key_path)
        write_pem_key(auth_key, auth_key_path)

        cmd = [
            "snpguest", "generate", "id-block",
            id_key_path,
            auth_key_path,
            measurement,
            "--family-id", family_id,
            "--image-id", image_id,
            "--svn", guest_svn,
            "--policy", policy,
            "--id-block-file", ID_BLOCK_FILE,
            "--auth-info-file", ID_AUTH_FILE,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        # Temp files are removed when the TemporaryDirectory context exits

    if result.returncode != 0:
        print(f"ERROR: snpguest failed (exit {result.returncode})", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(1)

    print(f"Generated {ID_BLOCK_FILE} and {ID_AUTH_FILE} for measurement {hex_str[:16]}...")
    print(f"  family_id={family_id} image_id={image_id} svn={guest_svn} policy={policy}")


if __name__ == "__main__":
    main()
