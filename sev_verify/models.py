"""Data model dataclasses for sev_verify."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType
from pathlib import Path
from typing import Literal, get_args


# Certification / failure-handling semantics (unchanged field name: ``type``)
StepSeverity = Literal["setup", "required", "info"]
# Backward-compatible alias (severity only; use StepKind for host/guest/vm_launch/…)
StepType = StepSeverity

# What the harness actually does for this step
StepKind = Literal["vm_launch", "vm_stop", "host", "guest", "guest_pull", "callable"]

Scope = Literal["host", "guest", "mixed"]


@dataclass
class Step:
    """A single executable step within a test.

    Use ``kind="callable"`` and ``handler`` for Python checks; see
    :class:`StepContext` and :class:`StepHandlerResult`.
    Use ``kind="vm_launch"`` / ``kind="vm_stop"`` to start or terminate the guest VM.
    """

    name: str
    type: StepSeverity
    kind: StepKind
    command: str = ""
    # For kind == "guest_pull": copy guest_src on the guest to host_dest on the host.
    guest_src: str = ""
    host_dest: str = ""
    # For kind == "callable": name of ``(ctx: StepContext) -> StepHandlerResult`` on the test module.
    handler: str = ""
    expected_result: str = "exit_code:0"  # e.g. "exit_code:0", "stdout_contains:PASS"
    timeout: int = 60

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Step.name must not be empty")
        if self.type not in get_args(StepSeverity):
            raise ValueError(
                f"Step {self.name!r}: invalid type {self.type!r}; "
                f"expected one of {get_args(StepSeverity)}"
            )
        if self.kind not in get_args(StepKind):
            raise ValueError(
                f"Step {self.name!r}: invalid kind {self.kind!r}; "
                f"expected one of {get_args(StepKind)}"
            )
        if self.timeout <= 0:
            raise ValueError(
                f"Step {self.name!r}: timeout must be positive, got {self.timeout}"
            )

        if self.kind in ("vm_launch", "vm_stop"):
            if self.command:
                raise ValueError(
                    f"Step {self.name!r}: {self.kind} steps must use an empty command"
                )
            if self.guest_src or self.host_dest:
                raise ValueError(
                    f"Step {self.name!r}: {self.kind} steps must not set guest_src/host_dest"
                )
        elif self.kind == "host":
            if not self.command:
                raise ValueError(f"Step {self.name!r}: host steps require a non-empty command")
            if self.guest_src or self.host_dest:
                raise ValueError(
                    f"Step {self.name!r}: host steps must not set guest_src/host_dest"
                )
        elif self.kind == "guest":
            if not self.command:
                raise ValueError(f"Step {self.name!r}: guest steps require a non-empty command")
            if self.guest_src or self.host_dest:
                raise ValueError(
                    f"Step {self.name!r}: guest steps must not set guest_src/host_dest"
                )
        elif self.kind == "guest_pull":
            if not self.guest_src:
                raise ValueError(
                    f"Step {self.name!r}: guest_pull steps require guest_src (path on guest)"
                )
            if not self.host_dest:
                raise ValueError(
                    f"Step {self.name!r}: guest_pull steps require host_dest (path on host)"
                )
        elif self.kind == "callable":
            if not self.handler:
                raise ValueError(
                    f"Step {self.name!r}: callable steps require a non-empty handler "
                    f"(function name on the test module)"
                )
            if self.command or self.guest_src or self.host_dest:
                raise ValueError(
                    f"Step {self.name!r}: callable steps must not set command, guest_src, or host_dest"
                )

        if self.kind != "callable" and self.handler:
            raise ValueError(
                f"Step {self.name!r}: handler is only allowed when kind is 'callable', not {self.kind!r}"
            )

        kind, sep, value = self.expected_result.partition(":")
        if kind not in ("exit_code", "stdout_contains") or not sep:
            raise ValueError(
                f"Step {self.name!r}: invalid expected_result {self.expected_result!r}; "
                f"expected 'exit_code:<int>' or 'stdout_contains:<string>'"
            )
        if kind == "exit_code":
            try:
                int(value)
            except ValueError as exc:
                raise ValueError(
                    f"Step {self.name!r}: exit_code value must be an integer, "
                    f"got {value!r}"
                ) from exc


@dataclass
class TestDefinition:
    """A test declared in the TOML manifest."""

    name: str
    module: str  # dotted module path, e.g. "cert_tests.common.snphost_ok"
    scope: Scope
    description: str = ""
    level: str = ""  # certification level, e.g. "3.0.0-0"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("TestDefinition.name must not be empty")
        if not self.module:
            raise ValueError(f"TestDefinition {self.name!r}: module must not be empty")
        if self.scope not in get_args(Scope):
            raise ValueError(
                f"TestDefinition {self.name!r}: invalid scope {self.scope!r}; "
                f"expected one of {get_args(Scope)}"
            )

    @property
    def requires_vm(self) -> bool:
        return self.scope in ("guest", "mixed")


@dataclass
class CertificationDefinition:
    """Top-level certification suite loaded from a TOML manifest."""

    version: str
    description: str
    tests: list[TestDefinition] = field(default_factory=list)
    # Ordered unique levels from the original manifest (preserved across filtering for chain validation)
    all_levels: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("CertificationDefinition.version must not be empty")
        # Auto-populate all_levels from tests if not explicitly provided.
        if not self.all_levels and self.tests:
            seen: set[str] = set()
            for t in self.tests:
                if t.level and t.level not in seen:
                    seen.add(t.level)
                    self.all_levels.append(t.level)


# ── Runtime result models (populated during execution) ──────────


@dataclass
class StepResult:
    """Result of executing a single Step."""

    step: Step
    result: Literal["pass", "fail", "error", "skip"]
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    duration_ms: int | None = None


@dataclass
class StepHandlerResult:
    """Return value from a ``kind="callable"`` step handler (like a lightweight process outcome)."""

    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class StepContext:
    """State passed to callable steps; ``step_results`` contains only prior steps.

    ``artifact_dir`` is the per-test directory under ``--artifacts-dir`` (see
    :func:`sev_verify.runner.test_artifact_dir`). Host steps also receive
    ``$SEV_VERIFY_ARTIFACT_DIR``.
    """

    test: TestDefinition
    guest_path: Path
    step_results: list[StepResult]
    module: ModuleType
    artifact_dir: Path
    # Set by the harness when a VM is in use (types: VMProfile, VMLaunchResult — see vm_profile).
    profile: object | None = None
    launch: object | None = None
    # Global CLI overrides (same as ``python3 -m sev_verify --qemu-binary`` / ``--ovmf``).
    cli_qemu_binary: str | None = None
    cli_ovmf_path: str | None = None


@dataclass
class TestResult:
    """Result of executing a TestDefinition."""

    test: TestDefinition
    result: Literal["pass", "fail", "error"]
    step_results: list[StepResult] = field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class CertificationResult:
    """Result of executing a CertificationDefinition."""

    certification: CertificationDefinition
    result: Literal["pass", "fail", "error"]
    test_results: list[TestResult] = field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None
