# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Does

`sev-certify` provides a framework for certifying AMD SEV-SNP support on operating systems. A "Dispatch Host" serves host/guest OS images (built by mkosi) to a bare-metal EPYC test server. The server boots the host image, which then launches a guest VM, runs attestation tests, and reports results as a GitHub Issue.

## Image Architecture

Images are built by `mkosi` using a composable module system under `modules/`. Each module is a directory with:
- `mkosi.conf` — package lists, includes, build options
- `mkosi.extra/` — files overlaid verbatim into the image filesystem

Module categories:
- `modules/build/host` and `modules/build/guest` — orchestrate includes
- `modules/launch/host/` — measurement, ID block generation, guest launch, verification
- `modules/system/`, `modules/test/`, `modules/report/`, `modules/stop/` — lifecycle phases

## Launch Pipeline (systemd service ordering)

All services are `Type=oneshot` with explicit `After=`/`Before=` ordering:

```
calculate-measurement.service   (guest_measurement.sh — runs snpguest generate measurement)
        ↓
generate-id-block.service       (generate_id_block.py — ephemeral keys + snpguest generate id-block)
        ↓
launch-guest.service            (launch-guest.sh — QEMU + sev-snp-guest object)
        ↓
verify-guest.service            (polls journal for "boot-successful" from guest)
        ↓
launch-done.service
```

If `calculate-measurement.service` is skipped (no AMDSEV OVMF), `guest_measurement.txt` is absent. `generate-id-block.service` detects this, exits 0, and `launch-guest.sh` launches without an ID block.

## Key Files

| File | Purpose |
|---|---|
| `modules/launch/host/generate-id-block/mkosi.extra/usr/local/lib/scripts/generate_id_block.py` | Generates ephemeral P-384 keys and calls `snpguest generate id-block` |
| `modules/launch/host/generate-id-block/mkosi.extra/usr/local/lib/systemd/system/generate-id-block.service` | Systemd unit; sets `Environment=` defaults for ID block metadata |
| `modules/launch/host/launch-guest/mkosi.extra/usr/local/lib/scripts/launch-guest.sh` | Builds QEMU command; extracts policy from `id-block.b64` bytes 88–95 (LE u64) |
| `modules/launch/host/guest-measurement/mkosi.extra/usr/local/lib/scripts/guest_measurement.sh` | Runs `snpguest generate measurement` |

## ID Block Metadata

Configurable via environment variables in `generate-id-block.service` (override with a systemd drop-in):

| Variable | Default | Notes |
|---|---|---|
| `ID_BLOCK_FAMILY_ID` | `0000000000000000000000000000fad0` | 32 hex chars |
| `ID_BLOCK_IMAGE_ID` | `0000000000000000000000000000aed0` | 32 hex chars |
| `ID_BLOCK_GUEST_SVN` | `48` | Decimal |
| `ID_BLOCK_POLICY` | `0xb0000` | Bits 16 (SMT), 17 (MBO), 19 (DEBUG) |

## Design Constraints

- **Additive principle**: ID block support must not cause failures that would not have occurred without it. This is why absent `guest_measurement.txt` causes exit 0 (not 1).
- **Ephemeral keys**: Deliberate — for benchmark/test use, not production attestation. The signature satisfies firmware's structural requirement, not external verifiability.
- See `../DESIGN.md` (workspace-level) for full rationale on policy bit choices and why the DEBUG bit (19) is set.

## Cross-Repo Context

This branch (`pr/id-block`) is part of a workspace at `~/code/git/features/id-block/` comparing this implementation against `virtee/snpguest` (in the sibling `virtee/` directory). The workspace `CLAUDE.md` and `DESIGN.md` explain the comparison goals and key differences.
