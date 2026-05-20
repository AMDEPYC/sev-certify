#!/usr/bin/env python3
"""Patch the ld field of id-block.b64 with the guest measurement, sign it,
and generate id-auth.b64 with the signature and ephemeral ID key."""

import base64
import os
import struct
import sys

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives import hashes

MEASUREMENT_FILE = "/usr/local/lib/guest-image/guest_measurement.txt"
ID_BLOCK_FILE = "/usr/local/lib/guest-image/id-block.b64"
ID_AUTH_FILE = "/usr/local/lib/guest-image/id-auth.b64"

# AMD SEV-SNP ABI constants
ALGO_ECDSA_P384_SHA384 = 1
CURVE_P384 = 2


def int_to_le(value, length):
    """Convert an integer to little-endian bytes, zero-padded to length."""
    return value.to_bytes(length, "big")[::-1]


def build_ecdsa_sig(r_int, s_int):
    """Build SEV_ECDSA_SIG (512 bytes): r[72] + s[72] + reserved[368]."""
    r = int_to_le(r_int, 48).ljust(72, b"\x00")
    s = int_to_le(s_int, 48).ljust(72, b"\x00")
    return r + s + b"\x00" * 368


def build_ecdsa_pub_key(pub_numbers):
    """Build SEV_ECDSA_PUB_KEY (1028 bytes): curve[4] + qx[72] + qy[72] + reserved[880]."""
    qx = int_to_le(pub_numbers.x, 48).ljust(72, b"\x00")
    qy = int_to_le(pub_numbers.y, 48).ljust(72, b"\x00")
    return struct.pack("<I", CURVE_P384) + qx + qy + b"\x00" * 880


def main():
    # If the measurement file doesn't exist, calculate-measurement.service
    # was skipped (e.g. AMDSEV OVMF not present).  Nothing to do.
    if not os.path.exists(MEASUREMENT_FILE):
        print(f"INFO: {MEASUREMENT_FILE} not found — skipping ID block generation")
        sys.exit(0)

    # Read measurement (format: 0x<96 hex chars> = 48 raw bytes)
    measurement_text = open(MEASUREMENT_FILE).read().strip()
    hex_str = measurement_text.removeprefix("0x")
    if len(hex_str) != 96:
        print(
            f"ERROR: unexpected measurement length {len(hex_str)} hex chars (expected 96)",
            file=sys.stderr,
        )
        sys.exit(1)
    ld = bytes.fromhex(hex_str)

    # Decode existing ID block template, patch ld at offset 0
    id_block = bytearray(base64.b64decode(open(ID_BLOCK_FILE).read().strip()))
    if len(id_block) != 96:
        print(
            f"ERROR: unexpected id-block length {len(id_block)} bytes (expected 96)",
            file=sys.stderr,
        )
        sys.exit(1)
    id_block[0:48] = ld

    # Generate ephemeral P-384 key pair
    private_key = ec.generate_private_key(ec.SECP384R1())
    public_key = private_key.public_key()

    # Sign the patched ID block
    der_sig = private_key.sign(bytes(id_block), ec.ECDSA(hashes.SHA384()))
    r_int, s_int = decode_dss_signature(der_sig)

    # Build ID_AUTH_INFO_STRUCT (4096 bytes)
    #   0x000: id_key_algo (u32)
    #   0x004: auth_key_algo (u32)
    #   0x008: reserved (56 bytes)
    #   0x040: id_block_sig (512 bytes)
    #   0x240: id_key (1028 bytes)
    #   0x644: reserved (60 bytes)
    #   0x680: author_key_sig (512 bytes, zeros)
    #   0x880: author_key (1028 bytes, zeros)
    #   0xC84: reserved (892 bytes)
    id_auth = bytearray(4096)
    struct.pack_into("<I", id_auth, 0x000, ALGO_ECDSA_P384_SHA384)  # id_key_algo
    # auth_key_algo = 0 (no author key), already zero
    id_auth[0x040 : 0x040 + 512] = build_ecdsa_sig(r_int, s_int)
    id_auth[0x240 : 0x240 + 1028] = build_ecdsa_pub_key(public_key.public_numbers())

    # Write outputs
    open(ID_BLOCK_FILE, "w").write(base64.b64encode(bytes(id_block)).decode())
    open(ID_AUTH_FILE, "w").write(base64.b64encode(bytes(id_auth)).decode())

    print(f"Patched {ID_BLOCK_FILE} with measurement ld={hex_str[:16]}...")
    print(f"Generated {ID_AUTH_FILE} with ephemeral ID key signature")


if __name__ == "__main__":
    main()
