"""Test runner: load modules, execute steps, collect results."""

from __future__ import annotations

import os
import subprocess
import time
from importlib import import_module
from pathlib import Path
from types import ModuleType
from dataclasses import replace

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from .guest_vsock import GuestVsockError, pull_guest_file_to_host, run_guest_command
from .models import (
    Step,
    StepContext,
    StepHandlerResult,
    StepResult,
    TestDefinition,
)
from .vm_profile import VMProfile, VMLaunchError, VMLaunchResult, stop_vm


def _check_expected_values(step: Step, exit_code: int, stdout: str) -> bool:
    """Check a step's expected_result against exit code and stdout."""
    kind, _, value = step.expected_result.partition(":")
    if kind == "exit_code":
        return exit_code == int(value)
    if kind == "stdout_contains":
        return value in stdout
    return False


def _check_expected(step: Step, proc: subprocess.CompletedProcess[str]) -> bool:
    """Check a step's expected_result against the process outcome."""
    return _check_expected_values(step, proc.returncode, proc.stdout or "")


def import_test_module(test: TestDefinition) -> ModuleType:
    """Import the Python module for a test definition."""
    fq_module = f"sev_verify.{test.module}"
    return import_module(fq_module)


def _artifact_path_segment(segment: str) -> str:
    """Single path component safe for POSIX directories."""
    t = (segment or "unknown").strip()
    if not t:
        return "unknown"
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in t)


def test_artifact_dir(
    artifacts_root: Path,
    certification_version: str | None,
    test: TestDefinition,
) -> Path:
    """
    Per-test directory for logs, pulled binaries, and analysis files.

    With a certification manifest (``certification_version`` is the manifest
    ``version``, e.g. ``\"3.0\"``)::

        <artifacts_root>/<version>/<test.level>/<test_name>/

    ``test.name`` hyphens become underscores (``vm-launch-attest`` → ``vm_launch_attest``).
    If ``test.level`` is empty, ``no-level`` is used.

    For prerequisite-only runs (``certification_version`` is None)::

        <artifacts_root>/prereqs/<test_name>/
    """
    test_name = _artifact_path_segment(test.name.replace("-", "_"))
    if certification_version is not None:
        cert = _artifact_path_segment(certification_version)
        level = _artifact_path_segment(test.level) if test.level else "no-level"
        return artifacts_root / cert / level / test_name
    return artifacts_root / "prereqs" / test_name


def load_test_execution_plan(test: TestDefinition) -> tuple[list[Step], VMProfile | None]:
    """
    Import the test module once and return steps plus optional VM profile.

    Tests that use ``vm_launch``, ``vm_stop``, ``guest``, or ``guest_pull`` steps may define
    ``vm_profile`` as a callable returning :class:`~sev_verify.vm_profile.VMProfile`,
    or as a :class:`VMProfile` instance. ``image_path`` is always replaced by the
    CLI guest path at launch.
    """
    mod = import_test_module(test)
    steps = mod.steps()
    declared: VMProfile | None = None
    raw = getattr(mod, "vm_profile", None)
    if raw is not None:
        declared = raw() if callable(raw) else raw
    return steps, declared


def load_steps(test: TestDefinition) -> list[Step]:
    """Import a test module and call its steps() function."""
    return load_test_execution_plan(test)[0]


def load_vm_profile(test: TestDefinition) -> VMProfile | None:
    """Return VM launch settings from the test module, if provided."""
    return load_test_execution_plan(test)[1]


