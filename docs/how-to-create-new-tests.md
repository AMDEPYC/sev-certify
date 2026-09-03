The sev-certify repository has an internal test harness called `sev_verify`. `sev_verify` is a Python application that can be used independently of the sev-certify infrastructure. This page is the short path for adding a test.

Step kinds, `VMProfile` fields, and current limits are in the [sev_verify reference](sev-verify.md). How to invoke the harness is in the [`sev_verify` README](../sev_verify/README.md).

## Contents

- [Add a test](#add-a-test)
  - [Create the module](#create-the-module)
  - [Define steps](#define-steps)
  - [VM profile](#vm-profile)
  - [Manifest entry](#manifest-entry)
  - [Run the test](#run-the-test)
  - [Templates](#templates)

# Add a test

This assumes you already have a test plan: you know how you will exercise the feature and what prerequisites it needs.

First, use the [certificate generation table](sev-verify.md#organization) to determine which generation the test belongs to. Within that generation, add the test at the newest available certificate level. **Do not add tests to older levels.** New levels are added by the maintainers when a level is released.

## Create the module

Create the Python module under that level folder. For level `3.0.0-1` the path is `sev_verify/cert_tests/c3_0/c3_0_0_1/my_new_test.py`. The filename must match `<test_name>` in the manifest **module** field below. The generation folders and `common/` are described in the [reference](sev-verify.md#organization).

## Define steps

Define `steps()` so it returns a list of `BaseStep` objects:

```python
from sev_verify.models import BaseStep, Step


def steps() -> list[BaseStep]:
    """Example test."""
    return [
        Step.for_host(
            name="Hello World",
            type="setup",
            command='echo "hello world"',
            timeout=60,
        ),
    ]
```

`Step.for_host(...)` is a host step. There are six step kinds (`host`, `guest`, `vm_launch`, `vm_stop`, `guest_pull`, `callable`). See [Steps](sev-verify.md#steps) in the reference.

## VM profile

If the test launches a guest (`scope` is `guest` or `mixed`), also define `vm_profile`. `image_path` is replaced by the CLI guest image at launch.

```python
from sev_verify.vm_profile import VMProfile

vm_profile = VMProfile(
    image_path="",
    memory_mb=4096,
)
```

Field list and the function form are in [VM profile](sev-verify.md#vm-profile).

## Manifest entry

Append a `[[tests]]` entry to that generation's `manifest.toml`. Omit `host_changes` unless the test can change the host.

```toml
[[tests]]
name = "my-new-test"
description = "Verify the feature under test"
module = "cert_tests.c3_0.c3_0_0_1.my_new_test"
scope = "mixed"
level = "3.0.0-1"
host_changes = true
```

`scope` is `host`, `guest`, or `mixed`. Set `host_changes = true` only if the test can leave the host in a changed state. The rest of the fields are in [Manifest](sev-verify.md#manifest).

## Run the test

Run just that level:

```bash
python3 -m sev_verify /path/to/guest.efi -v 3.0.0-1
```

`path_to_guest` is required. `-v 3.0.0-1` runs that exact level (`3.0` runs the whole generation, `3.0.0` runs every `3.0.0-*` level).

Pin QEMU and OVMF when the host defaults are not the firmware under test:

```bash
python3 -m sev_verify /path/to/guest.efi --qemu-binary /opt/qemu/bin/qemu-system-x86_64 --ovmf /usr/share/ovmf/OVMF.amdsev.fd -v 3.0.0-1
```

`--qemu` is a short form of `--qemu-binary`. If the manifest sets `host_changes = true`, also pass `--allow-host-changes`. Results go under `results/`. Per-test files go under `./artifacts`. The full flag list is in the [`sev_verify` README](../sev_verify/README.md).

## Templates

Use existing tests as templates:

- Mixed launch and attestation: `sev_verify/cert_tests/c3_0/c3_0_0_0/attestation_test.py`
- Host changes gated on `--allow-host-changes`: `sev_verify/cert_tests/c3_0/c3_0_0_1/snphost_config_commit.py`
