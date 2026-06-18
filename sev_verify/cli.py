"""CLI arg parsing and entry point for sev_verify."""

from __future__ import annotations

import argparse
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    CertificationDefinition,
    CertificationResult,
    StepContext,
    StepResult,
    TestDefinition,
    TestResult,
)
from .output import write_json, write_markdown
from .runner import (
    effective_vm_profile,
    import_test_module,
    load_test_execution_plan,
    run_callable_step,
    run_guest_pull_step,
    run_guest_step,
    run_step,
    run_vm_launch_step,
    run_vm_stop_step,
    test_artifact_dir,
)
from .vm_profile import VMLaunchResult, stop_vm

_LINE_WIDTH = 80


def _load_test_entries(toml_path: Path, data: dict) -> list[TestDefinition]:
    """Parse tests array from TOML data and return TestDefinition instances."""
    raw_tests = data.get("tests", [])
    if not isinstance(raw_tests, list):
        raise ValueError(f"{toml_path}: 'tests' must be an array of tables")

    tests: list[TestDefinition] = []
    for i, entry in enumerate(raw_tests):
        if not isinstance(entry, dict):
            raise ValueError(f"{toml_path}: tests[{i}] must be a table")
        try:
            tests.append(TestDefinition(**entry))
        except TypeError as exc:
            raise ValueError(f"{toml_path}: tests[{i}] has invalid fields: {exc}") from exc
        except ValueError as exc:
            raise ValueError(f"{toml_path}: tests[{i}]: {exc}") from exc
    return tests


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    '''
    Parse optional flags
    '''
    parser = argparse.ArgumentParser(
        prog="sev_verify",
        description="SEV-SNP certification testing harness",
    )
    parser.add_argument(
        "path_to_guest",
        help="Path to the guest image/UKI",
    )
    parser.add_argument(
        "--version",
        "-v",
        dest="versions",
        action="append",
        default=[],
        help="Version filter(s). Accepts: 3.0 (all tests in cert 3.0), "
        "3.0.0 (all level 3.0.0-* tests), 3.0.0-0 (exact level). "
        "Comma-separated lists and repeated -v flags both work. "
        "If omitted, all cert_tests/*/manifest.toml are used.",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("results"),
        help="Directory for JSON and Markdown result files (default: results/)",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        metavar="DIR",
        help="Base directory for per-test artifact folders (default: ./artifacts)",
    )
    parser.add_argument(
        "--qemu-binary",
        "--qemu",
        dest="qemu_binary",
        type=Path,
        default=None,
        metavar="PATH",
        help="Override QEMU executable (overrides test VMProfile and default qemu-system-x86_64)",
    )
    parser.add_argument(
        "--ovmf",
        dest="ovmf",
        type=Path,
        default=None,
        metavar="PATH",
        help="Override OVMF firmware .fd (overrides test VMProfile and host search paths)",
    )
    return parser.parse_args(argv)


def _parse_version_filters(raw: list[str]) -> list[str]:
    """Expand comma-separated -v values into a flat list of filters."""
    filters: list[str] = []
    for entry in raw:
        for part in entry.split(","):
            part = part.strip()
            if part:
                filters.append(part)
    return filters


def _manifest_version(version_filter: str) -> str:
    """Extract the manifest-level version (first two components) from a filter.

    '3.0'     -> '3.0'
    '3.0.0'   -> '3.0'
    '3.0.0-0' -> '3.0'
    """
    parts = version_filter.split(".")
    return ".".join(parts[:2])


def _matches_level(test_level: str, version_filter: str) -> bool:
    """Check whether a test level matches a version filter.

    Filter '3.0.0'   -> matches '3.0.0-0', '3.0.0-1', etc.
    Filter '3.0.0-0' -> matches only '3.0.0-0' (exact)

    Note: bare manifest versions like '3.0' are handled by the caller
    and do not reach this function.
    """
    if not test_level:
        return False
    # Exact match (fully-qualified level like '3.0.0-0')
    if version_filter == test_level:
        return True
    # '3.0.0' matches '3.0.0-*'
    if "-" not in version_filter and test_level.startswith(version_filter + "-"):
        return True
    # '3.0' matches everything in the manifest (handled by caller)
    return False


