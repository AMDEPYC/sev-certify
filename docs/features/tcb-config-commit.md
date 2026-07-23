# TCB Config & Commit (`SNP_CONFIG` / `SNP_COMMIT`)

**Description:** Host firmware commands that pin or lock the TCB version used to sign SEV-SNP attestation reports.  
**When to Use:** Air-gapped environments or fleet firmware rollouts where VCEK certificates need to stay valid across firmware changes.  
**How to Use:** `snphost` CLI (`config set`, `config reset`, `commit`) on the host.  
**What sev-certify tests:** Lowers `ReportedTcb` via config set, boots a guest, verifies the attestation report reflects the change, resets, and confirms a live guest's next report reflects the restored values. Also runs `snphost commit` (recording the committed floor before/after). ⚠️ **`snphost commit` is a permanent, irreversible change to the platform's committed TCB — see the warning under "How We Test It".**  

---

## What It Is

AMD SEV-SNP uses a TCB (Trusted Computing Base) version to determine which VCEK certificate signs attestation reports. The TCB is a 64-bit value encoding security patch levels of each firmware component (bootloader, TEE, SNP, microcode, and FMC on Turin).

`SNP_CONFIG` and `SNP_COMMIT` are host firmware commands that control which TCB value appears in guest attestation reports:

- **`SNP_CONFIG` (set)** — Overrides the `ReportedTcb` to a value lower than the running `CurrentTcb`. Guests booted after this command will have their attestation reports signed with a VCEK corresponding to the lowered TCB.
- **`SNP_CONFIG` (reset)** — Clears the override, restoring `ReportedTcb` to match `CurrentTcb`. Takes effect on live VMs immediately (the next attestation report reflects the restored value).
- **`SNP_COMMIT`** — Locks in the current TCB as a minimum floor (`CommittedTcb`). After commit, the platform will not boot with firmware below this level.

## Why You'd Use It

The primary use case is **air-gapped and fleet firmware management**. When firmware is updated across a cluster, the TCB changes and a new VCEK certificate is needed for attestation. In environments without connectivity to AMD's Key Distribution Service (KDS), or during rolling upgrades where hosts run mixed firmware versions, `SNP_CONFIG` lets operators:

1. **Defer VCEK refresh** — After a firmware update, set `ReportedTcb` back to the pre-update value so existing cached VCEK certificates remain valid. Update the cache during a planned maintenance window.
2. **Maintain mixed-version clusters** — During rolling upgrades, keep all hosts reporting the same TCB so a single cached VCEK per chip covers the entire fleet.
3. **Pre-stage certificates** — Fetch the new VCEK before applying firmware, then apply the update and let `ReportedTcb` advance naturally.

Important constraints:
- The ABI enforces `ReportedTcb <= CommittedTcb <= CurrentTcb`. You cannot set `ReportedTcb` above `CommittedTcb`.
- `SNP_CONFIG` does not persist across reboots; orchestration must re-apply it after each boot.

## How To Use It

The `snphost` CLI (from the [VirTEE](https://github.com/virtee/snphost) project) wraps the firmware commands.

```sh
# View current TCB values (Reported + Platform)
snphost show tcb

# Lower ReportedTcb (arguments: BL TEE SNP UCODE MASK_CHIP [FMC])
# Example: decrement Boot Loader SPL by 1 from current value of 4
snphost config set 3 2 27 25 0

# Reset ReportedTcb back to CurrentTcb
snphost config reset

# Lock current TCB as minimum (irreversible until next firmware update)
snphost commit
```

After `config set`, any guest requesting an attestation report will receive one signed with the VCEK corresponding to the lowered `ReportedTcb`. After `config reset`, the next attestation report (even from a running VM) reflects the restored values.

## How We Test It

The test is `snphost-config-commit` at certification level `3.0.0-1`, defined in:

- **Test module:** `sev_verify/cert_tests/c3_0/c3_0_0_1/snphost_config_commit.py`
- **Manifest entry:** `sev_verify/cert_tests/c3_0/manifest.toml`

It is a **mixed-scope** test — it exercises host commands and verifies their effect inside a guest VM. The test runs through the following sequence:

1. **Read current Platform TCB** from `snphost show tcb`.
2. **Lower one TCB field** via `snphost config set`, decrementing the first non-zero field (priority: BL > SNP > TEE > Microcode).
3. **Host-side verify** that `ReportedTcb` now differs from `PlatformTcb`.
4. **Boot an SEV-SNP guest** (the VM sees the lowered TCB from boot).
5. **Guest requests attestation report** via `snpguest report`.
6. **Pull the report** from guest to host and verify:
   - Guest `Reported TCB` matches the host's lowered `Reported TCB`.
   - Guest `Current TCB` still matches the host's unchanged `Platform TCB`.
7. **Verify the lowered report's signature** — fetch the *alternate* VCEK for the lowered `ReportedTcb` from the KDS (`snpguest fetch vcek` derives the URL from the report's TCB) and run a signature-only check (`snpguest verify attestation --signature`). This confirms the firmware actually signed the report with the VCEK corresponding to the lowered TCB, not just that the TCB fields were rewritten. Only this alternate VCEK is fetched; the baseline VCEK is already exercised by the `3.0.0-0` attestation test, so re-fetching it here would risk KDS rate-limiting.
8. **Reset TCB** on the host via `snphost config reset`.
9. **Host-side verify** that `ReportedTcb` matches `PlatformTcb` again.
10. **Same live VM** requests a second attestation report.
11. **Pull and verify** the second report — both guest `Reported` and `Current` TCB now match the host's restored values.
12. **Commit** (`required` step) — run `snphost commit`, which commits `CurrentTcb`. It runs after `config reset`, so the platform is back at baseline where `CommittedTcb` normally already equals `CurrentTcb`. The step reads `CommittedTcb` and `CurrentTcb` from the guest report (`snphost` has no host-side read for `CommittedTcb`) and records the committed floor **before and after** the commit; a commit failure fails the test. ⚠️ **See the warning below — this is a permanent, irreversible change.**
13. **Teardown** — stop the VM and run a final `config reset` to ensure clean state regardless of test outcome.

> ⚠️ **Warning: `snphost commit` permanently advances the platform's committed TCB floor.**
> Unlike `config set`/`config reset` (which are transient and reset on reboot), `snphost commit` writes `CommittedTcb := CurrentTcb` into the PSP. This is **irreversible** — the floor can only be raised further by future firmware updates, never lowered — and it does **not** reset across reboots. After commit, the platform will refuse to boot firmware below the committed level. In normal operation this test commits at the level already committed (a no-op), but on a machine where `CommittedTcb < CurrentTcb` it **will** advance the floor. The step records both values so the change is auditable in the run output.
