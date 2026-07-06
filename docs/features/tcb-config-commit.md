# TCB Config & Commit (`SNP_CONFIG` / `SNP_COMMIT`)

**Description:** Host firmware commands that perform operations on the host's TCB (Trusted Computing Base) levels. These levels are recorded in SEV-SNP attestation reports, and determine the VCEK that signs the report. Both operate on **in-memory firmware state only** and reset on reboot.  
**When to Use:** `SNP_CONFIG` for air-gapped/fleet VCEK management, `SNP_COMMIT` for provisional (in-memory) firmware updates. Note that provisional firmware updates are still in active development in the upstream linux kernel.
**How to Use:** `snphost` CLI (`config set`, `config reset`, `commit`) on the host.  
**What sev-certify tests:** Base functionality test for these commands: lowers `ReportedTcb` via config set, boots a guest, verifies the attestation report + signing VCEK reflects the change, resets, and confirms a live guest's next report reflects the restored values. 

---

## What It Is

AMD SEV-SNP records TCB (Trusted Computing Base) versions in attestation reports; the *reported* version also determines which [VCEK](#vcek) certificate signs the report. A TCB is a 64-bit value encoding security patch levels of each firmware component (bootloader, TEE, SNP, microcode, and [FMC](#fmc) on Turin).

The [PSP](#psp) tracks three platform-wide TCB values:

- **`CurrentTcb`** — the TCB of the firmware currently running.
- **`CommittedTcb`** — the anti-rollback floor; the platform will not run firmware below this level.
- **`ReportedTcb`** — the value used to select the signing VCEK.

The [ABI](#abi) enforces `ReportedTcb <= CommittedTcb <= CurrentTcb`.

It also tracks a fourth, per-VM value in each guest context ([GCTX](#gctx)):

- **`LaunchTcb`** — `CurrentTcb` captured at the moment the guest was launched (or imported). It is stamped into the guest's attestation report as `LAUNCH_TCB` and caps key derivation for that guest (a derived key's `TCB_VERSION` may not exceed `LaunchTcb`). `SNP_CONFIG` and `SNP_COMMIT` do not change it — it is fixed for the life of the VM.

`SNP_CONFIG` and `SNP_COMMIT` are host firmware commands that adjust `ReportedTcb` and `CommittedTcb` respectively. **Both only mutate in-memory firmware state — neither touches the firmware installed in flash — so both reset on reboot**, at which point the flash firmware reloads and the PSP's TCB values return to their installed defaults. (Making a committed floor truly permanent requires installing new firmware to flash, a separate operation from `snphost commit`.)

- **`SNP_CONFIG` (set)** (`snphost config set`) — Overrides `ReportedTcb` to a value lower than `CommittedTcb`. Guests booted after this command have their attestation reports signed with the VCEK corresponding to the lowered TCB.
- **`SNP_CONFIG` (reset)** (`snphost config reset`) — Clears the override, restoring `ReportedTcb` to match `CurrentTcb`. Takes effect on live VMs immediately (the next attestation report reflects the restored value).
- **`SNP_COMMIT`** (`snphost commit`) — Commits the currently-running (provisionally-loaded) firmware, advancing `CommittedTcb` up to `CurrentTcb` so the new firmware's TCB is reflected in VCEK derivation and attestation reports.

---

## Use Case 1 — Air-Gapped & Fleet VCEK Management (`SNP_CONFIG`)

When firmware is updated across a cluster, the TCB changes and a new VCEK certificate is needed for attestation. In environments without connectivity to AMD's [Key Distribution Service (KDS)](#kds), or during rolling upgrades where hosts run mixed firmware versions, `SNP_CONFIG` lets operators pin `ReportedTcb` so existing VCEK certificates stay valid:

1. **Defer VCEK refresh** — After a firmware update, set `ReportedTcb` back to the pre-update value so existing cached VCEK certificates remain valid. Update the cache during a planned maintenance window.
2. **Maintain mixed-version clusters** — During rolling upgrades, keep all hosts reporting the same TCB so a single cached VCEK per chip covers the entire fleet.
3. **Pre-stage certificates** — Fetch the new VCEK before applying firmware, then apply the update and let `ReportedTcb` advance naturally.

Constraints:
- `ReportedTcb <= CommittedTcb` — you cannot set `ReportedTcb` above the committed floor.
- `SNP_CONFIG` does not persist across reboots; orchestration must re-apply it after each boot.

## Use Case 2 — Provisional In-Memory Firmware Updates (`SNP_COMMIT`)

`SNP_COMMIT` supports the **provisional firmware update** flow. The hypervisor can load a new firmware image *provisionally* via `DOWNLOAD_FIRMWARE_EX` ([AMD SEV-SNP Firmware ABI spec, Platform Management, p.24](https://www.amd.com/content/dam/amd/en/documents/developer/56860.pdf#page=24)) so it can later roll back to the previously loaded firmware if it chooses.

> **Note:** Linux kernel support for `DOWNLOAD_FIRMWARE_EX` is still under active development upstream, so the provisional firmware update flow described here is not yet generally available.

After executing a `DOWNLOAD_FIRMWARE_EX` operation, the hypervisor has two choices:

- **Commit** — call `SNP_COMMIT`, which sets `CommittedTcb := CurrentTcb`. After this operation, the firmware will reject any downgrade below the newly committed level. Commit also sets `ReportedTcb := CurrentTcb` (ABI §8.3), so any `SNP_CONFIG` override in effect is cleared.
- **Roll back** — invoke `DOWNLOAD_FIRMWARE_EX` with the previously committed firmware image.

Within a boot session `SNP_COMMIT` is a one-way ratchet — the floor can be raised but not lowered. But as noted above it lives in memory only: a reboot reloads the flash firmware and reverts `CommittedTcb` to the installed level, so making an update permanent still requires installing the new image to flash.

---

## How To Use It

The `snphost` CLI (from the [VirTEE](https://github.com/virtee/snphost) project) wraps the firmware commands.

```sh
# View current TCB values (Reported + Platform)
snphost show tcb

# --- Use Case 1: SNP_CONFIG ---
# Lower ReportedTcb (arguments: BL TEE SNP UCODE MASK_CHIP [FMC])
# Example: decrement Boot Loader SPL by 1 from current value of 4
snphost config set 3 2 27 25 0

# Reset ReportedTcb back to CurrentTcb
snphost config reset

# --- Use Case 2: SNP_COMMIT ---
# Advance CommittedTcb to CurrentTcb (resets on reboot)
snphost commit
```

After `config set`, any guest requesting an attestation report will receive one signed with the VCEK corresponding to the lowered `ReportedTcb`. After `config reset`, the next attestation report (even from a running VM) reflects the restored values.

## How We Test It

The test is `snphost-config-commit` at certification level `3.0.0-1`, defined in:

- **Test module:** [`sev_verify/cert_tests/c3_0/c3_0_0_1/snphost_config_commit.py`](../../sev_verify/cert_tests/c3_0/c3_0_0_1/snphost_config_commit.py)
- **Manifest entry:** [`sev_verify/cert_tests/c3_0/manifest.toml`](../../sev_verify/cert_tests/c3_0/manifest.toml)

It is a **mixed-scope** test — it exercises host commands and verifies their effect inside a guest VM. See the test module and manifest above for the exact commands, flags, and assertions; what follows is the logical flow.

**`SNP_CONFIG` path.** The test reads the current platform TCB, lowers a single TCB field via `config set`, and confirms host-side that `ReportedTcb` now diverges from the platform value. It then boots a guest and pulls an attestation report, checking that the guest's reported TCB tracks the lowered value while its current TCB still reflects the unchanged platform. To prove the firmware actually re-derived the signing key (rather than just rewriting report fields), it fetches the *alternate* VCEK for the lowered TCB from the KDS and does a signature-only verification. Only the alternate VCEK is fetched — the baseline one is already exercised by the `3.0.0-0` attestation test, and re-fetching risks KDS rate-limiting. The override is then cleared with `config reset`, and the *same live VM* is asked for a second report to confirm the reset takes effect immediately on running guests.

**`SNP_COMMIT` path.** Before committing, the test guards against blessing a provisional firmware image: it compares `CommittedTcb` and `CurrentTcb` (read from the attestation report, the only output that carries `CommittedTcb`) and normally halts if they differ, since committing would advance the anti-rollback floor and remove the operator's ability to roll back. The teardown steps (stop VM, final `config reset`) are ordered *before* this gate precisely so that halting here cannot skip them — a failing `setup`-type step skips all later steps, so cleanup that must always run has to precede it. TCB state is therefore left clean whether the gate passes or halts. When `--allow-host-changes` is set, this guard is downgraded to a warning so the commit path can run to completion — advancing the floor is acceptable there because it resets on the next reboot. The test then runs `commit` and checks it returns success.

We can only test the **no-op commit**. On a normal (non-provisional) host `CommittedTcb == CurrentTcb`, so `snphost commit` has nothing to commit: it leaves the floor where it is and — despite ABI §8.3 — does *not* reset `ReportedTcb`, so any `SNP_CONFIG` override remains in effect (verified empirically against snphost 0.7.0). The `ReportedTcb := CurrentTcb` reset described in the ABI is only observable when commit actually commits a provisionally-loaded firmware image, which requires kernel `DOWNLOAD_FIRMWARE_EX` support that is not yet generally available. The test therefore asserts only that a no-op `commit` succeeds; it does not assert the override-clearing side effect. Teardown (stop VM, final `config reset`) runs before the commit gate so it happens regardless of the commit path's outcome.

---

## Glossary

<a id="abi"></a>
**ABI (Application Binary Interface)** — The [AMD SEV-SNP Firmware ABI Specification](https://www.amd.com/content/dam/amd/en/documents/developer/56860.pdf) (publication #56860), which defines the PSP firmware commands, the guest context and attestation report layouts, and the TCB ordering rules referenced throughout this document.

<a id="fmc"></a>
**FMC** — A firmware component whose security patch level is one of the fields in the TCB version. Present only on "Turin" (Family 1Ah) and newer chips; earlier Genoa/Milan TCB versions omit it.

<a id="gctx"></a>
**GCTX (Guest Context)** — Per-VM firmware state the [PSP](#psp) maintains for each SEV-SNP guest. It holds values fixed at launch, including `LaunchTcb`, which is stamped into the guest's attestation report as `LAUNCH_TCB`.

<a id="kds"></a>
**KDS (Key Distribution Service)** — AMD's public service that distributes [VCEK](#vcek) certificates. A verifier fetches the VCEK for a report's `ReportedTcb` from the KDS to check the report's signature.

<a id="psp"></a>
**PSP (Platform Security Processor)** — The dedicated security co-processor on AMD SoCs that runs the SEV-SNP firmware, tracks the platform TCB values, and derives attestation signing keys.

<a id="vcek"></a>
**VCEK (Versioned Chip Endorsement Key)** — An attestation signing key derived from chip-unique secrets and a TCB version. The VCEK corresponding to a report's `ReportedTcb` signs that report; a verifier fetches the matching VCEK certificate from the [KDS](#kds) to validate the signature.
