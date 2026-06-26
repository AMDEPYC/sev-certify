# generate-id-block

Host-image module that generates and signs an ID block immediately before guest
launch, using `snpguest` for block construction and signing.

## What It Does

`generate_id_block.py` runs as a oneshot systemd service after
`calculate-measurement.service` and before `launch-guest.service`. It:

1. Reads the guest measurement from `guest_measurement.txt` (48-byte SHA-384,
   written as `0x<96 hex chars>`)
2. Generates two ephemeral P-384 key pairs (id key and author key) — neither is
   persisted beyond the service run
3. Invokes `snpguest generate id-block` with the measurement, ephemeral keys,
   and metadata (family ID, image ID, guest SVN, policy)
4. `snpguest` writes `id-block.b64` and `id-auth.b64`

Both output files are then consumed by `launch-guest.sh`, which reads the policy
from `id-block.b64` and passes all three values (`policy=`, `id-block=`,
`id-auth=`) to QEMU's `sev-snp-guest` object.

## Metadata Configuration

The policy and guest identity fields are set via environment variables in the
service unit, with defaults for the benchmark test platform:

| Variable | Default | Description |
|---|---|---|
| `ID_BLOCK_FAMILY_ID` | `0000000000000000000000000000fad0` | 16-byte family ID (32 hex chars) |
| `ID_BLOCK_IMAGE_ID` | `0000000000000000000000000000aed0` | 16-byte image ID (32 hex chars) |
| `ID_BLOCK_GUEST_SVN` | `48` | Guest security version number |
| `ID_BLOCK_POLICY` | `0xb0000` | Guest policy flags (bits 16+17+19) |

Override any of these with a systemd drop-in (`systemctl edit generate-id-block`)
without modifying the script or service file.

For the rationale behind the default values see `DESIGN.md` in this workspace.

## Why Ephemeral Keys

The ID block exists to set policy and metadata bounds on the guest — it is not
intended to assert a persistent identity for this particular launch. An ephemeral
key per launch is sufficient: the firmware verifies the signature is internally
consistent (the ID block is signed by the key in ID auth), not that the key
belongs to any particular owner. No private key material is written to the image.

## File Locations

| File | Path |
|------|------|
| Input: measurement | `/usr/local/lib/guest-image/guest_measurement.txt` |
| Output: ID block | `/usr/local/lib/guest-image/id-block.b64` |
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

Requires `python3-cryptography` (installed via `mkosi.conf`). `snpguest` is
provided by the `modules/build/common/snpguest` build module, which downloads
the binary from `virtee/snpguest` releases and installs it to
`/usr/local/bin/snpguest`.
