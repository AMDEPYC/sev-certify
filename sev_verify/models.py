"""Data model dataclasses for sev_verify."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType
from pathlib import Path
from typing import Literal, get_args


# Certification / failure-handling semantics (unchanged field name: ``type``)
StepSeverity = Literal["setup", "required", "info"]
StepType = StepSeverity

StepKind = Literal["vm_launch", "vm_stop", "host", "guest", "guest_pull", "callable"]

Scope = Literal["host", "guest", "mixed"]


def _validate_expected_result_format(name: str, expected_result: str) -> None:
    result_kind, sep, value = expected_result.partition(":")
    if result_kind not in ("exit_code", "stdout_contains") or not sep:
        raise ValueError(
            f"Step {name!r}: invalid expected_result {expected_result!r}; "
            f"expected 'exit_code:<int>' or 'stdout_contains:<string>'"
        )
    if result_kind == "exit_code":
        try:
            int(value)
        except ValueError as exc:
            raise ValueError(
                f"Step {name!r}: exit_code value must be an integer, got {value!r}"
            ) from exc


@dataclass(kw_only=True)
class BaseStep:
    """One executable step returned from :func:`steps` (built via :class:`Step`).

    ``kind`` selects which of ``command`` / ``handler`` / ``guest_src``+``host_dest``
    are meaningful; :meth:`__post_init__` enforces that only the right fields are set.
    """

    name: str
    type: StepSeverity
    kind: StepKind
    expected_result: str = "exit_code:0"
    timeout: int = 10
    command: str = ""
    handler: str = ""
    guest_src: str = ""
    host_dest: str = ""

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
            if self.handler:
                raise ValueError(
                    f"Step {self.name!r}: {self.kind} steps must not set handler"
                )
        elif self.kind == "host":
            if not self.command:
                raise ValueError(f"Step {self.name!r}: host steps require a non-empty command")
            if self.guest_src or self.host_dest or self.handler:
                raise ValueError(
                    f"Step {self.name!r}: host steps must only set command (not handler/paths)"
                )
        elif self.kind == "guest":
            if not self.command:
                raise ValueError(f"Step {self.name!r}: guest steps require a non-empty command")
            if self.guest_src or self.host_dest or self.handler:
                raise ValueError(
                    f"Step {self.name!r}: guest steps must only set command (not handler/paths)"
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
            if self.command or self.handler:
                raise ValueError(
                    f"Step {self.name!r}: guest_pull steps must not set command or handler"
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

        _validate_expected_result_format(self.name, self.expected_result)


@dataclass(kw_only=True)
class Step:
    """Fluent factory for :class:`BaseStep`.

    Chained style::

        Step(name="probe", type="required").host(command="snphost ok")

    One-call style (clear signatures in the IDE)::

        Step.for_host(name="probe", type="required", command="snphost ok")
        Step.for_callable(name="check", type="required", handler="my_fn")

    ``for_*`` names avoid clashing with instance methods ``.host()``, ``.guest()``, …
    """

    name: str
    type: StepSeverity
    expected_result: str = "exit_code:0"
    timeout: int = 10

    def _common(self) -> dict[str, str | int]:
        return {
            "name": self.name,
            "type": self.type,
            "expected_result": self.expected_result,
            "timeout": self.timeout,
        }

    def host(self, command: str) -> BaseStep:
        return BaseStep(kind="host", command=command, **self._common())

    def guest(self, command: str) -> BaseStep:
        return BaseStep(kind="guest", command=command, **self._common())

    def vm_launch(self) -> BaseStep:
        return BaseStep(kind="vm_launch", **self._common())

    def vm_stop(self) -> BaseStep:
        return BaseStep(kind="vm_stop", **self._common())

    def guest_pull(self, guest_src: str, host_dest: str) -> BaseStep:
        return BaseStep(
            kind="guest_pull",
            guest_src=guest_src,
            host_dest=host_dest,
            **self._common(),
        )

    def call(self, handler: str) -> BaseStep:
        return BaseStep(kind="callable", handler=handler, **self._common())

    @classmethod
    def for_host(
        cls,
        name: str,
        type: StepSeverity,
        command: str,
        *,
        expected_result: str = "exit_code:0",
        timeout: int = 10,
    ) -> BaseStep:
        return cls(
            name=name, type=type, expected_result=expected_result, timeout=timeout
        ).host(command)

    @classmethod
    def for_guest(
        cls,
        name: str,
        type: StepSeverity,
        command: str,
        *,
        expected_result: str = "exit_code:0",
        timeout: int = 10,
    ) -> BaseStep:
        return cls(
            name=name, type=type, expected_result=expected_result, timeout=timeout
        ).guest(command)

    @classmethod
    def for_vm_launch(
        cls,
        name: str,
        type: StepSeverity,
        *,
        expected_result: str = "exit_code:0",
        timeout: int = 10,
    ) -> BaseStep:
        return cls(
            name=name, type=type, expected_result=expected_result, timeout=timeout
        ).vm_launch()

    @classmethod
    def for_vm_stop(
        cls,
        name: str,
        type: StepSeverity,
        *,
        expected_result: str = "exit_code:0",
        timeout: int = 10,
    ) -> BaseStep:
        return cls(
            name=name, type=type, expected_result=expected_result, timeout=timeout
        ).vm_stop()

    @classmethod
    def for_guest_pull(
        cls,
        name: str,
        type: StepSeverity,
        guest_src: str,
        host_dest: str,
        *,
        expected_result: str = "exit_code:0",
        timeout: int = 10,
    ) -> BaseStep:
        return cls(
            name=name, type=type, expected_result=expected_result, timeout=timeout
        ).guest_pull(guest_src, host_dest)

    @classmethod
    def for_callable(
        cls,
        name: str,
        type: StepSeverity,
        handler: str,
        *,
        expected_result: str = "exit_code:0",
        timeout: int = 10,
    ) -> BaseStep:
        return cls(
            name=name, type=type, expected_result=expected_result, timeout=timeout
        ).call(handler)


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

    step: BaseStep
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
