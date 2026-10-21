# Ciphertext Hiding (`CIPHERTEXT_HIDING_DRAM`)

**Description:** An SEV-SNP feature on Sorano (EPYC 8005) and Turin (EPYC 9005) that stops the host from observing the ciphertext of a guest's private DRAM. The guest policy can require it, and the attestation report records whether it was enabled for that guest.  
**When to Use:** When a guest owner needs the hypervisor to see a fixed pattern instead of guest ciphertext, and is willing to reserve SNP ASIDs for that purpose.  
**How to Use:** Enable it in BIOS, then load `kvm_amd` with `ciphertext_hiding_asids=<N>`. Can enforce the feature to be enabled by setting guest policy bit 24. See [How To Use It](#how-to-use-it) for the commands.  
**What sev-certify tests:** That a guest which requires ciphertext hiding is rejected while the feature is disabled, and that once it is enabled with one ASID the guest launches and its attestation report shows the feature.

---

## What It Is

The property the feature is meant to provide is that a host read of a guest's private memory does not return the ciphertext. The expected host-visible bytes are `0xff`. That blocks an attacker who would otherwise glean information about the plaintext from patterns in the ciphertext. sev-certify does not check those bytes directly; see [What is not covered](#what-is-not-covered).

Ciphertext hiding has two independent switches, both reported in [`SNP_PLATFORM_STATUS`](#snp-platform-status):

- **`CIPHERTEXT_HIDING_DRAM_CAP`** — the platform can do ciphertext hiding and the BIOS has enabled it. Without this bit, the kernel cannot turn the feature on.
- **`CIPHERTEXT_HIDING_DRAM_EN`** — the feature is enabled for the running kernel. This is what a guest launch actually requires.

The BIOS configuration divides ASIDs between SEV and SEV-ES/SEV-SNP. The kernel parameter [`ciphertext_hiding_asids`](#ciphertext-hiding-asids) further partitions the SEV-ES/SEV-SNP pool: the first *N* ASIDs are reserved for SEV-SNP guests that receive ciphertext hiding. *N* of 0 leaves the feature disabled. After SNP initialization KVM clamps the request to the ASIDs the CPU actually has and writes the effective count back to sysfs.

A guest requires the feature by setting [`CIPHERTEXT_HIDING_DRAM`](../getting-started-user-guide-for-sev.md) (guest policy bit 24). The firmware rejects that launch when `CIPHERTEXT_HIDING_DRAM_EN` is clear. When the launch is accepted, the attestation report's [`PLATFORM_INFO`](#platform-info) bit 4 is set.

---

## How To Use It

Requirements:

- SEV firmware 1.55 or newer.
- Linux kernel 6.18 or newer.

Enable it in the BIOS. On an AMD CRB, the setting is at:

`AMD CBS → UMC Common Options → DDR RAS → ECC Configuration → CipherText Hiding Enable`

`snphost` (from the [VirTEE](https://github.com/virtee/snphost) project) can report the two platform bits. A build that does emits one of these messages from `snphost ok --features`:

- `Ciphertext Hiding is available and enabled` — `CIPHERTEXT_HIDING_DRAM_CAP` and `CIPHERTEXT_HIDING_DRAM_EN` are both set. The BIOS has enabled the feature, and the running kernel has it on.
- `Ciphertext Hiding is available, but not enabled` — `CIPHERTEXT_HIDING_DRAM_CAP` is set and `CIPHERTEXT_HIDING_DRAM_EN` is clear. The platform can do ciphertext hiding, but the kernel has not turned it on. Load `kvm_amd` with `ciphertext_hiding_asids` greater than 0.
- `Ciphertext Hiding is unavailable` — `CIPHERTEXT_HIDING_DRAM_CAP` is clear. The BIOS is not reporting the capability to run the feature. That is either a platform that cannot do ciphertext hiding, or a capable platform where the BIOS setting is off. On Sorano or Turin with SEV firmware 1.55 or newer, this result means the BIOS setting is off: enable CipherText Hiding there. The test does not read the firmware version; it only sees this bit.

`snphost ok` exits 1 when any check fails, including this one while the feature is merely disabled. Read the JSON message, not the exit code. `snphost` does not report how many ASIDs were assigned; that value is in sysfs.

```sh
# Platform bits. Judge the Ciphertext Hiding message, not the exit code.
snphost ok --features --output json

# ASIDs currently reserved. 0 means the feature is disabled.
cat /sys/module/kvm_amd/parameters/ciphertext_hiding_asids

# Enable with one SNP ASID. Stop every KVM guest first.
# modprobe -r is never forced: if a guest is still running, the unload fails.
sudo modprobe -r kvm_amd
sudo modprobe kvm_amd ciphertext_hiding_asids=<number of desired asids>

# Return to disabled
sudo modprobe -r kvm_amd
sudo modprobe kvm_amd ciphertext_hiding_asids=0
```

"Built in" means `kvm_amd` is compiled into the kernel image (`CONFIG_KVM_AMD=y`) instead of shipped as a separate loadable module, `kvm_amd.ko` (`CONFIG_KVM_AMD=m`). The module code is the same either way; only how it is added to the kernel differs. A built-in module cannot be unloaded or reloaded. `/sys/module/kvm_amd` exists, but `initstate` does not. Set `kvm_amd.ciphertext_hiding_asids=<N>` on the kernel command line and reboot instead.

Stock distro kernels, including Ubuntu, use `=m`. To check a host manually:

```sh
grep CONFIG_KVM_AMD= /boot/config-$(uname -r)
```

`=y` means built in. `=m` means loadable. The test itself checks for `initstate`, not this file.

A guest that requires this feature enabled during the guest launch uses policy 0x1030000: bit 16 (SMT allowed), bit 17 (reserved, must be one), and bit 24 (CIPHERTEXT_HIDING_DRAM). Inside the guest after the guest boot, snpguest report writes an attestation report whose PLATFORM_INFO bit 4 reports whether ciphertext hiding was enabled. For more guidance on how to manually launch a guest, see [How to Run a Guest Manually](../how-to-run-guest-manually.md).

---

## How We Test It

The test is `ciphertext-hiding` at certification level `4.0.0-0`, defined in:

- **Test module:** [`sev_verify/cert_tests/c4_0/c4_0_0_0/ciphertext_test.py`](../../sev_verify/cert_tests/c4_0/c4_0_0_0/ciphertext_test.py)
- **Manifest entry:** [`sev_verify/cert_tests/c4_0/manifest.toml`](../../sev_verify/cert_tests/c4_0/manifest.toml)

It is a **mixed-scope** test. The manifest sets `host_changes = true` because reloading `kvm_amd` changes host state for the boot session. Pass `--allow-host-changes` to permit that reload. Without the flag the test never disables or enables the feature.

The guest profile sets policy `0x1030000` and 2048 MiB of memory. There is no ID block. The positive launch asks the guest for an attestation report (`snpguest report report.bin request.bin --random`), pulls `report.bin` to the host, and parses it with [`sev_verify/attestation_report.py`](../../sev_verify/attestation_report.py).

Platform state is read in `steps()`, before any step runs, because the step list has to be chosen up front. The preflight step then announces which case that read selected.

### Preflight

The preflight step is `required`. It reports one of these and, when the platform cannot continue, `steps()` returns only that step. The failure text names the platform state. A stop fails level `4.0.0-0`.

| Platform | `--allow-host-changes` | What runs |
|---|---|---|
| Status could not be read | either | Preflight only |
| No `CIPHERTEXT_HIDING_DRAM_CAP` | either | Preflight only. On Sorano or Turin, enable ciphertext hiding in BIOS. |
| Capable, disabled | no | Preflight only. The test will not enable the feature on its own. |
| Capable, enabled, ASID count unreadable | yes | Preflight only. The original setting could not be restored, so nothing is reloaded. |
| Capable, enabled | no | Positive test only. The feature is left enabled. |
| Capable, enabled, ASID count known | yes | Positive test, then disable, negative test, then restore the original ASID count. |
| Capable, disabled | yes | Negative test, enable 1 ASID, positive test, then disable again. |

A host that stops in the preflight ends after that announcement, and the level fails.

### With `--allow-host-changes`

**Already enabled**, with a readable ASID count (for example 2):

1. Positive test, while the feature is still at its original setting.
2. Reload `kvm_amd` with `ciphertext_hiding_asids=0` and require `CIPHERTEXT_HIDING_DRAM_EN` clear.
3. Negative test.
4. Reload with the original count and require `CIPHERTEXT_HIDING_DRAM_EN` set.

**Already disabled:**

1. Negative test, before anything is changed.
2. Reload with `ciphertext_hiding_asids=1` and require `CIPHERTEXT_HIDING_DRAM_EN` set. The positive test always enables exactly one ASID, not the host's previous count.
3. Positive test.
4. Reload with `ciphertext_hiding_asids=0` and require `CIPHERTEXT_HIDING_DRAM_EN` clear.

Each reload is a `setup` step. A `setup` failure skips every later step, including a restore that has not run yet. The handler still tries to leave `kvm_amd` loaded: if the requested `modprobe` fails after a successful unload, it loads `kvm_amd` again with no `ciphertext_hiding_asids` parameter before returning the failure. Unload is `modprobe -r` and is never forced.

### What is checked

| Step | Checks |
|---|---|
| Positive launch | The guest boots with policy bit 24 set. |
| Report | `policy` has `CIPHERTEXT_HIDING_DRAM` (bit 24) set, and `PLATFORM_INFO` has ciphertext hiding enabled (bit 4) set. A mismatch prints `FAIL: Check for ciphertext hiding enablement from the Attestation Report fails: ` followed by the field that did not match. |
| After the positive launch | `vm_stop` runs and is `required`. A failed stop fails the test, before a later `kvm_amd` reload. |
| Negative launch | QEMU is rejected at `SNP_LAUNCH_START`. Matching that string rules out a boot timeout, which would also be a non-zero exit. |
| After the negative launch | `vm_stop` runs and is `required`. A rejected launch leaves no guest, and that stop passes with `No running guest`. If the launch unexpectedly stayed up, the stop tears it down before the next `kvm_amd` reload. A failed stop fails the test. |

### What is not covered

- **ASID exhaustion.** The number of concurrent SEV-SNP guests must not exceed `ciphertext_hiding_asids`. The harness launches one VM at a time, so this is not tested.
- **Direct memory inspection.** Confirming that a host read of private guest memory returns `0xff` needs access to those private pages. No userspace mechanism for that was found, and changing the kernel to expose it is out of scope.
- **Built-in `kvm_amd`** (`CONFIG_KVM_AMD=y`). A module with no `initstate` cannot be reloaded. The reload step fails and tells the operator to set the parameter on the kernel command line.

---

## Glossary

<a id="ciphertext-hiding-asids"></a>
**`ciphertext_hiding_asids`** — `kvm_amd` module parameter. The number of SNP ASIDs reserved for ciphertext hiding. `0` disables the feature. The effective value is readable at `/sys/module/kvm_amd/parameters/ciphertext_hiding_asids` after the module loads.

<a id="platform-info"></a>
**`PLATFORM_INFO`** — A 64-bit field at offset `0x040` in the attestation report. Bit 4 is set when ciphertext hiding was enabled for that guest.

<a id="snp-platform-status"></a>
**`SNP_PLATFORM_STATUS`** — Host firmware state read through `snphost`. For this feature it carries `CIPHERTEXT_HIDING_DRAM_CAP` (BIOS capability) and `CIPHERTEXT_HIDING_DRAM_EN` (kernel enablement). To get the latest SEV firmware for your platform, see the [AMD SEV](https://www.amd.com/en/developer/sev.html) page, or update your platform BIOS using your OEM's instructions.
