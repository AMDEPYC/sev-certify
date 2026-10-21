"""ciphertext_test: Verify ciphertext hiding enablement and guest launch.

Ciphertext hiding requires host-side configuration, which makes it more
involved to test.

Enablement has two prerequisites, which we check using SNP_PLATFORM_STATUS:

1. BIOS support and configuration (CIPHERTEXT_HIDING_DRAM_CAP).
2. Kernel enablement (CIPHERTEXT_HIDING_DRAM_EN).

Kernel enablement requires loading kvm_amd with ciphertext_hiding_asids=<N>.
If kvm_amd is a loadable module, we can apply this parameter without rebooting
by unloading and reloading it. All KVM VMs must be stopped first. Unload is
only forced if allowed host changes are enabled. 
The positive test enables exactly one ASID. The original ASID
count, including zero when the feature was disabled, is restored afterward.

With ciphertext hiding enabled, the reserved ASIDs are allocated to SEV-SNP
guests. The BIOS configuration divides ASIDs between SEV and SEV-ES/SEV-SNP.
The kernel parameter further partitions the latter pool into separate SEV-ES
and SEV-SNP ranges. SEV-SNP guests launched in this configuration receive
ciphertext hiding protection.

The guest can check the ciphertext hiding indication in the attestation
report's platform_info field. The guest policy can also require ciphertext
hiding: launching a guest with this requirement must fail if ciphertext
hiding is disabled on the host.

Testing steps:

Pre-flight:
- Read SNP_PLATFORM_STATUS through snphost and announce which case will run.
- Without CIPHERTEXT_HIDING_DRAM_CAP, stop. BIOS support is missing.
- With the capability but the feature disabled, and without
  --allow-host-changes, stop. The test will not enable it on its own.

With --allow-host-changes, and the platform capable:
- If ciphertext hiding is enabled, disable it, then run the negative test.
- If it is already disabled, run the negative test immediately.
- Negative test: a guest whose policy requires ciphertext hiding must be rejected.
- Enable ciphertext hiding with ciphertext_hiding_asids=1 and run the positive test.
- Positive test: the guest launches and its attestation report shows ciphertext
  hiding in platform_info.
- Restore the original ciphertext_hiding_asids value. Zero means disabled.

Without --allow-host-changes:
- Run only the positive test, and only when ciphertext hiding is already enabled.
- Do not disable the feature.

Test cases not currently covered:

ASID exhaustion:
- Verify that the number of concurrently running SEV-SNP VMs cannot exceed
  the ASID count specified by ciphertext_hiding_asids=<N>.
- The harness does not currently support launching multiple VMs per test,
  so this case is not covered.

Direct memory inspection:
- We investigated reading private guestmemfd pages to verify that host reads
  return 0xff bytes when ciphertext hiding is enabled, but we did not find
  a userspace mechanism for accessing those private pages.
- Modifying the kernel to expose this access is beyond the scope of
  sev-verify.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sev_verify import attestation_report
from sev_verify.models import BaseStep, Step, StepContext, StepHandlerResult
from sev_verify.vm_profile import VMProfile

# Guest policy: SMT allowed (bit 16), reserved must-be-one (bit 17), and
# CIPHERTEXT_HIDING_DRAM (bit 24) so the launch requires ciphertext hiding.
POLICY_CIPHERTEXT_HIDING_DRAM = 1 << 24
CIPHERTEXT_POLICY = (1 << 16) | (1 << 17) | POLICY_CIPHERTEXT_HIDING_DRAM

# ATTESTATION_REPORT PLATFORM_INFO bit 4: ciphertext hiding enabled.
PLATFORM_INFO_CIPHERTEXT_HIDING_EN = 1 << 4


_KVM_AMD_SYSFS = Path("/sys/module/kvm_amd")
_ASIDS_PARAM = _KVM_AMD_SYSFS / "parameters" / "ciphertext_hiding_asids"

vm_profile = VMProfile(
    image_path="",
    memory_mb=2048,
    policy=CIPHERTEXT_POLICY,
)


# ── SNP_PLATFORM_STATUS via snphost ─────────────────────────────

# Messages emitted by the snphost "Ciphertext Hiding" optional-feature check,
# which reads CIPHERTEXT_HIDING_DRAM_CAP / _EN from SNP_PLATFORM_STATUS.
_MSG_ENABLED = "Ciphertext Hiding is available and enabled"
_MSG_CAP_ONLY = "Ciphertext Hiding is available, but not enabled"
_MSG_UNAVAILABLE = "Ciphertext Hiding is unavailable"


class CiphertextStatusError(Exception):
    """SNP_PLATFORM_STATUS ciphertext hiding bits could not be read."""


@dataclass(frozen=True)
class CiphertextStatus:
    cap: bool
    enabled: bool
    command: str

    def __str__(self) -> str:
        return (
            f"CIPHERTEXT_HIDING_DRAM_CAP={int(self.cap)} "
            f"CIPHERTEXT_HIDING_DRAM_EN={int(self.enabled)}"
        )


def _read_ciphertext_status() -> CiphertextStatus:
    """Read the ciphertext hiding bits of SNP_PLATFORM_STATUS through snphost."""
    proc = subprocess.run(
        ["snphost", "ok", "--features", "--output", "json"],
        capture_output=True, text=True, timeout=60,
    )
    cmd = shlex.join(proc.args)
    # snphost ok exits 1 whenever any check fails, including the optional
    # ciphertext check when it is disabled, so judge by the output instead.
    try:
        tests = json.loads(proc.stdout)["tests"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CiphertextStatusError(
            f"could not parse `{cmd}` output ({exc}); stderr: {proc.stderr.strip()}"
        ) from exc

    message = None
    for test in tests:
        if "Ciphertext Hiding" in str(test.get("name", "")):
            message = str(test.get("message", ""))
            break
    if message is None:
        raise CiphertextStatusError(
            f"`{cmd}` did not report a Ciphertext Hiding check; an snphost "
            "build that reads the SNP_PLATFORM_STATUS ciphertext hiding bits "
            "is required"
        )

    if message == _MSG_ENABLED:
        return CiphertextStatus(cap=True, enabled=True, command=cmd)
    if message == _MSG_CAP_ONLY:
        return CiphertextStatus(cap=True, enabled=False, command=cmd)
    if message == _MSG_UNAVAILABLE:
        return CiphertextStatus(cap=False, enabled=False, command=cmd)
    raise CiphertextStatusError(f"`{cmd}` Ciphertext Hiding check failed: {message}")


# ── Pre-flight ──────────────────────────────────────────────────


def report_ciphertext_hiding_status(
    ctx: StepContext, status: CiphertextStatus | None, asids: int | None
) -> StepHandlerResult:
    """Announce which ciphertext hiding case this run will execute."""
    if status is None:
        return StepHandlerResult(
            exit_code=1,
            stderr="Failure reading SNP_PLATFORM_STATUS",
        )

    if not status.cap:
        return StepHandlerResult(
            exit_code=1,
            stderr=(
                "Platform is not capable of ciphertext hiding. Make sure "
                "ciphertext hiding is enabled in BIOS if platform is a Turin."
            ),
            command=status.command,
        )

    if not status.enabled:
        if ctx.allow_host_changes:
            return StepHandlerResult(
                exit_code=0,
                stdout=(
                    "Platform is capable of ciphertext hiding. Ciphertext hiding "
                    "is disabled. Will run the negative test, enable 1 ASID for "
                    "the positive test, then restore the disabled setting."
                ),
                command=status.command,
            )
        return StepHandlerResult(
            exit_code=1,
            stderr=(
                "Platform is capable of ciphertext hiding. Ciphertext hiding is "
                "disabled in the kernel, but host configuration can't be changed. "
                "Use --allow-host-changes to allow host changes."
            ),
            command=status.command,
        )

    if ctx.allow_host_changes:
        if asids < 1:
            return StepHandlerResult(
                exit_code=1,
                stderr=(
                    "Ciphertext hiding is enabled, but ciphertext_hiding_asids "
                    "could not be read, so the original setting cannot be restored."
                ),
                command=status.command,
            )
        return StepHandlerResult(
            exit_code=0,
            stdout=(
                "Platform is capable and has ciphertext hiding enabled "
                f"({asids} ASIDs). Will disable it for the negative test, "
                "enable 1 ASID for the positive test, then restore "
                f"{asids} ASIDs."
            ),
            command=status.command,
        )
    return StepHandlerResult(
        exit_code=0,
        stdout=(
            "Platform is capable and has ciphertext hiding enabled. Will not "
            "disable it for testing. Negative case is not applicable; use "
            "--allow-host-changes to allow host changes."
        ),
        command=status.command,
    )


# ── kvm_amd interactions ──────────────────────────────────────────────


def _run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _current_asids() -> int:
    """ASID count kvm_amd is using for ciphertext hiding. Zero means disabled."""
    try:
        raw = _ASIDS_PARAM.read_text().strip()
    except OSError:
        return 0
    return int(raw) if raw.isdigit() else 0


def reload_kvm_amd(ctx: StepContext, asids: int) -> StepHandlerResult:
    """Reload kvm_amd so ciphertext hiding uses ``asids`` SNP ASIDs.

    Zero disables it. Unloading is never forced. On a failed reload the
    previous ASID count is loaded again.
    """
    if not ctx.allow_host_changes:
        return StepHandlerResult(
            exit_code=1,
            stderr=(
                "Reloading kvm_amd changes the host. Pass "
                "--allow-host-changes to permit it."
            ),
        )
    if asids < 0:
        return StepHandlerResult(exit_code=1, stderr=f"ASID count must be >= 0, got {asids}")

    loaded = (_KVM_AMD_SYSFS / "initstate").exists()
    if _KVM_AMD_SYSFS.exists() and not loaded:
        return StepHandlerResult(
            exit_code=1,
            stderr=(
                "kvm_amd is not a loadable module; set "
                f"kvm_amd.ciphertext_hiding_asids={asids} on the kernel "
                "command line and reboot instead."
            ),
        )

    # Read parameters before unloading; sysfs disappears with the module.
    if asids >=0:
        requested = ["modprobe", "kvm_amd", f"ciphertext_hiding_asids={asids}"]
    else:
        requested = ["modprobe", "kvm_amd"]

    fallback = ["modprobe", "kvm_amd"]

    if loaded:
        unload = _run(["modprobe", "-r", "kvm_amd"])
        if unload.returncode != 0:
            return StepHandlerResult(
                exit_code=1,
                stderr=f"modprobe -r kvm_amd failed: {unload.stderr.strip()}",
                command=shlex.join(unload.args),
            )

    load = _run(requested)
    if load.returncode != 0:
        restore = _run(fallback)
        restored = "restored" if restore.returncode == 0 else "NOT restored"
        return StepHandlerResult(
            exit_code=1,
            stderr=(
                f"{shlex.join(requested)} failed: {load.stderr.strip()} "
                f"(previous kvm_amd configuration {restored})"
            ),
            command=shlex.join(requested),
        )

    try:
        effective = _ASIDS_PARAM.read_text().strip()
    except OSError:
        effective = "unknown"

    label = "disabled" if asids == 0 else f"{asids} ASIDs"
    return StepHandlerResult(
        exit_code=0,
        stdout=f"kvm_amd reloaded with ciphertext hiding {label} (effective: {effective})",
        command=shlex.join(requested),
    )


# ── Verification ────────────────────────────────────────────────


def verify_ciphertext_state(_ctx: StepContext, enabled: bool) -> StepHandlerResult:
    """Require CIPHERTEXT_HIDING_DRAM_EN to equal ``enabled``."""
    try:
        status = _read_ciphertext_status()
    except CiphertextStatusError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))
    if status.enabled != enabled:
        expect = "enabled" if enabled else "disabled"
        return StepHandlerResult(
            exit_code=1,
            stderr=f"FAIL: ciphertext hiding is not {expect}: {status}",
            command=status.command,
        )
    return StepHandlerResult(exit_code=0, stdout=str(status), command=status.command)


def verify_report_ciphertext_hiding(ctx: StepContext) -> StepHandlerResult:
    """Check the report's policy requires and PLATFORM_INFO reports ciphertext hiding."""
    try:
        report = attestation_report.read(
            ctx.artifact_dir / "report.bin",
            generation=attestation_report.host_generation(),
        )
    except attestation_report.ReportError as exc:
        return StepHandlerResult(exit_code=1, stderr=str(exc))

    errors = []
    if (report.policy & POLICY_CIPHERTEXT_HIDING_DRAM) == 0:
        errors.append(
            f"policy {hex(report.policy)} does not have CIPHERTEXT_HIDING_DRAM (bit 24) set"
        )
    if (report.platform_info & PLATFORM_INFO_CIPHERTEXT_HIDING_EN) == 0:
        errors.append(
            f"platform_info {hex(report.platform_info)} does not have "
            "ciphertext hiding enabled (bit 4) set"
        )
    summary = f"policy={hex(report.policy)} platform_info={hex(report.platform_info)}"
    if errors:
        return StepHandlerResult(
            exit_code=1, stderr="\n".join(["FAIL: " + e for e in errors] + [summary])
        )
    return StepHandlerResult(
        exit_code=0, stdout=f"Report shows ciphertext hiding enabled ({summary})"
    )


