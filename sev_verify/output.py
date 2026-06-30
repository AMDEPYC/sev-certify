"""JSON and Markdown output writers for certification results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import CertificationResult, StepResult, TestResult


_RESULT_ICON = {
    "pass": ":white_check_mark:",
    "fail": ":x:",
    "error": ":boom:",
    "skip": ":fast_forward:",
}


def _group_tests_by_level(
    test_results: list[TestResult],
) -> tuple[dict[str, list[TestResult]], list[TestResult]]:
    """Group test results by level, separating unlabeled tests."""
    by_level: dict[str, list[TestResult]] = {}
    unlabeled: list[TestResult] = []
    for tr in test_results:
        if tr.test.level:
            by_level.setdefault(tr.test.level, []).append(tr)
        else:
            unlabeled.append(tr)
    return by_level, unlabeled


def _step_dict(sr: StepResult, *, include_output: bool) -> dict[str, Any]:
    d: dict[str, Any] = {
        "name": sr.step.name,
        "type": sr.step.type,
        "kind": sr.step.kind,
        "result": sr.result,
    }
    if sr.step.kind == "callable" and sr.step.handler:
        d["handler"] = sr.step.handler
    if sr.duration_ms is not None:
        d["duration_ms"] = sr.duration_ms
    if include_output:
        if sr.stdout:
            d["stdout"] = sr.stdout
        if sr.stderr:
            d["stderr"] = sr.stderr
        if sr.exit_code is not None:
            d["exit_code"] = sr.exit_code
    return d


def _test_dict(tr: TestResult) -> dict[str, Any]:
    passing = tr.result == "pass"
    return {
        "name": tr.test.name,
        "scope": tr.test.scope,
        "level": tr.test.level or None,
        "result": tr.result,
        "started_at": tr.started_at,
        "completed_at": tr.completed_at,
        "steps": [
            _step_dict(sr, include_output=not passing)
            for sr in tr.step_results
        ],
    }


def write_json(
    cr: CertificationResult,
    certified_level: str | None,
    output_dir: Path,
) -> Path:
    """Write machine-readable JSON certification result."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Group tests by level, preserving manifest ordering
    tests_by_level, unlabeled = _group_tests_by_level(cr.test_results)

    levels_out = []
    for level in cr.certification.all_levels:
        trs = tests_by_level.get(level, [])
        level_result = "pass" if all(t.result == "pass" for t in trs) else "fail"
        if not trs:
            level_result = "skip"
        levels_out.append({
            "level": level,
            "result": level_result,
            "tests": [_test_dict(tr) for tr in trs],
        })

    doc: dict[str, Any] = {
        "schema_version": "1.0",
        "certification_version": cr.certification.version,
        "description": cr.certification.description,
        "result": cr.result,
        "certified_level": certified_level,
        "max_certification_level": cr.certification.max_certification_level,
        "started_at": cr.started_at,
        "completed_at": cr.completed_at,
        "levels": levels_out,
    }

    if unlabeled:
        doc["unlabeled_tests"] = [_test_dict(tr) for tr in unlabeled]

    dest = output_dir / f"cert-{cr.certification.version}.json"
    dest.write_text(json.dumps(doc, indent=2) + "\n")
    return dest


def _fmt_duration_md(ms: int | None) -> str:
    if ms is None:
        return ""
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def write_markdown(
    cr: CertificationResult,
    certified_level: str | None,
    output_dir: Path,
) -> Path:
    """Write human-readable Markdown certification report."""
    output_dir.mkdir(parents=True, exist_ok=True)

    result_label = "PASS" if cr.result == "pass" else "FAIL"
    lines: list[str] = []
    w = lines.append

    w(f"## SEV Certification {cr.certification.version} -- {result_label}")
    w("")
    w(f"**Certified level:** {certified_level or 'none'}")
    if cr.certification.max_certification_level:
        w(f"**Max certification level:** {cr.certification.max_certification_level}")
    w(f"**Started:** {cr.started_at}")
    w(f"**Completed:** {cr.completed_at}")
    w("")

    # Group tests by level
    tests_by_level, unlabeled = _group_tests_by_level(cr.test_results)

    # Collect failures for details section
    failures: list[TestResult] = []

    for level in cr.certification.all_levels:
        trs = tests_by_level.get(level, [])
        if not trs:
            continue
        w(f"### Level {level}")
        w("")
        w("| Test | Scope | Result |")
        w("|------|-------|--------|")
        for tr in trs:
            icon = _RESULT_ICON.get(tr.result, tr.result)
            w(f"| {tr.test.name} | {tr.test.scope} | {icon} |")
            if tr.result != "pass":
                failures.append(tr)
        w("")

    if unlabeled:
        w("### Other Tests")
        w("")
        w("| Test | Scope | Result |")
        w("|------|-------|--------|")
        for tr in unlabeled:
            icon = _RESULT_ICON.get(tr.result, tr.result)
            w(f"| {tr.test.name} | {tr.test.scope} | {icon} |")
            if tr.result != "pass":
                failures.append(tr)
        w("")

    if failures:
        w("### Failure Details")
        w("")
        for tr in failures:
            w("<details>")
            w(f"<summary>{tr.test.name} ({tr.result})</summary>")
            w("")
            for sr in tr.step_results:
                if sr.result in ("pass", "skip"):
                    continue
                w(f"**{sr.step.name}** — {sr.result}")
                duration = _fmt_duration_md(sr.duration_ms)
                if duration:
                    w(f"Duration: {duration}")
                if sr.stderr:
                    w("```")
                    w(sr.stderr.rstrip())
                    w("```")
                elif sr.stdout:
                    w("```")
                    w(sr.stdout.rstrip())
                    w("```")
            w("")
            w("</details>")
            w("")

    dest = output_dir / f"cert-{cr.certification.version}.md"
    dest.write_text("\n".join(lines) + "\n")
    return dest
