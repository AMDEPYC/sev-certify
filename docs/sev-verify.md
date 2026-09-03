# sev_verify reference

Reference for writing tests in the `sev_verify` harness: where tests live, the manifest, step kinds, and `VMProfile`.

To add a test, follow [How to create new tests](how-to-create-new-tests.md). To run the harness, see the [`sev_verify` README](../sev_verify/README.md).

## Contents

- [Organization](#organization)
- [Manifest](#manifest)
- [Steps](#steps)
  - [Common](#common)
  - [Host step](#host-step)
  - [Guest step](#guest-step)
  - [VM launch step](#vm-launch-step)
  - [VM stop step](#vm-stop-step)
  - [Guest-pull step](#guest-pull-step)
  - [Callable step](#callable-step)
- [VM profile](#vm-profile)
  - [Adding parameters](#adding-parameters)
- [Current limitations](#current-limitations)

## Organization

New tests always belong under the `sev_verify/cert_tests` directory. Anything else under `sev_verify` is harness infrastructure.

Each certificate generation has its own folder (`c3_0`, `c3_1`, `c4_0`, `c4_1`, `c5_0`). Each generation corresponds to the minimum AMD EPYC generation that supports the features being tested:

| Certificate location | Certificate generation | AMD EPYC CPU |
| -------- | -------- | -------- |
| c3_0 | 3.0 | AMD EPYC 7003 |
| c3_1 | 3.1 | AMD EPYC 9004 |
| c4_0 | 4.0 | AMD EPYC 8005 |
| c4_1 | 4.1 | AMD EPYC 9005 |
| c5_0 | 5.0 | AMD EPYC 9006 |

Under each certificate folder there is a `manifest.toml` file and several subfolders that correspond to the certification levels for that processor generation.

`manifest.toml` is the table of contents for the certificate generation. It lists the certification levels for that generation and which tests each level introduced.

The tests themselves live in the certificate-level folders. Each test is its own Python module.

The `sev_verify/cert_tests/common` directory contains prerequisite tests that run before every certification level. These are declared in `prereqs.toml`. For example, `snp_ok` verifies that SEV-SNP is enabled and functional on the host and that the required components are present. If any required prerequisite fails, certification is skipped, since subsequent tests would also fail.

`sev_verify/cert_tests/common` is also where shared helper modules belong when more than one test needs the same logic. For example, several tests may inspect an attestation report or the platform TCB. Define that function once under `common/` and import it from each test module instead of copying it:

```python
from sev_verify.cert_tests.common.tcb import read_platform_tcb
```

The harness does not run these modules on its own. A helper runs only when a test imports it. Any normal Python module in that directory is fine.

## Manifest

Start with the manifest. Add the new test entry there first, at the newest available certificate level. **Do not add tests to older certificate levels.**

New certificate levels are added by the maintainers when it is time to release a new level.

Each test entry includes:

- **name** — Name of the new test.
- **description** — What feature(s) the test covers.
- **module** — Python import path to the test module.
  - Format: `cert_tests.<generation>.<level>.<test_name>`
  - Example: `cert_tests.c3_0.c3_0_0_1.snphost_config_commit`
  - **Important:** `<test_name>` must match the Python module filename in the certificate-level directory.
- **scope** — Whether the test runs on the host, guest, or mixed (both).
  - Options: `host`, `guest`, `mixed`
- **level** — Certification level this test belongs to. See [Certification Level Definitions](certifications.md#certification-level-definitions).
- **host_changes** *(optional)* — Whether the test can change host configuration.
  - **Important:** Mark this accurately. Tests that set `host_changes = true` can leave the host in a changed state.

Add a `[[tests]]` table to that generation's `manifest.toml`. Omit `host_changes` unless the test can change the host. When it can:

```toml
[[tests]]
name = "my-new-test"
description = "Verify the feature under test"
module = "cert_tests.c3_0.c3_0_0_1.my_new_test"
scope = "mixed"
level = "3.0.0-1"
host_changes = true
```

The module file for that entry is `sev_verify/cert_tests/c3_0/c3_0_0_1/my_new_test.py`.

## Steps

A certification is a collection of tests. A test is a collection of steps.

In your test module, define a `steps()` function that returns a list of `BaseStep` objects. Import `BaseStep` and `Step` from `sev_verify.models`.

`Step.for_host(...)` builds a **host** step (`kind="host"`). Choose the factory that matches how the step should run: `Step.for_<kind>(...)`.

There are six step kinds, each for a different role in a test.

### Common

Every step kind shares these parameters:

- **name** — Name of the step (a short description).
- **type** — Severity of the step.
  - Options:
    - `setup` — Prepares files, directories, or other state needed later. If it fails, the test fails and remaining steps are skipped.
    - `required` — Must succeed for the test to pass. Later steps still run.
    - `info` — Displays helpful information. A failed `info` step does not fail the test.
- **expected_result** — What the step must return.
  - Default: `exit_code:0` (the command completed successfully).
  - Also accepted: `stdout_contains:<string>`.
- **timeout** — Maximum time allowed in seconds for the step. The step fails if this is exceeded.
  - Default: 10 seconds.

You can also chain `.add_hint(pattern, message)` on any step. If the step fails and `pattern` appears in stdout or stderr, the harness prints `[Hint] message`. The first matching hint wins.

Example:

```python
Step.for_host(
    name="Fetch VCEK from KDS",
    type="setup",
    command='snpguest fetch vcek pem "$SEV_VERIFY_ARTIFACT_DIR" "$SEV_VERIFY_ARTIFACT_DIR/report.bin"',
    timeout=60,
).add_hint("429", "Rate limited by KDS, re-run in a minute"),
```

### Host step

The simplest step kind is **host**. Factory: `Step.for_host(...)`.

A host step runs on the host system. It executes the shell command in the `command` parameter.

Kind-specific parameters:

- **command** — Shell command to run on the host.

The harness sets `$GUEST_PATH` to the CLI guest image and `$SEV_VERIFY_ARTIFACT_DIR` to this test's artifact directory. If `command` is a path to an executable file, that file is invoked with the guest path as `$1` instead of being run through the shell.

Example:

```python
Step.for_host(
    name="Copy guest image into artifacts",
    type="setup",
    command='cp "$GUEST_PATH" "$SEV_VERIFY_ARTIFACT_DIR/guest.efi"',
    timeout=60,
),
```

Quote the variables so paths with spaces still work. Later host steps can read files from `"$SEV_VERIFY_ARTIFACT_DIR"` the same way — for example after a `guest_pull` that wrote `report.bin` there.

### Guest step

Factory: `Step.for_guest(...)`.

A guest step runs a command inside the launched VM over vsock. A `vm_launch` step must succeed first. If there is no running guest yet, the harness launches one from `vm_profile` before the first guest or guest-pull step.

Kind-specific parameters:

- **command** — Command to run in the guest.

Example:

```python
Step.for_guest(
    name="Get attestation report with snpguest",
    type="required",
    command="snpguest report report.bin request.bin --random",
    timeout=300,
),
```

### VM launch step

Factory: `Step.for_vm_launch(...)`.

A VM launch step starts the SEV-SNP guest described by the test module's `vm_profile` (merged with the CLI guest image). It waits until the vsock agent is ready. There are no kind-specific parameters beyond the common ones.

Guest, guest-pull, and later VM-stop steps use this running VM. The test's manifest **scope** should be `guest` or `mixed`.

**Success** (default `expected_result` of `exit_code:0`):

- QEMU starts and stays running (it does not exit immediately).
- The guest vsock agent responds. That means the kernel, vsock driver, and exec agent are up.

**Failure** (`exit_code:1`):

- QEMU exits immediately — for example a firmware or SNP launch error such as a bad measurement.
- QEMU stays up but the guest never becomes ready (vsock agent does not respond).

With the default `expected_result`, a successful launch **passes** and either failure **fails** the step. If the test is meant to show that a launch is refused, set `expected_result="exit_code:1"` so a failed launch **passes**.

Example:

```python
Step.for_vm_launch(
    name="Launch SEV-SNP guest",
    type="setup",
    timeout=300,
),
```

### VM stop step

Factory: `Step.for_vm_stop(...)`.

A VM stop step terminates the guest started by `vm_launch`. There are no kind-specific parameters beyond the common ones. After a stop, a later `vm_launch` can start a new VM.

If the test still has a guest running at the end, the harness stops it in a `finally` block. Put an explicit `vm_stop` before any later `setup` step that might fail and skip teardown.

**Success** (default `expected_result` of `exit_code:0`):

- The harness sends SIGTERM to QEMU and the process exits within **timeout** (default 10 seconds).
- If SIGTERM is not enough, it sends SIGKILL and waits again for the same timeout.
- The step stdout is `Guest VM stopped`.

**Failure** (`exit_code:1`, or an error):

- Stopping QEMU raises an OS error (for example the process is already gone and cannot be signaled).
- The process does not exit within the kill window.

With the default `expected_result`, a clean stop **passes**. If the test is meant to show that stop fails, set `expected_result="exit_code:1"`.

Example:

```python
Step.for_vm_stop(
    name="Stop VM",
    type="info",
    timeout=60,
),
```

### Guest-pull step

Factory: `Step.for_guest_pull(...)`.

A guest-pull step copies a file from the guest to the host. A running guest is required (see Guest step).

Kind-specific parameters:

- **guest_src** — Path of the file on the guest.
- **host_dest** — Destination path on the host. A relative path is resolved under the test's artifact directory. An absolute path is used as-is.

The artifact directory is:

```
<artifacts-dir>/<manifest version>/<test level>/<test_name>/
```

The default `<artifacts-dir>` is `./artifacts`. Only hyphens in the test **name** become underscores in that last folder. The certification level is left as written, so `3.0.0-0` stays `3.0.0-0`. A test named `attestation-test` at that level, with `host_dest="report.bin"`, writes:

```
./artifacts/3.0/3.0.0-0/attestation_test/report.bin
```

`attestation-test` is the part that changed, to `attestation_test`.

Later host steps should not hard-code that path. Use `$SEV_VERIFY_ARTIFACT_DIR` instead. Callable steps use `ctx.artifact_dir`.

Example:

```python
Step.for_guest_pull(
    name="Pull report from guest",
    type="required",
    guest_src="report.bin",
    host_dest="report.bin",
    timeout=120,
),
Step.for_host(
    name="Show pulled report",
    type="info",
    command='ls -l "$SEV_VERIFY_ARTIFACT_DIR/report.bin"',
    timeout=30,
),
```

### Callable step

Factory: `Step.for_callable(...)`.

A callable step runs a Python function on the test module instead of a shell command. Use this for comparisons, parsing, or any logic that is not a one-line command.

Kind-specific parameters:

- **handler** — Name of a function defined in the same test module.

The function must take a `StepContext` (`ctx`) and return a `StepHandlerResult(exit_code=..., stdout=..., stderr=...)`. The same **expected_result** rules apply.

`StepContext` is the class the harness passes into every callable handler. It is the in-process equivalent of `$GUEST_PATH` and `$SEV_VERIFY_ARTIFACT_DIR` on host steps: read it instead of hard-coding paths or re-running earlier work.

Fields:

- **test** — The manifest entry for this test (`name`, `level`, `scope`, `host_changes`, and so on).
- **guest_path** — Path to the CLI guest image (same value as `$GUEST_PATH`).
- **step_results** — Results of steps that already ran in this test. The current step is not included.
- **module** — The loaded test module (the same module that defines `steps()` and the handler).
- **artifact_dir** — This test's artifact directory (same location as `$SEV_VERIFY_ARTIFACT_DIR`). Pulled files with a relative `host_dest` land here.
- **profile** — The active `VMProfile` when a VM has been launched; otherwise `None`.
- **launch** — The `VMLaunchResult` for the running guest when a VM is up; otherwise `None`.
- **cli_qemu_binary** — `--qemu-binary` / `--qemu` from the CLI, if set.
- **cli_ovmf_path** — `--ovmf` from the CLI, if set.
- **allow_host_changes** — `True` when the operator passed `--allow-host-changes`.

Return a `StepHandlerResult` with:

- **exit_code** — Compared against `expected_result` (default `exit_code:0`).
- **stdout** / **stderr** — Captured in the step result. `stdout` is also used when `expected_result` is `stdout_contains:<string>`.

Example:

```python
from sev_verify.models import BaseStep, Step, StepContext, StepHandlerResult


def check_hello(ctx: StepContext) -> StepHandlerResult:
    report = ctx.artifact_dir / "report.bin"
    if not report.exists():
        return StepHandlerResult(exit_code=1, stderr=f"missing {report}")
    return StepHandlerResult(exit_code=0, stdout=f"found {report}")


def steps() -> list[BaseStep]:
    return [
        Step.for_callable(
            name="Check hello",
            type="required",
            handler="check_hello",
            timeout=30,
        ),
    ]
```

## VM profile

The VM profile is the other main part of a test that launches a guest. The harness turns it into QEMU arguments. The same profile is reused for every step in the test, so `vm_launch` and `vm_stop` start and stop a guest with that configuration.

Fields:

- **image_path** — Guest image to boot. Always replaced by the CLI guest path at launch, so leaving it empty is fine.
- **qemu_binary** — QEMU executable. Default: `qemu-system-x86_64`. Prefer `--qemu-binary` / `--qemu` on the CLI instead of hard-coding this.
- **ovmf_path** — OVMF firmware `.fd`. Default: search well-known host paths. Prefer `--ovmf` on the CLI instead of hard-coding this.
- **memory_mb** — Guest RAM in MiB. Default: `4096`. Must be positive.
- **guest_error_log** — File for QEMU stderr. Default: `/tmp/guest-error.log`.
- **network_enabled** — User-mode NAT so the guest can reach the network (for example to fetch certificates). Default: `True`.
- **vsock_cid** — Guest vsock CID. Default: `3`. Must be `>= 3`.
- **vsock_port** — Guest vsock port for the exec agent. Default: `5000`.
- **vsock_use_vhost** — Use `vhost-vsock-pci` when `True`, otherwise `virtio-vsock-pci`. Default: `True`.
- **vsock_boot_timeout** — Seconds to wait for the vsock agent after launch. Default: `180`. This is the boot wait; the `vm_launch` step `timeout` does not control it.
- **vsock_connect_timeout** — Seconds to wait when connecting to the agent. Default: `10`.
- **vsock_command_timeout** — Default seconds for a guest command. Default: `300`.
- **vsock_max_response_bytes** — Maximum vsock response size. Default: 16 MiB.
- **host_data** — SEV-SNP `HOST_DATA` (exactly 32 bytes as hex or base64). Default: unset.
- **policy** — SEV-SNP guest policy (int or hex string). Default: unset.
- **auth_key_enabled** — Pass `author-key-enabled=true` to QEMU. Default: `False`.
- **kernel_hashes** — Pass `kernel-hashes=on` to QEMU. Default: `True`.
- **cbitpos** — C-bit position. Default: `51`.
- **reduced_phys_bits** — Reduced physical bits. Default: `1`.

Define a VM profile in one of two ways.

Assign a `VMProfile` instance to a `vm_profile` variable:

```python
from sev_verify.vm_profile import VMProfile

vm_profile = VMProfile(
    image_path="",
    memory_mb=4096,
)
```

Or define a `vm_profile()` function that returns a `VMProfile` instance. Use a function when some launch settings are computed when the test is loaded. The harness calls it with **no arguments**.

```python
import random

from sev_verify.vm_profile import VMProfile


def vm_profile() -> VMProfile:
    memory_mb = random.choice((2048, 4096))
    return VMProfile(
        image_path="",
        memory_mb=memory_mb,
    )
```

`--qemu-binary` / `--qemu` and `--ovmf` override `qemu_binary` and `ovmf_path` after the profile is loaded.

If `vm_profile` is omitted, the harness uses `VMProfile` defaults with the CLI guest image. The test's manifest **scope** should be `guest` or `mixed` when you launch a VM.

### Adding parameters

`VMProfile` does not expose every QEMU option. It covers the settings the **existing** tests need. You can add a field when a new test needs launch options that are not there yet.

1. Add the field to the `VMProfile` class with a suitable name and default.
2. Thread it into the QEMU argv in `build_qemu_command` in `sev_verify/vm_profile.py`.
3. If the field belongs on the SEV-SNP guest object (`-object sev-snp-guest,...`), update `_build_sev_snp_guest_object` instead (or as well).

## Current limitations

The harness still has some limitations. We are working on them so tests can cover more cases and so writing tests stays simple. Known limitations:

- **One `VMProfile` and one running VM per test.** Some tests need two or more guests, with the same profile or different ones. Today a test module can declare only one `vm_profile`. You can `vm_stop` and `vm_launch` again, but that reuses the same profile. Two guests cannot run at the same time.
- **Callable handlers do not take extra parameters.** The harness calls `handler(ctx)` with a `StepContext` only. There is no way to pass extra arguments into the function, which limits how flexible this step kind is.
- **Shared helpers are added as tests need them.** When the same logic shows up in more than one test, put it in a module under `sev_verify/cert_tests/common` and import it. There is no separate library release for this.
