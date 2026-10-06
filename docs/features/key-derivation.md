# SNP Derived Keys (`MSG_KEY_REQ`)

**Description:** A guest firmware request that returns a 256-bit key derived from a root key (the VCEK or the VMRK) and a selectable set of the guest's own identity fields. The same inputs on the same chip always produce the same key, and changing any selected input produces a different one.  
**When to Use:** Uses include sealing data to a guest identity, so that only a guest with the same selected identity on the same chip can recover it. The guest chooses which identity fields (policy, image ID, family ID, measurement, SVN, TCB version) the key is bound to.  
**How to Use:** `snpguest key` in the guest (wraps `SNP_GUEST_REQUEST` / `MSG_KEY_REQ`).  
**What sev-certify tests:** That derivation is deterministic and stable across independent guest launches, that each root, VMPL, and GFS bit changes the key, and that the firmware enforces the guest SVN and TCB version bounds.

---

## What It Is

`MSG_KEY_REQ` is an `SNP_GUEST_REQUEST` message a guest sends to the [ASP/PSP](tcb-config-commit.md#psp). The firmware answers with a 32-byte key (in Linux, the `SNP_GET_DERIVED_KEY` ioctl). The key is a function of:

- **A root key** — the [VCEK](tcb-config-commit.md#vcek) (derived from chip-unique secrets) or the [VMRK](#vmrk).
- **A VMPL** — the privilege level the request is made at.
- **Guest Field Select (GFS)** — a bitmask choosing which guest fields are mixed into the key (below).
- **Guest identity fields** — the guest's policy, [family ID](#family-id), [image ID](#image-id) and measurement. These are fixed when the guest is launched (the first three come from the [ID block](#id-block)), and each is mixed in only if its GFS bit is set.
- **A guest SVN** and a **TCB version** — caller-supplied values that are mixed in only if the matching GFS bit is set.

### Guest Field Select

Only the least-significant 6 bits are defined for message version 1; bit 6 exists only in message version 2.

| Bit | Field mixed in | Source |
|---|---|---|
| 0 | Guest Policy | Launch (the [ID block](#id-block) policy) |
| 1 | Image ID | ID block |
| 2 | Family ID | ID block |
| 3 | Measurement | Launch measurement |
| 4 | Guest SVN | Input |
| 5 | TCB Version | Input |
| 6 | Launch Mitigation Vector | Input (message version 2 only) |

### Bounds

Three inputs are caller-supplied, so the firmware limits them:

- **Guest SVN** must not exceed the guest SVN given at launch in the ID block. A guest launched without an ID block has little to vary here, which is why this test always launches with one.
- **TCB version** refers to a set of SVNs. No input SVN can exceed the corresponding SVN in the Launch TCB of the attestation report. It is a 64-bit value whose byte layout depends on the processor generation — see [TCB version encoding](#tcb-version-encoding).

A request above either bound is rejected by the firmware; `snpguest key` exits non-zero and writes no key.

The third caller-supplied input is:

- **VMPL** is always input and must not be lower than the current VMPL.

---

## How To Use It

`snpguest` (from the [VirTEE](https://github.com/virtee/snpguest) project) is preinstalled in the guest image.

```sh
# Derive a key from the VCEK, bound to the guest policy, guest SVN and TCB version
# (GFS 0b110001 = bits 0, 4 and 5). --vmpl is the VMPL the request is made at,
# which must be at or above the VMPL the guest is running at.
snpguest key derived-key.bin vcek --vmpl 0 \
    --guest_field_select 0b110001 --guest_svn 2 --tcb_version 1

# The same request from the VM root key instead
snpguest key derived-key.bin vmrk --vmpl 0 \
    --guest_field_select 0b110001 --guest_svn 2 --tcb_version 1
```

`--vmpl` defaults to 1 in `snpguest`. The firmware rejects a request made at a VMPL below the caller's own, so a guest running at VMPL 2 or 3 must pass it explicitly. The VMPL is also mixed into the key, so the same guest gets a different key at each VMPL.

### TCB version encoding

`--tcb_version` takes the TCB as one packed 64-bit integer. The ordering differs between processor generations, so the same integer means different things on each:

| Byte | Milan / Genoa | Turin |
|---|---|---|
| 0 | Boot Loader | [FMC](tcb-config-commit.md#fmc) |
| 1 | TEE | Boot Loader |
| 2 | — | TEE |
| 3 | — | SNP |
| 6 | SNP | — |
| 7 | Microcode | Microcode |

Layouts for processor generations after Turin have not been confirmed. Encoding with the wrong layout does not fail loudly: a value meant for one component silently lands in another. See `TcbVersion` in [`sev_verify/attestation_report.py`](../../sev_verify/attestation_report.py), which owns both directions (decoding the report and encoding this flag).

---

## How We Test It

The test is `key-derivation` at certification level `3.0.0-1`, defined in:

- **Test module:** [`sev_verify/cert_tests/c3_0/c3_0_0_1/key_derivation_test.py`](../../sev_verify/cert_tests/c3_0/c3_0_0_1/key_derivation_test.py)
- **Manifest entry:** [`sev_verify/cert_tests/c3_0/manifest.toml`](../../sev_verify/cert_tests/c3_0/manifest.toml)

It is a **mixed-scope** test: all logic runs on the host, and each `snpguest key` request is sent to the running guest over vsock. No guest-side script is needed, and it does not change host state (no `--allow-host-changes` needed).

### Setup

The guest is **always launched with an ID block** (generated by `calculate_measurement` and `generate_id_block` in [`sev_verify/cvm_props.py`](../../sev_verify/cvm_props.py)), so the report carries a non-zero guest SVN, family ID and image ID. The defaults are family ID `sev-certify-fam0`, image ID `sev-certify-img0`, guest SVN `48` and policy `0xb0000`, each overridable with the `ID_BLOCK_*` environment variables.

The test then fetches an attestation report and reads it **as bytes** with [`sev_verify/attestation_report.py`](../../sev_verify/attestation_report.py), not by parsing `snpguest display report` text. The report supplies the guest SVN bound (`GUEST_SVN`) and the TCB bound (`LAUNCH_TCB`), and the host's processor generation (from `/proc/cpuinfo`) selects the TCB layout above. A generation not listed in `SUPPORTED_GENERATIONS` is refused rather than guessed at, since decoding with the wrong layout produces plausible but wrong bounds. `LAUNCH_TCB` is read only from version 3 and later reports.

Both bounds are **launch values**: the guest SVN from the ID block and the guest's `LAUNCH_TCB`. Neither changes while the guest runs.

The test module documents that the firmware rejects a request made at a VMPL lower than the caller's own, and that the VMPL in the attestation report is the one *requested* in the report message rather than the one the guest runs at. So the test first **detects the running VMPL** by trying 0 through 3 and taking the lowest that is accepted. Requests are then made at that VMPL rather than assuming 0; only the VMPL isolation step also makes one at the next level up.

### What is checked

| Step | Checks |
|---|---|
| Determinism | Two identical requests return the same key. |
| VMPL isolation | A key at the running VMPL differs from one at the next VMPL up, with the same GFS for both (so this also shows the VMPL is mixed in without a GFS bit). Not applicable (and reported as such) at VMPL 3. |
| Root key difference | VCEK and VMRK keys differ for otherwise identical inputs. |
| SVN bounds and sensitivity | The guest SVN bound itself is accepted; bound+1 to bound+3 are rejected; sampled SVNs from 0 to the bound each give a distinct key. If `ID_BLOCK_GUEST_SVN` is 0 there is only one valid SVN, and the sensitivity part is reported as N/A. |
| TCB bounds and sensitivity | The full launch TCB is accepted; for each component, launch+1 to +3 (that component alone) are rejected; sampled values of each component give distinct keys. A component already at 255 is skipped in the above-bound check, since there is no higher value to try. |
| GFS sensitivity | GFS `0x1` and `0x2` give different keys. |
| GFS field mixing | Each of bits 0–5 alone gives a key different from GFS `0`. This shows each mask bit reaches the derivation. It does not show that the selected field is mixed in: the mask is itself an input to the derivation, so any distinct mask gives a distinct key whatever the field values are. |
| Cross-CVM | A key derived in one guest, saved on the host, equals the key re-derived after that guest is stopped and a second guest is launched from the same image and ID block. |

Three design points are worth knowing when reading the test:

- **Every rejection test has a control.** A request above a bound is only meaningful if the same request *at* the bound succeeds, otherwise the rejection could have any cause. The test checks the control first and fails if it is rejected.
- **A rejection is told apart from a transport failure.** The test treats a non-zero `snpguest` exit as a refusal. That could also be a tool or argument error, which is why every rejection test is paired with a control (above). A failure to fetch the key after `snpguest` succeeded raises instead, so it cannot be mistaken for "bound enforced".
- **The sweeps are sampled, not exhaustive.** A component or SVN whose maximum is below 16 is tested at every value. A larger one is tested at its first three, middle three and last three values. Deriving every value would take hundreds of requests per component, and requests that mix in the TCB version can be markedly slower than other key requests on some processors.

For the TCB sweep, one component is varied at a time with the others at 0. FMC is included only where the processor generation has it.

### What is not covered

- **Launch Mitigation Vector** (GFS bit 6, message version 2) is not exercised.
- **VMRK** is compared against VCEK once; the SVN, TCB and GFS sweeps use the VCEK only.
- **VMPL isolation** compares the running VMPL with the next one up, not every pair.
- **The cross-CVM check** uses the default request parameters only. With the default GFS (`0x1`), only the guest policy is mixed in.
- **Changing an identity value.** Policy, family ID, image ID and measurement are fixed for the whole run, so the test never changes one and shows the key changing with it. GFS bits 1–3 (image ID, family ID, measurement) are exercised only as masks, not as values (see GFS field mixing above). Showing that a different family ID or image ID gives a different key would need a second guest launched with a different ID block, for example by changing `ID_BLOCK_FAMILY_ID` or `ID_BLOCK_IMAGE_ID` between launches.
- **Unrecognised processor generations** (for example one newer than Turin whose TCB layout is not yet in `SUPPORTED_GENERATIONS`) fail at the report-parsing step, and the steps that need the report's bounds fail with it. Supporting a new generation means adding it to that table, with its layout confirmed against `snphost show tcb` on real hardware.
- **Version 2 attestation reports** leave `LAUNCH_TCB` undecoded, so the TCB step fails rather than guess. No platform tested so far produces them.

---

## Glossary

<a id="family-id"></a>
**Family ID** — A 16-byte value the guest owner puts in the [ID block](#id-block), copied into the attestation report. It conventionally groups related guest images. Mixed into a derived key when GFS bit 2 is set.

<a id="id-block"></a>
**ID block** — A signed structure supplied when a guest is launched. It carries the expected measurement, guest policy, family ID, image ID and guest SVN. The firmware checks the measurement at launch, and the other fields then appear in the attestation report and are available to mix into derived keys. sev-certify self-signs one with ephemeral keys.

<a id="image-id"></a>
**Image ID** — A 16-byte value the guest owner puts in the [ID block](#id-block), copied into the attestation report. It conventionally identifies one specific guest image. Mixed into a derived key when GFS bit 1 is set.

<a id="launch-tcb"></a>
**LaunchTcb (`LAUNCH_TCB`)** — The TCB (`CurrentTcb`) captured when a guest was launched. It is fixed for the life of the VM and appears in the attestation report as `LAUNCH_TCB`.

<a id="msg-key-req"></a>
**`MSG_KEY_REQ`** — The `SNP_GUEST_REQUEST` message type that asks the firmware to derive a key. In the guest it is reached through `snpguest key`, or the `SNP_GET_DERIVED_KEY` ioctl.

<a id="svn"></a>
**SVN (Security Version Number)** — A version counter the guest owner assigns in the ID block. A derived key can mix in an SVN no higher than the launch value, so a key tied to an older SVN stays derivable by newer guests, but not the reverse.

<a id="vmpl"></a>
**VMPL (Virtual Machine Privilege Level)** — One of four privilege levels (0–3) inside a guest, 0 being the most privileged. Each key request is made at one, and the VMPL is one of the inputs to the derived key.

<a id="vmrk"></a>
**VMRK (VM Root Key)** — The second selectable root key, alongside the [VCEK](tcb-config-commit.md#vcek).
