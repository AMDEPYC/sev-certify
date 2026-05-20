# generate-id-block

Host-image module that patches and signs the ID block template immediately
before guest launch.

The unsigned template that this module operates on is built by the browser-based
tool described in [`tools/id-block-README.md`](../../../../tools/id-block-README.md).

## What It Does

`generate_id_block.py` runs as a oneshot systemd service after
`calculate-measurement.service` and before `launch-guest.service`. It:

1. Reads the guest measurement from `guest_measurement.txt` (48-byte SHA-384,
   written as `0x<96 hex chars>`)
2. Decodes the ID block template from `id-block.b64` (must be exactly 96 bytes)
3. Patches bytes 0–47 (the `ld` field) with the actual guest measurement
4. Generates an ephemeral P-384 key pair
5. Signs the patched ID block with ECDSA-P384-SHA384
6. Writes the signed ID block back to `id-block.b64`
7. Writes a 4096-byte `ID_AUTH_INFO` structure to `id-auth.b64` containing the
   signature and the ephemeral public key (no author key)

Both output files are then consumed by `launch-guest.sh`, which reads the policy
from `id-block.b64` and passes all three values (`policy=`, `id-block=`,
`id-auth=`) to QEMU's `sev-snp-guest` object.

## Why Ephemeral Keys

The ID block exists to set policy and metadata bounds on the guest — it is not
intended to assert a persistent identity for this particular launch. An ephemeral
key per launch is sufficient: the firmware verifies the signature is internally
consistent (the ID block is signed by the key in ID auth), not that the key
belongs to any particular owner.

If persistent ID key identity is needed in the future, the script would need to
accept a pre-generated key rather than generating one.

## File Locations

| File | Path |
|------|------|
| Input: measurement | `/usr/local/lib/guest-image/guest_measurement.txt` |
| Input/output: ID block | `/usr/local/lib/guest-image/id-block.b64` |
| Output: ID auth | `/usr/local/lib/guest-image/id-auth.b64` |
| Script | `/usr/local/lib/scripts/generate_id_block.py` |

## Service Ordering

```
calculate-measurement.service
        ↓
generate-id-block.service      ← this module
        ↓
launch-guest.service
```

## Dependencies

Requires `python3-cryptography` (installed via `mkosi.conf`).