def run_step(step: Step, guest_path: Path, artifact_dir: Path | None = None) -> StepResult:
    """Execute a single host step and return its result."""
    # Build the command line.  The guest_path is exposed as $GUEST_PATH
    # so scripts can use it, and also passed as $1 when the command is
    # an executable file (not a shell expression).
    env = {**os.environ, "GUEST_PATH": str(guest_path)}
    if artifact_dir is not None:
        env["SEV_VERIFY_ARTIFACT_DIR"] = str(artifact_dir)

    cmd_path = Path(step.command)
    if cmd_path.is_file():
        args: str | list[str] = [str(cmd_path), str(guest_path)]
        shell = False
    else:
        args = step.command
        shell = True

    start = time.monotonic()
    try:
        proc = subprocess.run(
            args,
            timeout=step.timeout,
            capture_output=True,
            text=True,
            shell=shell,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - start) * 1000)
        return StepResult(
            step=step,
            result="error",
            stderr=f"Timed out after {step.timeout}s",
            duration_ms=duration_ms,
        )
    except OSError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return StepResult(
            step=step,
            result="error",
            stderr=str(exc),
            duration_ms=duration_ms,
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    passed = _check_expected(step, proc)

    return StepResult(
        step=step,
        result="pass" if passed else "fail",
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration_ms=duration_ms,
    )


def run_vm_launch_step(
    step: Step, profile: VMProfile,
) -> tuple[StepResult, VMLaunchResult | None]:
    """Start the guest described by ``profile``. Returns ``(StepResult, launch or None)``."""
    start = time.monotonic()
    try:
        launch = profile.vm_launch()
    except VMLaunchError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return (
            StepResult(
                step=step,
                result="error",
                stderr=str(exc),
                duration_ms=duration_ms,
            ),
            None,
        )
    duration_ms = int((time.monotonic() - start) * 1000)
    exit_code = 0 if launch.ok else 1
    passed = _check_expected_values(step, exit_code, launch.message)
    return (
        StepResult(
            step=step,
            result="pass" if passed else "fail",
            exit_code=exit_code,
            stdout=launch.message if launch.ok else None,
            stderr=None if launch.ok else launch.message,
            duration_ms=duration_ms,
        ),
        launch,
    )


def run_vm_stop_step(step: Step, launch: VMLaunchResult) -> StepResult:
    """Terminate QEMU for ``launch`` (``stop_vm``; ``step.timeout`` is the wait/kill window)."""
    start = time.monotonic()
    try:
        stop_vm(launch, timeout=float(step.timeout))
    except OSError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return StepResult(
            step=step,
            result="error",
            stderr=str(exc),
            duration_ms=duration_ms,
        )
    duration_ms = int((time.monotonic() - start) * 1000)
    msg = "Guest VM stopped"
    passed = _check_expected_values(step, 0, msg)
    return StepResult(
        step=step,
        result="pass" if passed else "fail",
        exit_code=0 if passed else 1,
        stdout=msg,
        duration_ms=duration_ms,
    )


def run_guest_step(step: Step, profile: VMProfile) -> StepResult:
    """Execute a guest step over AF_VSOCK (same expected_result rules as host)."""
    start = time.monotonic()
    try:
        gcr = run_guest_command(
            profile,
            step.command,
            timeout=float(step.timeout),
            wait_for_ready=False,
        )
    except GuestVsockError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return StepResult(
            step=step,
            result="error",
            stderr=str(exc),
            duration_ms=duration_ms,
        )

    passed = _check_expected_values(step, gcr.exit_code, gcr.stdout)
    return StepResult(
        step=step,
        result="pass" if passed else "fail",
        exit_code=gcr.exit_code,
        stdout=gcr.stdout,
        stderr=gcr.stderr,
        duration_ms=gcr.duration_ms,
    )


def run_guest_pull_step(
    step: Step, profile: VMProfile, artifact_dir: Path | None = None,
) -> StepResult:
    """Copy ``step.guest_src`` from the guest to ``step.host_dest`` on the host."""
    start = time.monotonic()
    host_path = Path(step.host_dest)
    if artifact_dir is not None and not host_path.is_absolute():
        host_path = artifact_dir / host_path
    try:
        pull_guest_file_to_host(
            profile,
            step.guest_src,
            host_path,
            timeout=float(step.timeout),
        )
    except GuestVsockError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return StepResult(
            step=step,
            result="error",
            stderr=str(exc),
            duration_ms=duration_ms,
        )
    duration_ms = int((time.monotonic() - start) * 1000)
    passed = _check_expected_values(step, 0, "")
    return StepResult(
        step=step,
        result="pass" if passed else "fail",
        exit_code=0,
        duration_ms=duration_ms,
    )


def run_callable_step(step: Step, ctx: StepContext) -> StepResult:
    """Run ``ctx.module.<step.handler>(ctx)`` with a wall-clock timeout (thread pool)."""
    start = time.monotonic()
    fn = getattr(ctx.module, step.handler, None)
    if fn is None:
        duration_ms = int((time.monotonic() - start) * 1000)
        return StepResult(
            step=step,
            result="error",
            stderr=f"Test module has no attribute {step.handler!r}",
            duration_ms=duration_ms,
        )
    if not callable(fn):
        duration_ms = int((time.monotonic() - start) * 1000)
        return StepResult(
            step=step,
            result="error",
            stderr=f"Module attribute {step.handler!r} is not callable",
            duration_ms=duration_ms,
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(fn, ctx)
            try:
                hr = fut.result(timeout=float(step.timeout))
            except FuturesTimeoutError:
                duration_ms = int((time.monotonic() - start) * 1000)
                return StepResult(
                    step=step,
                    result="error",
                    stderr=f"Callable {step.handler!r} timed out after {step.timeout}s",
                    duration_ms=duration_ms,
                )
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return StepResult(
            step=step,
            result="error",
            stderr=str(exc),
            duration_ms=duration_ms,
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    if not isinstance(hr, StepHandlerResult):
        return StepResult(
            step=step,
            result="error",
            stderr=(
                f"Handler {step.handler!r} must return StepHandlerResult, "
                f"got {type(hr).__name__}"
            ),
            duration_ms=duration_ms,
        )

    passed = _check_expected_values(step, hr.exit_code, hr.stdout)
    return StepResult(
        step=step,
        result="pass" if passed else "fail",
        exit_code=hr.exit_code,
        stdout=hr.stdout or None,
        stderr=hr.stderr or None,
        duration_ms=duration_ms,
    )


def effective_vm_profile(
    declared: VMProfile | None,
    guest_path: Path,
    *,
    qemu_binary: str | None = None,
    ovmf_path: str | None = None,
) -> VMProfile:
    """
    Merge CLI guest path (and optional QEMU / OVMF overrides) into a profile.

    The ``path_to_guest`` argument always supplies the bootable guest image;
    ``declared`` supplies QEMU, vsock, and SEV-SNP options from the test module.
    Non-``None`` ``qemu_binary`` / ``ovmf_path`` override the merged profile.
    """

    if declared is None:
        base = VMProfile(image_path=str(guest_path))
    else:
        base = replace(declared, image_path=str(guest_path))

    if qemu_binary is not None:
        base = replace(base, qemu_binary=qemu_binary)
    if ovmf_path is not None:
        base = replace(base, ovmf_path=ovmf_path)
    return base
