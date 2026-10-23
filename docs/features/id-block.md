# SNP ID Block

**Description:** A 96-byte signed structure the guest owner hands to the hypervisor at launch. It pins the guest's expected launch measurement and carries owner-chosen identity fields (`FAMILY_ID`, `IMAGE_ID`, `GUEST_SVN`, `POLICY`). Firmware refuses to finish the launch if the measurement does not match, and copies the identity fields and a digest of the signing key into the guest's attestation report.  
**When to Use:** When an SEV CVM needs an identity other than its measurement. With an ID block, the CVM has two identities, the measurement and [ID block signer, `FAMILY_ID`, `IMAGE_ID`, `GUEST_SVN`, `POLICY`]. Also, a `GUEST_SVN` <= current `GUEST_SVN` can be mixed into SEV derived keys.  
**How ID blocks are used:** sev-certify/verify uses `snpguest generate id-block` to build an ID block and its signature block and passes them to QEMU with `-object sev-snp-guest,id-block=...,id-auth=...`. In `sev_verify`, two shared callable steps do both for you.  
**What sev-certify tests:** `id-block-test` launches a guest with a valid ID block and checks that the attestation report carries the ID block's fields, then confirms that three deliberately broken ID blocks (bad measurement, SMT-incompatible policy, impossible ABI version) are rejected for the right reason.

---

## What It Is

An SEV-SNP launch normally accepts whatever the hypervisor loads. An **ID block** lets the guest owner state up front what a valid launch looks like. It is 96 bytes and holds (along with a structure version):