def _filter_tests(
    cert: CertificationDefinition, level_filters: list[str],
) -> CertificationDefinition:
    """Return a copy of cert with tests filtered to matching levels.

    Preserves all_levels from the original manifest so the summary can
    detect skipped prerequisite levels.
    """
    if not level_filters:
        return cert

    filtered = [
        t for t in cert.tests
        if any(_matches_level(t.level, f) for f in level_filters)
    ]

    return CertificationDefinition(
        version=cert.version,
        description=cert.description,
        tests=filtered,
        all_levels=list(cert.all_levels),  # defensive copy
    )


def load_manifest(toml_path: Path) -> CertificationDefinition:
    """Load and validate a TOML certification manifest."""
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except OSError as exc:
        raise ValueError(f"Cannot read manifest {toml_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Malformed TOML in {toml_path}: {exc}") from exc

    for key in ("version", "description"):
        if key not in data:
            raise ValueError(f"{toml_path}: missing required key {key!r}")

    tests = _load_test_entries(toml_path, data)

    return CertificationDefinition(
        version=str(data["version"]),
        description=str(data["description"]),
        tests=tests,
    )


def load_prereqs(cert_dir: Path) -> list[TestDefinition]:
    """Load prerequisite tests from cert_tests/common/prereqs.toml."""
    prereqs_path = cert_dir / "common" / "prereqs.toml"
    if not prereqs_path.exists():
        return []

    try:
        with open(prereqs_path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Cannot load prereqs {prereqs_path}: {exc}") from exc

    return _load_test_entries(prereqs_path, data)


def discover_manifests(
    cert_dir: Path, version_filters: list[str],
) -> list[tuple[Path, list[str]]]:
    """Find manifest.toml files and associated level filters.

    Returns a list of (manifest_path, level_filters) tuples.
    level_filters is empty when the entire manifest should run.
    """
    if not cert_dir.is_dir():
        return []

    if not version_filters:
        return [(p, []) for p in sorted(cert_dir.glob("*/manifest.toml"))]

    # Group filters by manifest version (first two components).
    # A bare version like '3.0' means "run all tests" (no level filter).
    # A specific level like '3.0.0-0' filters within the manifest.
    run_all: set[str] = set()
    per_manifest: dict[str, list[str]] = {}
    for vf in version_filters:
        mv = _manifest_version(vf)
        if vf == mv:
            run_all.add(mv)
        else:
            per_manifest.setdefault(mv, []).append(vf)

    results: list[tuple[Path, list[str]]] = []

    for mv in dict.fromkeys(_manifest_version(vf) for vf in version_filters):
        subfolder = "c" + mv.replace(".", "_")
        mpath = cert_dir / subfolder / "manifest.toml"
        if not mpath.exists():
            print(f"Error: no manifest for version {mv!r} "
                  f"(expected {mpath})", file=sys.stderr)
            continue

        if mv in run_all:
            results.append((mpath, []))
        else:
            results.append((mpath, per_manifest.get(mv, [])))

    return results


# ── Output helpers ───────────────────────────────────────────────

_RESULT_LABEL = {"pass": "PASS", "fail": "FAIL", "error": "ERR!", "skip": "SKIP"}
_IS_TTY = sys.stdout.isatty()


def _flush(msg: str, end: str = "\n") -> None:
    """Print and immediately flush so output appears in real time."""
    print(msg, end=end, flush=True)


def _section(label: str) -> None:
    """Print a section header with consistent width."""
    prefix = f"── {label} "
    _flush(f"{prefix}{'─' * (_LINE_WIDTH - len(prefix))}")


def _fmt_duration(ms: int | None) -> str:
    """Format a duration for display."""
    if ms is None:
        return ""
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def _step_result_line(sr: StepResult, is_last: bool) -> str:
    connector = "└─" if is_last else "├─"
    icon = _RESULT_LABEL.get(sr.result, "????")
    duration = _fmt_duration(sr.duration_ms)
    suffix = f" {duration} [{icon}]" if duration else f" [{icon}]"
    name_part = f"   {connector} {sr.step.name} "
    avail = _LINE_WIDTH - len(name_part) - len(suffix)
    dots = "·" * max(avail, 2)
    return f"{name_part}{dots}{suffix}"


# ── Live execution + output ──────────────────────────────────────


def execute_test(
    test: TestDefinition,
    guest_path: Path,
    *,
    artifacts_root: Path,
    certification_version: str | None = None,
    qemu_binary: str | None = None,
    ovmf_path: str | None = None,
) -> TestResult:
    """Run a test, printing each step live as it executes."""
    started_at = datetime.now(timezone.utc).isoformat()
    step_results: list[StepResult] = []
    launch: VMLaunchResult | None = None

    if test.description:
        name_part = f"   {test.name} "
        desc_part = f" {test.description}"
        avail = _LINE_WIDTH - len(name_part) - len(desc_part)
        _flush(f"{name_part}{' ' * max(avail, 2)}{desc_part}")
    else:
        _flush(f"   {test.name}")

    try:
        try:
            steps, declared_profile = load_test_execution_plan(test)
        except Exception as exc:
            _flush(f"   [ERR!] — failed to load module: {exc}")
            return TestResult(
                test=test, result="error", step_results=[],
                started_at=started_at, completed_at=datetime.now(timezone.utc).isoformat(),
            )

        artifact_dir = test_artifact_dir(artifacts_root, certification_version, test)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        _flush(f"   Artifacts: {artifact_dir}")

        profile = None
        if test.requires_vm:
            profile = effective_vm_profile(
                declared_profile,
                guest_path,
                qemu_binary=qemu_binary,
                ovmf_path=ovmf_path,
            )

        mod = import_test_module(test)
        ctx = StepContext(
            test=test,
            guest_path=guest_path,
            step_results=step_results,
            module=mod,
            artifact_dir=artifact_dir,
            profile=profile,
            launch=None,
            cli_qemu_binary=qemu_binary,
            cli_ovmf_path=ovmf_path,
        )

        overall = "pass"
        total_steps = len(steps)

        for i, step in enumerate(steps):
            is_last = i == total_steps - 1

            ctx.profile = profile
            ctx.launch = launch

            if _IS_TTY:
                connector = "└─" if is_last else "├─"
                _flush(f"   {connector} {step.name} ...", end="\r")

            if step.kind == "vm_launch":
                if profile is None:
                    sr = StepResult(
                        step=step,
                        result="error",
                        stderr="vm_launch step requires guest image / VM profile context",
                    )
                elif launch is not None:
                    sr = StepResult(
                        step=step,
                        result="error",
                        stderr="Duplicate vm_launch step (guest is already running)",
                        duration_ms=0,
                    )
                else:
                    sr, new_launch = run_vm_launch_step(step, profile)
                    launch = new_launch
            elif step.kind == "vm_stop":
                if launch is None:
                    sr = StepResult(
                        step=step,
                        result="error",
                        stderr="vm_stop: no running guest (run vm_launch or a guest step first)",
                        duration_ms=0,
                    )
                else:
                    sr = run_vm_stop_step(step, launch)
                    if sr.result != "error":
                        launch = None
            elif step.kind == "host":
                sr = run_step(step, guest_path, artifact_dir)
            elif step.kind in ("guest", "guest_pull"):
                if profile is None:
                    sr = StepResult(
                        step=step,
                        result="error",
                        stderr="Guest steps require a guest image path and VM profile",
                    )
                else:
                    if launch is None:
                        launch = profile.vm_launch()
                    if not launch.ok:
                        sr = StepResult(
                            step=step,
                            result="error",
                            stderr=launch.message,
                            duration_ms=0,
                        )
                    elif step.kind == "guest":
                        sr = run_guest_step(step, launch.profile)
                    else:
                        sr = run_guest_pull_step(step, launch.profile, artifact_dir)
            elif step.kind == "callable":
                sr = run_callable_step(step, ctx)
            else:
                sr = StepResult(
                    step=step,
                    result="error",
                    stderr=f"Unsupported step kind {step.kind!r}",
                )
            step_results.append(sr)

            _flush(_step_result_line(sr, is_last))

            if sr.result in ("fail", "error"):
                if sr.stderr:
                    gutter = "   " if is_last else "│  "
                    for line in sr.stderr.strip().splitlines()[:5]:
                        _flush(f"   {gutter}   {line}")
                combined = (sr.stderr or "") + (sr.stdout or "")
                for pattern, hint_msg in step.hints:
                    if pattern in combined:
                        gutter = "   " if is_last else "│  "
                        _flush(f"   {gutter}   [Hint] {hint_msg}")
                        break
                if step.type == "setup":
                    overall = "fail"
                    for j, remaining in enumerate(steps[i + 1:], i + 1):
                        skip = StepResult(step=remaining, result="skip")
                        step_results.append(skip)
                        _flush(_step_result_line(skip, j == total_steps - 1))
                    break
                elif step.type == "required":
                    overall = "fail"

        passed = sum(1 for s in step_results if s.result == "pass")
        failed = sum(1 for s in step_results if s.result in ("fail", "error"))
        icon = _RESULT_LABEL.get(overall, "????")
        counts = f"{passed}/{total_steps} passed" if not failed else f"{failed}/{total_steps} failed"
        suffix = f" {counts} [{icon}]"
        name_part = f"   result:"
        avail = _LINE_WIDTH - len(name_part) - len(suffix)
        _flush(f"{name_part}{' ' * max(avail, 2)}{suffix}")

        return TestResult(
            test=test, result=overall, step_results=step_results,
            started_at=started_at, completed_at=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        if launch is not None:
            stop_vm(launch)


def execute_certification(
    cert: CertificationDefinition,
    guest_path: Path,
    *,
    artifacts_root: Path,
    qemu_binary: str | None = None,
    ovmf_path: str | None = None,
) -> CertificationResult:
    """Run all tests in a certification with live output."""
    started_at = datetime.now(timezone.utc).isoformat()
    test_results: list[TestResult] = []
    overall = "pass"

    _section(f"Certification {cert.version}")
    _flush(f"   {cert.description}")
    _flush("")

    current_level = None
    for test in cert.tests:
        if test.level != current_level:
            if test.level:
                _flush(f"   ── {test.level} ──")
            current_level = test.level

        tr = execute_test(
            test,
            guest_path,
            artifacts_root=artifacts_root,
            certification_version=cert.version,
            qemu_binary=qemu_binary,
            ovmf_path=ovmf_path,
        )
        test_results.append(tr)
        if tr.result != "pass":
            overall = tr.result
        _flush("")

    icon = _RESULT_LABEL.get(overall, "????")
    passed = sum(1 for t in test_results if t.result == "pass")
    label = f"   Certification {cert.version}:"
    suffix = f" {passed}/{len(test_results)} passed [{icon}]"
    _flush(f"{label}{' ' * max(_LINE_WIDTH - len(label) - len(suffix), 2)}{suffix}")
    _flush("")

    return CertificationResult(
        certification=cert, result=overall, test_results=test_results,
        started_at=started_at, completed_at=datetime.now(timezone.utc).isoformat(),
    )


def _highest_certified_level(cr: CertificationResult) -> str | None:
    """Determine the highest certification level achieved.

    Walks the manifest's full ordered level list. A level counts as
    achieved only if every prior level was also run and passed.
    Returns None if no contiguous chain of passing levels exists
    (e.g. level 0 was skipped or failed).
    """
    # Build a map: level -> list of results for tests at that level
    results_by_level: dict[str, list[str]] = {}
    for tr in cr.test_results:
        if tr.test.level:
            results_by_level.setdefault(tr.test.level, []).append(tr.result)

    highest = None
    for level in cr.certification.all_levels:
        level_results = results_by_level.get(level)
        if not level_results:
            # Level was not run (filtered out) — chain broken
            break
        if any(r != "pass" for r in level_results):
            # Level had a failure — chain broken
            break
        highest = level
    return highest


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    guest_path = Path(args.path_to_guest)

    if not guest_path.exists():
        print(f"Error: guest path does not exist: {guest_path}", file=sys.stderr)
        return 1

    qemu_override: str | None = None
    if args.qemu_binary is not None:
        if not args.qemu_binary.is_file():
            print(f"Error: QEMU binary not found: {args.qemu_binary}", file=sys.stderr)
            return 1
        qemu_override = str(args.qemu_binary.resolve())

    ovmf_override: str | None = None
    if args.ovmf is not None:
        if not args.ovmf.is_file():
            print(f"Error: OVMF image not found: {args.ovmf}", file=sys.stderr)
            return 1
        ovmf_override = str(args.ovmf.resolve())

    cert_dir = Path(__file__).resolve().parent / "cert_tests"

    version_filters = _parse_version_filters(args.versions)
    manifest_entries = discover_manifests(cert_dir, version_filters)

    if not manifest_entries:
        print(
            "Error: no manifest.toml found in cert_tests/*/",
            file=sys.stderr,
        )
        return 1

    _flush(f"   Guest: {guest_path}")
    if qemu_override:
        _flush(f"   QEMU:  {qemu_override}")
    if ovmf_override:
        _flush(f"   OVMF:  {ovmf_override}")
    _flush("")

    total_tests = 0
    total_passed = 0

    # ── Run prerequisites (once, gates all certifications) ───────
    prereqs = load_prereqs(cert_dir)
    if prereqs:
        _section("Prerequisites")
        _flush("")
        prereq_results = []
        for test in prereqs:
            tr = execute_test(
                test,
                guest_path,
                artifacts_root=args.artifacts_dir,
                certification_version=None,
                qemu_binary=qemu_override,
                ovmf_path=ovmf_override,
            )
            prereq_results.append(tr)
            _flush("")

        total_tests += len(prereq_results)
        total_passed += sum(1 for r in prereq_results if r.result == "pass")

        if any(r.result != "pass" for r in prereq_results):
            label = "   Prerequisites:"
            suffix = f" {total_passed}/{total_tests} passed [FAIL]"
            _flush(f"{label}{' ' * max(_LINE_WIDTH - len(label) - len(suffix), 2)}{suffix}")
            _flush("   Skipping all certifications.")
            return 1
        label = "   Prerequisites:"
        suffix = f" {total_passed}/{total_tests} passed [PASS]"
        _flush(f"{label}{' ' * max(_LINE_WIDTH - len(label) - len(suffix), 2)}{suffix}")
        _flush("")

    # ── Run certifications ───────────────────────────────────────
    cert_results: list[CertificationResult] = []
    for manifest_path, level_filters in manifest_entries:
        cert = load_manifest(manifest_path)
        cert = _filter_tests(cert, level_filters)
        if not cert.tests:
            levels = ", ".join(level_filters)
            print(f"Warning: no tests match level filter(s) {levels!r} "
                  f"in certification {cert.version}", file=sys.stderr)
            continue
        cr = execute_certification(
            cert,
            guest_path,
            artifacts_root=args.artifacts_dir,
            qemu_binary=qemu_override,
            ovmf_path=ovmf_override,
        )
        cert_results.append(cr)
        total_tests += len(cr.test_results)
        total_passed += sum(1 for tr in cr.test_results if tr.result == "pass")

        certified_level = _highest_certified_level(cr)
        json_path = write_json(cr, certified_level, args.output_dir)
        md_path = write_markdown(cr, certified_level, args.output_dir)
        _flush(f"   Wrote {json_path}")
        _flush(f"   Wrote {md_path}")
        _flush("")

    # ── Summary ──────────────────────────────────────────────────
    _section("Summary")
    for cr in cert_results:
        highest_passing = _highest_certified_level(cr)
        badge = highest_passing or "---"
        label = f"   Certification {cr.certification.version}:"
        suffix = f" [{badge}]"
        _flush(f"{label}{' ' * max(_LINE_WIDTH - len(label) - len(suffix), 2)}{suffix}")
    _flush("")

    return 1 if any(cr.result != "pass" for cr in cert_results) else 0
