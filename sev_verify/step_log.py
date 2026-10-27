"""Step execution logging for debugging."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from .models import BaseStep, StepResult


class StepLogger:
    """Writes step execution details to the artifact directory.

    Maintains both a main steps.log and per-guest logs under <guest_id>/steps.log
    for easier debugging when multiple guests are launched. The guest_id defaults
    to a generated UUID if not explicitly set on the vm_launch step.

    Per-guest directories also contain:
    - qemu-command.log: The full QEMU command line used to launch the guest
    - qemu-boot.log: Guest serial console output (dmesg logs)
    - qemu-error.log: QEMU stderr output for debugging launch failures
    - guest-journal.log: Guest journald logs (pulled via vsock before vm_stop)
    """

    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = artifact_dir
        self.log_path = artifact_dir / "steps.log"
        self._current_guest_id: str | None = None
        self._current_guest_dir: Path | None = None
        self._guest_log_path: Path | None = None

    def _ts(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def _set_guest_context(self, guest_id: str | None) -> None:
        """Update the current guest context and create guest directory if needed."""
        if guest_id and guest_id != self._current_guest_id:
            self._current_guest_id = guest_id
            guest_dir = self.artifact_dir / guest_id
            guest_dir.mkdir(parents=True, exist_ok=True)
            self._current_guest_dir = guest_dir
            self._guest_log_path = guest_dir / "steps.log"
        elif guest_id is None:
            self._current_guest_id = None
            self._current_guest_dir = None
            self._guest_log_path = None

    def _write_qemu_command_log(self, command: str) -> None:
        """Write the QEMU command to qemu-command.log in the guest directory."""
        if self._current_guest_dir is None:
            return
        cmd_log = self._current_guest_dir / "qemu-command.log"
        with open(cmd_log, "w") as f:
            f.write(f"# QEMU command for guest {self._current_guest_id}\n")
            f.write(f"# Timestamp: {self._ts()}\n\n")
            f.write(command)
            f.write("\n")


    def _write_guest_journal_log(self, journal_output: str | None) -> None:
        """Write guest journald logs to guest-journal.log in the guest directory."""
        if self._current_guest_dir is None:
            return
        journal_log = self._current_guest_dir / "guest-journal.log"
        with open(journal_log, "w") as f:
            f.write(f"# Guest journal log for guest {self._current_guest_id}\n")
            f.write(f"# Timestamp: {self._ts()}\n\n")
            if journal_output:
                f.write(journal_output)
                if not journal_output.endswith("\n"):
                    f.write("\n")
            else:
                f.write("(no journal output captured)\n")

    def _write_step_entry(
        self,
        f: TextIO,
        step: "BaseStep",
        result: "StepResult",
        *,
        command: str | None = None,
        guest_id: str | None = None,
        process_id: int | None = None,
    ) -> None:
        """Write a single step entry to a file handle."""
        f.write(f"Step: {step.name}\n")
        f.write(f"Kind: {step.kind}\n")
        f.write(f"Type: {step.type}\n")
        f.write(f"Timestamp: {self._ts()}\n")
        if step.kind == "host":
            f.write(f"Command: {step.command}\n")
        elif step.kind == "guest":
            if guest_id:
                f.write(f"Guest ID: {guest_id}\n")
            f.write(f"Command: {step.command}\n")
        elif step.kind == "callable":
            f.write(f"Handler: {step.handler}\n")
            if step.args:
                f.write(f"Args: {step.args!r}\n")
            if result.command:
                f.write(f"Command: {result.command}\n")
        elif step.kind == "guest_pull":
            if guest_id:
                f.write(f"Guest ID: {guest_id}\n")
            f.write(f"Pull: {step.guest_src} (guest) -> {step.host_dest} (host)\n")
            if result.command:
                f.write(f"Command: {result.command}\n")
        elif step.kind == "vm_launch":
            if guest_id:
                f.write(f"Guest ID: {guest_id}\n")
            if command:
                f.write(f"Command: {command}\n")
        elif step.kind == "vm_stop":
            if guest_id:
                f.write(f"Guest ID: {guest_id}\n")
            if process_id is not None:
                f.write(f"Process ID: {process_id}\n")
            if command:
                f.write(f"Process Command: {command}\n")
        f.write(f"Duration: {result.duration_ms}ms\n")
        f.write(f"Status: {result.result.upper()}")
        if result.exit_code is not None:
            f.write(f" (exit={result.exit_code})")
        f.write("\n")
        if result.stdout:
            f.write(f"[stdout]\n{result.stdout}")
            if not result.stdout.endswith("\n"):
                f.write("\n")
        if result.stderr:
            f.write(f"[stderr]\n{result.stderr}")
            if not result.stderr.endswith("\n"):
                f.write("\n")

        # Create a separator
        separator_len = 150
        f.write("-" * separator_len + "\n")

    def log_step(
        self,
        step: "BaseStep",
        result: "StepResult",
        *,
        command: str | None = None,
        guest_id: str | None = None,
        guest_journal: str | None = None,
        process_id: int | None = None,
    ) -> None:
        """Log a step to the main log and optionally to a guest-specific log.

        When a guest_id is provided (typically for vm_launch, guest, guest_pull,
        and vm_stop steps), the step is also logged to <guest_id>/steps.log.

        For vm_launch steps, creates qemu-command.log with the full QEMU command line.
        QEMU writes qemu-boot.log and qemu-error.log directly to the guest directory.

        For vm_stop steps, writes guest-journal.log with journald logs pulled via vsock.
        """
        # Update guest context on vm_launch or when guest_id changes
        if step.kind == "vm_launch" and guest_id:
            self._set_guest_context(guest_id)
            # Write QEMU command log at launch time
            if command:
                self._write_qemu_command_log(command)
        elif step.kind in ("guest", "guest_pull") and guest_id and self._current_guest_id != guest_id:
            # Update guest context if it changed (e.g., switching between multiple guests)
            self._set_guest_context(guest_id)
        elif step.kind == "vm_stop":
            # Write guest journald logs
            if guest_journal:
                self._write_guest_journal_log(guest_journal)

        # Always write to the main log
        with open(self.log_path, "a") as f:
            self._write_step_entry(f, step, result, command=command, guest_id=guest_id, process_id=process_id)

        # Write to guest-specific log if we have a guest context
        if self._guest_log_path is not None:
            with open(self._guest_log_path, "a") as f:
                self._write_step_entry(f, step, result, command=command, guest_id=guest_id, process_id=process_id)

        # Clear guest context after vm_stop
        if step.kind == "vm_stop":
            self._set_guest_context(None)
