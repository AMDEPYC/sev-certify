# sev_verify

Host-side testing harness for SEV-SNP certification. Reads TOML manifests that declare which tests to run, imports per-test Python modules that define executable steps, and orchestrates execution across host and guest environments.
sev-verify uses a non-secure vsock channel between the host and the guest, which is launched as a CVM. Given the purpose of sev-certify, this is acceptable. The vsock channel properties are properties of the guest and the guest is purpose-built for sev-certify. As built, there is no incentive for an attacker to take advantage of the security weakness of the vsock channel.

## Usage

```bash
# Run a specific certification level
python3 -m sev_verify /path/to/guest.efi -v 3.0

# Run multiple levels
python3 -m sev_verify /path/to/guest.efi -v 3.0 -v 3.1

# Override QEMU and/or OVMF (paths must exist; applied to every test that launches a VM)
python3 -m sev_verify /path/to/guest.efi --qemu-binary /opt/qemu/bin/qemu-system-x86_64 --ovmf /usr/share/ovmf/OVMF.amdsev.fd -v 3.0
# Short form for QEMU:
python3 -m sev_verify /path/to/guest.efi --qemu /opt/qemu/bin/qemu-system-x86_64

# Run all certifications found in cert_tests/
python3 -m sev_verify /path/to/guest.efi

# Put per-test artifacts somewhere other than ./artifacts
python3 -m sev_verify /path/to/guest.efi --artifacts-dir /data/sev-artifacts -v 3.0

# Put results somewhere other than results/
python3 -m sev_verify /path/to/guest.efi --output-dir /data/sev-artifacts -v 3.0
```

## How it works

1. Discover manifests at `cert_tests/*/manifest.toml`. Each manifest declares test entries (name, scope, module path).

2. For each test, import its Python module and call `steps()` to get the ordered list of **`BaseStep`** records. Each has a **`kind`** field (`host`, `guest`, `vm_launch`, …). Define steps with **`Step`** either **chained** (``Step(...).host(command=...)``, …) or **in one call** with ``Step.for_host(...)``, ``Step.for_callable(...)``, etc., so your editor shows every required parameter for that shape. Only the fields relevant to ``kind`` may be set; invalid combinations are rejected at construction.

   - **`type`** — certification semantics: `setup` (failure skips remaining steps), `required`, or `info`.
   - **`kind`** — what runs: `host`, `vm_launch`, **`vm_stop`**, `guest`, `guest_pull`, or **`callable`** (in-process handler on the test module; see below).
   - Common fields on **`Step`**: **`expected_result`** (default ``exit_code:0``), **`timeout`** (default **10** seconds); kind-specific arguments go on the chained method or the matching ``Step.for_*`` factory.

3. **Callable steps** — ``Step(...).call(handler="fn")`` or ``Step.for_callable(..., handler="fn")`` builds a step whose `kind` is `callable`. The harness calls `getattr(<test_module>, step.handler)(ctx)` where **`ctx`** is a **`StepContext`**: manifest **`test`**, CLI **`guest_path`**, **`step_results`** from earlier steps in this run, the loaded **`module`**, when a VM is active **`profile`** / **`launch`**, and global **`cli_qemu_binary`** / **`cli_ovmf_path`** when you passed **`--qemu-binary`** / **`--ovmf`**. The handler must return **`StepHandlerResult(exit_code=..., stdout=..., stderr=...)`**; the same **`expected_result`** rules apply (`exit_code:…`, `stdout_contains:…`). Use this for comparisons (e.g. parse `report.bin` and check fields), derived checks, or any logic that is not a shell one-liner. The step **`timeout`** is enforced with a thread-pool wait (stuck CPU in C extensions may not interrupt cleanly).

4. If in the test manifest the scope is defined as either `guest` or `mixed`, the harness builds a `VMProfile` from the test module (`vm_profile()` or `vm_profile` attribute) merged with the CLI `path_to_guest`. If `vm_profile` is omitted, defaults from `vm_profile.VMProfile` are used with the CLI image.

5. Execute steps in order. A `vm_launch` step starts QEMU and waits for the vsock agent; `guest` / `guest_pull` communicate with the VM that has been launched. A **`vm_stop`** step calls `stop_vm` and clears the running guest; Later `vm_launch` can start again. Host steps use subprocess. `guest_pull` runs `base64` on the guest and writes decoded bytes to `host_dest`. If the test ends with a guest still running, the harness still tears it down in a `finally` block.

6. Write results to `results/` (or ``--output-dir``).

## Artifacts directory

Per-test files (pulled guest binaries, logs you add, etc.) go under **``--artifacts-dir``** (default `./artifacts`), organized as::

    <artifacts-dir>/<manifest version>/<test level>/<test_name>/

Example: manifest ``version = "3.0"``, test ``name = "vm-launch-attest"``, ``level = "3.0.0-0"`` → ``artifacts/3.0/3.0.0-0/vm_launch_attest/`` (hyphens in the manifest name become underscores in the folder name).

Prerequisite tests (no certification) use ``<artifacts-dir>/prereqs/<test_name>/``.

The harness creates the directory before the first step and prints ``Artifacts: …``. Callable steps use ``ctx.artifact_dir``; host shell steps get ``$SEV_VERIFY_ARTIFACT_DIR``. For ``guest_pull``, a *relative* ``host_dest`` is resolved under ``artifact_dir``; absolute paths are unchanged.

## Layout

```
sev_verify/              Harness package
  cli.py                 CLI arg parsing + entry point
  models.py              Step (factory), BaseStep (runtime record), TestDefinition, …
  runner.py              load_test_execution_plan, run_step, run_vm_launch_step, …
  vm_profile.py          VMProfile, QEMU argv, vm_launch / stop_vm
  guest_vsock.py         vsock command channel to the guest
  cert_tests/            Certification levels
    common/              Shared test modules
      snp_ok.py      Example host-only test
      ...
    c3_0/                Level 3.0 (example)
      manifest.toml      What to run
      ...
results/                 Output (gitignored)
```

## Requirements

Python 3.11+ (uses `tomllib` from stdlib). No external packages.

## ⚠️ Caution: tests that make permanent platform changes

Most tests are transient — `snphost config set`/`reset`, VM launches, etc. reset on reboot. **One test makes an irreversible change:**

- **`3.0.0-1` snphost-config-commit** runs `snphost commit`, which writes `CommittedTcb := CurrentTcb` into the PSP. This **permanently advances the platform's committed TCB floor**: it cannot be lowered (only raised by future firmware updates) and does **not** reset across reboots. After commit, the platform will refuse to boot firmware below the committed level. In normal operation the commit lands on the level already committed (a no-op), but on a machine where `CommittedTcb < CurrentTcb` it **will** advance the floor. The test records the committed floor before/after in its step output. See [`docs/features/tcb-config-commit.md`](../docs/features/tcb-config-commit.md) for details.