- **LD** — the expected [launch measurement](#launch-digest) of the guest.
- **FAMILY_ID** and **IMAGE_ID** — two 16-byte, owner-defined labels (see [FAMILY_ID and IMAGE_ID](#family-id-image-id)).
- **GUEST_SVN** — an owner-defined security version number.
- **POLICY** — the [guest policy](#policy) the owner expects the guest to launch under.

The ID block is signed with an **ID key**. The ID key is in turn optionally signed by an **author key**. The two public keys and both signatures travel in a second structure, the **ID auth info** (`id-auth`). QEMU takes the two as separate base64 values: `id-block=` and `id-auth=`.

At `SNP_LAUNCH_FINISH`, firmware checks the ID block signature against the ID auth info and compares the ID block's launch digest with the measurement it actually computed. A mismatch fails the launch with a `Bad measurement` error. When the launch succeeds, the guest's attestation report carries:

| Report field | Comes from |
|---|---|
| `FAMILY_ID`, `IMAGE_ID`, `GUEST_SVN` | the ID block |
| `ID_KEY_DIGEST` | a digest of the ID public key; all zero means **no ID block was used** |
| `AUTHOR_KEY_DIGEST` | a digest of the author public key (only when the author key is used) |

**Notes:**

Firmware validates the *author* key only when `author-key-enabled=true` is passed to QEMU (`VMProfile.auth_key_enabled`, default `False`). `sev_verify` leaves it off, so its ID blocks are effectively self-signed by the ID key. This reflects "development history" as the snpguest command above does result in both an ID key and an author key being used. Given this, it would be easy to make the change to use an author key.

Attestation reports include a `POLICY` field, but this is always true and an ID block isn't required to set a guest policy. Guest policy is specified independently (using `policy=` on the QEMU command line) and, if an ID block is used, its `POLICY` field must match this independently specified policy.

---

## How sev-certify/verify Uses ID blocks

sev-certify/verify use `snpguest` (from the [VirTEE](https://github.com/virtee/snpguest) project), which builds both structures. sev-certify/verify computes the expected measurement first, then uses `snpguest` to sign it into an ID block with two P-384 keys:

```sh
# Expected launch measurement for this OVMF + guest image. With --output-format hex
# the file holds the digest already 0x-prefixed, which snpguest requires as input:
# an unprefixed string is decoded as base64, not hex.
snpguest generate measurement --vcpu-type EPYC-v4 --ovmf OVMF.amdsev.fd \
    --kernel guest.efi --output-format hex --measurement-file guest_measurement.txt

# ID block + ID auth info, each written as base64
snpguest generate id-block id-key.pem author-key.pem "$(cat guest_measurement.txt)" \
    --family-id sev-certify-fam0 --image-id sev-certify-img0 \
    --svn 48 --policy 0xb0000 \
    --id-file id-block.b64 --auth-file id-auth.b64
```

`snpguest` signs and computes digests but does not create keys; sev-certify/verify generates the two P-384 PEM files using Python's crypto library. The `id-block` and `id-auth` launch parameters are described in the [getting started guide](../getting-started-user-guide-for-sev.md).

### How to use an ID block in a test

Two callable steps in [`sev_verify/cvm_props.py`](../../sev_verify/cvm_props.py), run in this order before `vm_launch`:

1. **`calculate_measurement`** — runs `snpguest generate measurement` against the guest image and the profile's resolved OVMF, and writes `guest_measurement.txt` to the test's artifact directory. It uses `--vcpu-type EPYC-v4`, which matches the `-cpu EPYC-v4` in the QEMU command (`sev_verify/vm_profile.py`); a test that changed the CPU model would need to change both. It fails (non-zero exit) when OVMF cannot be resolved or `snpguest` errors, and because the step is `setup` severity the rest of the test is then skipped.
2. **`generate_id_block`** — reads `guest_measurement.txt`, creates two **ephemeral** P-384 key pairs (discarded after use, so `ID_KEY_DIGEST` differs on every run), runs `snpguest generate id-block`, and updates `ctx.profile` with `id_block`, `id_auth` and `policy`. The following `vm_launch` then passes all three to QEMU.

The ID block's identifying fields come from four environment variables, read when the step runs:

| Variable | Default | Constraint |
|---|---|---|
| `ID_BLOCK_FAMILY_ID` | `sev-certify-fam0` | ASCII, at most 16 bytes; NUL-padded to 16 in the report |
| `ID_BLOCK_IMAGE_ID` | `sev-certify-img0` | same |
| `ID_BLOCK_GUEST_SVN` | `48` | non-negative decimal integer |
| `ID_BLOCK_POLICY` | `0xb0000` | non-negative integer; `0x`, decimal and octal forms are accepted |

A bad value fails the step with a message that names the variable. `sudo` resets the environment by default, so when running the harness as root pass the variables through explicitly (for example `sudo ID_BLOCK_GUEST_SVN=7 python3 -m sev_verify ...`, or `sudo env ...`), subject to what your sudo policy allows.

The default policy `0xb0000` sets bit 16 (SMT allowed), bit 17 (reserved, must be one) and bit 19 (**DEBUG**). Setting the DEBUG bit reflects previously misunderstanding the exact semantics of the bit. In the case of the DEBUG, successful guest launch doesn't depend on the bit being consistent with some other debug setting. Instead, policy bit 19 is essentially both a policy bit and a debug enable bit. Given this, `0x30000` and `0xb0000` are equally permissive, but `0xb0000` enables debug while `0x30000` doesn't. The ABI major/minor requirement (bits 15:8 and 7:0) is left at zero, meaning no minimum.

Import both handlers and put the two steps ahead of `vm_launch`:

```python
from sev_verify.cvm_props import calculate_measurement, generate_id_block  # noqa: F401
from sev_verify.models import BaseStep, Step


def steps() -> list[BaseStep]:
    return [
        Step.for_callable(
            name="Calculate measurement", type="setup",
            handler="calculate_measurement", timeout=60,
        ),
        Step.for_callable(
            name="Generate ID block", type="setup",
            handler="generate_id_block", timeout=30,
        ),
        Step.for_vm_launch(
            name="Launch guest", type="setup",
            guest_id="my-test-vm", timeout=300,
        ),
        # ... guest steps ...
    ]
```

The `# noqa: F401` import is deliberate: the harness resolves a callable step's `handler` by name from the test module's globals, so the import is needed even though nothing in the module calls it.

Things to know before relying on it:

- **Order matters.** `calculate_measurement` must precede `generate_id_block`, and both must precede `vm_launch`. Keep them `setup` severity so that a failure stops the test instead of letting it run against a guest with no ID block.
- **A missing measurement is not an error.** If `guest_measurement.txt` is absent, `generate_id_block` exits 0, logs an `INFO` line, and leaves the profile alone, so the guest launches *without* an ID block. A malformed file (not 48 bytes of hex) does fail the step.
- **Reading the result back.** After `snpguest report report.bin request.bin --random` and a `guest_pull`, parse the report with `attestation_report.read(ctx.artifact_dir / "report.bin", generation=attestation_report.host_generation())` and compare against `read_id_block_metadata()`, the same helper the generator used, so the expectation cannot drift from what was built. `verify_id_block_fields` in the test module is a worked example.
- **Varying the ID block inside one test.** Replace `ctx.profile` with `dataclasses.replace(ctx.profile, id_block=..., id_auth=..., policy=...)` after building a fresh ID block with `snpguest`. The negative cases do this in `_regenerate_id_block`, which is currently private to `id_block_test.py`; a second test that needs it should lift it into `cvm_props.py` instead of copying it.

---

## How We Test It

The test is `id-block-test` at certification level `3.0.0-1`, defined in:

- **Test module:** [`sev_verify/cert_tests/c3_0/c3_0_0_1/id_block_test.py`](../../sev_verify/cert_tests/c3_0/c3_0_0_1/id_block_test.py)
- **Shared steps:** [`sev_verify/cvm_props.py`](../../sev_verify/cvm_props.py)
- **Manifest entry:** [`sev_verify/cert_tests/c3_0/manifest.toml`](../../sev_verify/cert_tests/c3_0/manifest.toml)

It is a **mixed-scope** test (`scope = "mixed"`, no `host_changes`). `-v 3.0.0-1` runs it together with the other `3.0.0-1` tests. Needs on the host: `snpguest`, `python3-cryptography` (used to generate the key pairs), an AMDSEV-capable OVMF, and a QEMU new enough to provide `sev-snp-guest` (9.1 or later). See the test module and manifest above for the exact commands and assertions; what follows is the logical flow.

**Positive path.** Calculate the measurement, generate an ID block, launch with it, pull an attestation report, stop the VM, and then check the report on the host. `verify_id_block_fields` asserts that `GUEST_SVN`, `POLICY`, `FAMILY_ID` and `IMAGE_ID` equal the values the ID block was built with, and that `ID_KEY_DIGEST` is non-zero, with a dedicated message for the all-zero case so "no ID block was used" is not buried under four field mismatches. The report is parsed directly from bytes, with the host's processor generation passed in so the TCB layout is never guessed.

**Negative path.** Three further launches must be refused. Each regenerates the ID block with one deliberate defect, using a fresh key pair, and asserts the *reason* for the rejection, not merely that the launch failed. A bare `exit_code:1` would also be satisfied by a boot timeout, so it cannot tell a real rejection from a hung guest.

| Case | Defect | Expected message | Rejected by |
|---|---|---|---|
| Bad measurement | first byte of the launch digest flipped | `Bad measurement` | firmware, at `SNP_LAUNCH_FINISH` |
| SMT-incompatible policy | policy bit 16 cleared on an SMT-active host | `SNP_LAUNCH_START` | KVM, before firmware sees it (`ret=-22 fw_error=0`), so there is no firmware string to match |
| Impossible ABI version | policy `ABI_MAJOR` (bits 15:8) set to 255 | `Policy is not allowed` | firmware, `SNP_LAUNCH_START` `fw_error=7` |

**The SMT case is conditional.** Clearing the SMT bit only produces a rejection when SMT is actually active, which `steps()` decides from `/sys/devices/system/cpu/smt/active` when the module loads. On a host with SMT off or unsupported, the case is replaced by an `info` step, *SMT policy case not applicable*, that states why, so the omission appears in the results instead of the case silently vanishing.

**Severities.** Measurement, ID block and launch are `setup` (a failure skips the rest). Report retrieval, field verification and each negative-case launch are `required`. The `Stop VM` steps are `info`.

**Artifacts** land in `artifacts/3.0/3.0.0-1/id_block_test/`: `guest_measurement.txt`, `id-block.b64`, `id-auth.b64`, `neg-id-block.b64` and `neg-id-auth.b64` (overwritten by each negative case), and `report.bin`. With `--debug`, each launch also gets a directory named by its `guest_id` (`positive-valid-id-block-vm`, `negative-bad-measurement-vm`, `negative-smt-policy-vm`, `negative-abi-version-vm`) holding `qemu-error.log` and the other guest logs.

### What is not covered

- **The `ID_KEY_DIGEST` value** is only checked to be non-zero. It is not compared with a digest of the key that was generated, so a report carrying a *different* ID key's digest would pass.
- **`AUTHOR_KEY_DIGEST`** is not asserted, and the author key path (`author-key-enabled=true`) is not exercised at all.
- **Family ID and image ID are ASCII only,** because that is what the `ID_BLOCK_*` variables accept, so values with arbitrary bytes are never exercised.
- **The SMT rejection** is not exercised on a host where SMT is off.

---

## Known Quirks

- **`ERR!` on a `Stop VM` after a rejected launch.** After a launch that was *expected* to fail there is no guest to stop, so a following `vm_stop` step can print `ERR!` with `vm_stop: no running guest`. These steps are `info` severity, so the test still passes, but the summary line counts them and can read like `1/14 failed [PASS]`. This is a limitation of the harness, tracked in [#315](https://github.com/AMDEPYC/sev-certify/issues/315).
- **`snpguest` family/image ID format has changed between builds.** Releases up to and including v0.10.0 take `--family-id` and `--image-id` as 16 ASCII characters, which is what the harness passes. Later development builds may expect 32 hex characters instead and reject the harness's value. `snpguest --version` does not tell them apart; check `snpguest generate id-block --help`.

---

## Glossary

<a id="abi"></a>
**ABI (Application Binary Interface)** — The [AMD SEV-SNP Firmware ABI Specification](https://www.amd.com/content/dam/amd/en/documents/developer/56860.pdf) (publication #56860), which defines the firmware commands, the ID block and ID auth layouts, the guest policy bits, and the attestation report layout referenced throughout this document.

<a id="author-key"></a>
**Author key** — The key that signs the [ID key](#id-key). Firmware validates it only when the author key is enabled at launch (`author-key-enabled=true`); `sev_verify` leaves it disabled.

<a id="family-id-image-id"></a>
**Family ID / Image ID** — Two 16-byte, owner-defined identifiers carried in the [ID block](#id-block) and copied into the report as `FAMILY_ID` and `IMAGE_ID`. By convention the family ID groups related images and the image ID names one specific image; firmware assigns them no meaning.

<a id="guest-svn"></a>
**Guest SVN** — An owner-defined security version number carried in the [ID block](#id-block) and reported as `GUEST_SVN`. Also, a `GUEST_SVN` <= current `GUEST_SVN` can be mixed into SEV derived keys.

<a id="id-block"></a>
**ID block** — The 96-byte structure holding the expected [launch digest](#launch-digest), family ID, image ID, version, guest SVN and [policy](#policy), signed by the [ID key](#id-key).

<a id="id-auth"></a>
**ID auth info** — The companion structure to the [ID block](#id-block). It carries the ID block's signature, the ID public key, the [author key](#author-key)'s signature over the ID key, and the author public key. Passed to QEMU as `id-auth=`.

<a id="id-key"></a>
**ID key** — The key that signs the [ID block](#id-block). Firmware reports a digest of its public key as `ID_KEY_DIGEST`; `sev_verify` generates a fresh one for every run.

<a id="launch-digest"></a>
**Launch digest (measurement)** — The 48-byte digest firmware computes over what was loaded into the guest at launch (OVMF, initial vCPU state, and kernel hashes when enabled). The [ID block](#id-block) states the digest the owner expects, and firmware refuses to finish the launch if the two differ.

<a id="policy"></a>
**Policy** — The 64-bit guest policy, fixed at launch. See [ABI](#abi). The same value is given to QEMU (`policy=`) and written into the [ID block](#id-block); `generate_id_block` keeps the two in step.
