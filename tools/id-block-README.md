# SEV-SNP ID Block Builder (`tools/id-block-builder.html`)

Browser-based tool for building unsigned ID block templates for SEV-SNP guest
launch. Opens locally — no server needed.

The ID block template produced here is the input to the runtime signing step.
See [`modules/launch/host/generate-id-block/README.md`](../modules/launch/host/generate-id-block/README.md)
for how the template is patched and signed at launch time.

## What an ID Block Does

An ID block is a 96-byte structure passed to QEMU at guest launch. The firmware
validates it at `LAUNCH_FINISH` and uses it to:

- Enforce a minimum ABI version requirement on the host firmware
- Enforce guest policy flags (SMT, single-socket, debug, etc.)
- Bind guest key derivation to a specific guest SVN upper bound, family ID,
  and image ID

Without an ID block, QEMU applies its own default policy and no SVN/metadata
bounds are set on key derivation.

## Inputs

| Field | Size | Notes |
|-------|------|-------|
| Launch digest | 48 bytes, hex | All-zeros placeholder — patched at runtime with the actual guest measurement |
| Family ID | 16 bytes, hex | Identifies the guest family; mixed into derived keys when GFS bit 1 is set |
| Image ID | 16 bytes, hex | Identifies the guest image; mixed into derived keys when GFS bit 0 is set |
| Guest SVN | 4 bytes, decimal | Upper bound for `--guest_svn` at key derivation time |
| ABI minor / ABI major | 1 byte each | Minimum firmware ABI version required; firmware rejects launch if below this |
| Policy flags | 48 bits, hex | Bits 63:16 of the 64-bit guest policy |

## Output

A base64-encoded 96-byte ID block (`id-block.b64`). The block is unsigned —
the launch digest field is a placeholder. Both are fixed at runtime by
`generate_id_block.py`.

## Policy Flags Reference

Policy bits were verified empirically against AMD EPYC 9654 (Genoa), 2 sockets,
SMT on, RAPL disabled, no CXL devices, TSME enabled, API 1.54 build 6. 127
oracle tests were performed.

| Bit(s) | Label | Notes |
|--------|-------|-------|
| 16 | SMT_ALLOWED | Must be 1 when host SMT is on; firmware rejects with fw_error=7 |
| 17 | MBO | Must-be-one; structural requirement for SNP |
| 18 | RESERVED | KVM rejects unconditionally (EINVAL) |
| 19 | DEBUG | Permissive on test HW |
| 20 | SINGLE_SOCKET | Must be 0 on multi-socket systems; firmware rejects with fw_error=7 |
| 21–63 | RESERVED | KVM rejects unconditionally (EINVAL) |

The most permissive valid policy on the test platform is bits 16+17 set, all
else clear. The tool defaults to this value (`00000000000b` in the 48-bit hex
input, yielding full policy `0x00000000000b0000`).

The tool warns if bit 17 (MBO) is cleared or if reserved bits are set.

## Default ID Block

A pre-built default `id-block.b64` is embedded in the guest image directory:

```
modules/launch/host/launch-guest/mkosi.extra/usr/local/lib/guest-image/id-block.b64
```

It was produced by this tool with:
- Launch digest: all zeros (placeholder)
- Family ID / Image ID: all zeros
- Guest SVN: 1
- ABI version: 0.0 (accepts any firmware)
- Policy: bits 16+17 set (SMT_ALLOWED + MBO)
