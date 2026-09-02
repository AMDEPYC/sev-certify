"""Step execution logging for debugging."""

from __future__ import annotations

import shutil
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

    def _write_qemu_boot_log(self, stdout: str | None) -> None:
        """Write boot messages to qemu-boot.log in the guest directory."""
        if self._current_guest_dir is None:
            return
        boot_log = self._current_guest_dir / "qemu-boot.log"
        with open(boot_log, "w") as f:
            f.write(f"# Boot log for guest {self._current_guest_id}\n")
            f.write(f"# Timestamp: {self._ts()}\n\n")
            if stdout:
                f.write(stdout)
                if not stdout.endswith("\n"):
                    f.write("\n")
            else:
                f.write("(no boot output captured)\n")

    def _write_qemu_error_log(self, stderr: str | None) -> None:
        """Write QEMU errors to qemu-error.log in the guest directory."""
        if self._current_guest_dir is None:
            return
        error_log = self._current_guest_dir / "qemu-error.log"
        with open(error_log, "w") as f:
            f.write(f"# Error log for guest {self._current_guest_id}\n")
            f.write(f"# Timestamp: {self._ts()}\n\n")
            if stderr:
                f.write(stderr)
                if not stderr.endswith("\n"):
                    f.write("\n")
            else:
                f.write("(no errors)\n")

    def _copy_guest_error_log(self, error_log_path: str) -> None:
        """Copy the QEMU guest error log to the guest directory."""
        if self._current_guest_dir is None:
            return
        src = Path(error_log_path)
        if src.is_file():
            dest = self._current_guest_dir / "qemu-error.log"
            shutil.copy2(src, dest)

    def _copy_guest_boot_log(self, boot_log_path: str) -> None:
        """Copy the QEMU guest boot log (serial console output) to the guest directory."""
        if self._current_guest_dir is None:
            return
        src = Path(boot_log_path)
        if src.is_file():
            dest = self._current_guest_dir / "qemu-boot.log"
            shutil.copy2(src, dest)

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
            if result.command:
                f.write(f"Command: {result.command}\n")
        elif step.kind == "guest_pull":
            if guest_id:
                f.write(f"Guest ID: {guest_id}\n")
            f.write(f"Pull: {step.guest_src} -> {step.host_dest}\n")
            if result.command:
                f.write(f"Command to read the guest file {step.guest_src}: {result.command}\n")
        elif step.kind == "vm_launch":
            if guest_id:
                f.write(f"Guest ID: {guest_id}\n")
            if command:
                f.write(f"Command: {command}\n")
        elif step.kind == "vm_stop":
            if guest_id:
                f.write(f"Guest ID: {guest_id}\n")
            if result.command:
                f.write(f"Command: {result.command}\n")
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
        f.write("-" * 60 + "\n")

    def log_step(
        self,
        step: "BaseStep",
        result: "StepResult",
        *,
        command: str | None = None,
        guest_id: str | None = None,
        guest_error_log_path: str | None = None,
        guest_boot_log_path: str | None = None,
        guest_journal: str | None = None,
    ) -> None:
        """Log a step to the main log and optionally to a guest-specific log.

        When a guest_id is provided (typically for vm_launch, guest, guest_pull,
        and vm_stop steps), the step is also logged to <guest_id>/steps.log.

        For vm_launch steps, creates:
        - qemu-command.log: The full QEMU command line
        - qemu-boot.log: Guest serial console output (initial dmesg logs)
        - qemu-error.log: QEMU stderr

        For vm_stop steps, updates:
        - qemu-boot.log: Complete serial console output (full dmesg)
        - qemu-error.log: QEMU stderr
        - guest-journal.log: Guest journald logs (pulled via vsock)
        """
        # Update guest context on vm_launch or when guest_id changes
        if step.kind == "vm_launch" and guest_id:
            self._set_guest_context(guest_id)
            # Write QEMU command log at launch time
            if command:
                self._write_qemu_command_log(command)
            # Copy initial boot log at launch (captures logs if guest crashes mid-boot)
            if guest_boot_log_path:
                self._copy_guest_boot_log(guest_boot_log_path)
            else:
                self._write_qemu_boot_log(result.stdout)
            if guest_error_log_path:
                self._copy_guest_error_log(guest_error_log_path)
            else:
                self._write_qemu_error_log(result.stderr)
        elif step.kind in ("guest", "guest_pull") and guest_id and self._current_guest_id != guest_id:
            # Update guest context if it changed (e.g., switching between multiple guests)
            self._set_guest_context(guest_id)
        elif step.kind == "vm_stop":
            # Copy complete boot and error logs at vm_stop (overwrites with full dmesg)
            if guest_boot_log_path:
                self._copy_guest_boot_log(guest_boot_log_path)
            else:
                self._write_qemu_boot_log(result.stdout)
            if guest_error_log_path:
                self._copy_guest_error_log(guest_error_log_path)
            else:
                self._write_qemu_error_log(result.stderr)
            # Write guest journald logs
            if guest_journal:
                self._write_guest_journal_log(guest_journal)

        # Always write to the main log
        with open(self.log_path, "a") as f:
            self._write_step_entry(f, step, result, command=command, guest_id=guest_id)

        # Write to guest-specific log if we have a guest context
        if self._guest_log_path is not None:
            with open(self._guest_log_path, "a") as f:
                self._write_step_entry(f, step, result, command=command, guest_id=guest_id)

        # Clear guest context after vm_stop
        if step.kind == "vm_stop":
            self._set_guest_context(None)