def steps(ctx: StepContext) -> list[BaseStep]:
    # Platform state is read here, not by a step: steps()
    # We use pre-flight steps to communicate the result of the read.
    try:
        status = _read_ciphertext_status()
    except CiphertextStatusError:
        status = None

    original_asids = _current_asids()

    pre_flight_steps = [
        Step.for_callable(
            name="Check ciphertext enablement using SNP_PLATFORM_STATUS",
            type="info",
            handler="report_ciphertext_hiding_status",
            args={"status": status, "asids": original_asids},
            timeout=90,
        )
    ]

    steps_list: list[BaseStep] = list(pre_flight_steps)
    
    # Pre-flight is a setup step and stops the run. Return it alone so the
    # log is not a list of skipped steps.
    if status is None or not status.cap:
        return steps_list

    # For some reason ciphertext hiding is enabled, but we can't get the
    # number of original asids
    if status.enabled and ctx.allow_host_changes and original_asids < 1:
        return steps_list

    # Steps to test that ciphertext hiding is enabled correctly
    positive_test = [
        Step.for_vm_launch(
            name="Launch guest requiring ciphertext hiding",
            guest_id="ciphertext-hiding-vm",
            type="required",
            timeout=300,
        ).add_hint(
            "Address already in use",
            "A previous VM may still be running. "
            "Try: sudo kill $(pgrep -f 'qemu.*guest-cid')",
        ),
        Step.for_guest(
            name="Get attestation report with snpguest",
            type="required",
            command="snpguest report report.bin request.bin --random",
            timeout=300,
        ),
        Step.for_guest_pull(
            name="Pull report from guest",
            type="required",
            guest_src="report.bin",
            host_dest="report.bin",
            timeout=120,
        ),
        Step.for_callable(
            name="Verify ciphertext hiding in report",
            type="required",
            handler="verify_report_ciphertext_hiding",
            timeout=30,
        ),
        Step.for_vm_stop(
            name="Stop VM",
            type="info",
            timeout=60,
        ),
    ]

    # Steps to follow to make sure ciphertext hiding can't be used if disabled.
    negative_test = [
        Step.for_vm_launch(
            name="Launch with ciphertext hiding disabled (expect rejection)",
            guest_id="negative-ciphertext-disabled-vm",
            type="required",
            # Rejected at launch start, either by KVM policy validation or by
            # firmware POLICY_FAILURE; matching it rules out a boot timeout.
            expected_result="stdout_contains:SNP_LAUNCH_START",
            timeout=300,
        ),
        Step.for_vm_stop(
            name="Stop VM (after rejected launch)",
            type="info",
            timeout=60,
        ),
    ]
    
    # Host changes are not allowed.
    # If ciphertext is enabled, then test it for positive case.
    # If ciphertext is disabled, then no test will run, a negative case ONLY is not useful. 
    if not ctx.allow_host_changes:
        if status.enabled:
            steps_list += positive_test
        return steps_list

    enable_ciphertext = [
                Step.for_callable(
                    name="Enable ciphertext hiding by reloading kvm_amd with ciphertext_hiding_asids=1",
                    type="setup",
                    handler="reload_kvm_amd",
                    args={"asids": 1},
                    timeout=120,
                ).add_hint("in use", "Stop all running KVM VMs before reloading kvm_amd."),
                Step.for_callable(
                    name=f"Verify ciphertext hiding is enabled",
                    type="required",
                    handler="verify_ciphertext_state",
                    args={"enabled": True},
                    timeout=90,
                )
            ]

    disable_ciphertext = [
                Step.for_callable(
                    name="Disable ciphertext hiding by reloading kvm_amd with ciphertext_hiding_asids=0",
                    type="setup",
                    handler="reload_kvm_amd",
                    args={"asids": 0},
                    timeout=120,
                ).add_hint("in use", "Stop all running KVM VMs before reloading kvm_amd."),
                Step.for_callable(
                    name=f"Verify ciphertext hiding is disabled",
                    type="required",
                    handler="verify_ciphertext_state",
                    args={"enabled": False},
                    timeout=90,
                )
            ]

    restore_ciphertext = [
                Step.for_callable(
                    name=f"Restore ciphertext hiding by reloading kvm_amd with ciphertext_hiding_asids={original_asids}",
                    type="setup",
                    handler="reload_kvm_amd",
                    args={"asids": original_asids},
                    timeout=120,
                ).add_hint("in use", "Stop all running KVM VMs before reloading kvm_amd."),
                Step.for_callable(
                    name=f"Verify ciphertext hiding is enabled",
                    type="required",
                    handler="verify_ciphertext_state",
                    args={"enabled": True},
                    timeout=90,
                )
            ]

    # Host changes are allowed
    if ctx.allow_host_changes:
        # ciphertext is enabled
        if status.enabled:
            # Run positive test first
            steps_list += positive_test

            # Disable ciphertext hiding
            steps_list += disable_ciphertext

            # negative test

            steps_list += negative_test

            # Reset ciphertext hiding

            steps_list += restore_ciphertext
        # ciphertext is disabled
        else:
            # Run negative test first
            steps_list += negative_test

            # Enable Ciphertext hiding

            steps_list += enable_ciphertext

            # Positive test

            steps_list += positive_test

            # Disable ciphertext hiding

            steps_list += disable_ciphertext

    return steps_list