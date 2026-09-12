#!/usr/bin/env python3
"""
PPT Master - Semantic Tool: deck.export

Wraps scripts/svg_to_pptx.py as a stable contract: project / output /
expected_state in, PPTX path + postflight summary + warning summary out.
Deliberately does not implement an export *gate* (checking build_state.json
or blocking on a stale quality report before running) -- that decision
logic belongs to a future P4 Lifecycle Hook, not this P3 execution
interface. This tool only runs the exporter and reports what happened; it
never touches build_state.json itself (references/artifact-ownership.md).

Dependencies:
    None beyond existing sibling PPT Master modules (invokes the exporter as
    a subprocess rather than importing its internal package, so a future
    change to svg_to_pptx's internals never requires touching this file).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .errors import ToolError

_PPTX_LINE_PREFIX = "[PPTX] "
_REPORT_LINE_PREFIX = "[REPORT] "


def deck_export(payload: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """Export svg_output/ to a native PPTX and summarize the postflight report.

    payload:
        project (required): project directory path
        output (optional): explicit output .pptx path (-o)
        quick_generate (optional bool)
        no_notes / with_notes (optional bool, mutually exclusive)
        native_charts_and_tables (optional bool)
        expected_state (optional): {"slide_count": N} -- checked against the
            actual export and reported as a warning on mismatch, never a
            hard failure (a strict pre-export gate is P4's job, not this
            tool's)
    """
    project = payload.get("project")
    if not isinstance(project, str) or not project:
        raise ToolError("INVALID_PROJECT", "project must be a non-empty path string")
    project_path = Path(project)
    if not project_path.is_dir():
        raise ToolError("INVALID_PROJECT", f"project directory not found: {project_path}")

    exporter_script = Path(__file__).resolve().parents[1] / "svg_to_pptx.py"
    command = [sys.executable, str(exporter_script), str(project_path)]
    if payload.get("quick_generate"):
        command.append("--quick-generate")
    if payload.get("no_notes"):
        command.append("--no-notes")
    elif payload.get("with_notes"):
        command.append("--with-notes")
    if payload.get("native_charts_and_tables"):
        command.append("--native-charts-and-tables")
    output = payload.get("output")
    if output:
        command += ["-o", str(output)]

    result = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )

    pptx_path: str | None = None
    report_path: str | None = None
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith(_PPTX_LINE_PREFIX):
            pptx_path = line[len(_PPTX_LINE_PREFIX):].strip()
        elif line.startswith(_REPORT_LINE_PREFIX):
            report_path = line[len(_REPORT_LINE_PREFIX):].strip()

    if result.returncode != 0 or not pptx_path or not report_path:
        raise ToolError(
            "EXPORT_FAILED",
            "svg_to_pptx.py did not produce a PPTX and postflight report",
            exit_code=result.returncode,
            stdout_tail=result.stdout[-2000:],
            stderr_tail=result.stderr[-2000:],
        )

    try:
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError("EXPORT_FAILED", f"cannot read postflight report {report_path}: {exc}") from exc

    warnings: list[str] = []
    quality = report.get("quality", {})
    for issue in quality.get("categories", {}).get("introduced", {}).get("issues", []):
        warnings.append(f"{issue.get('file', '?')}: {issue.get('message', '')}")

    expected_state = payload.get("expected_state")
    if isinstance(expected_state, dict) and "slide_count" in expected_state:
        actual_slides = report.get("source", {}).get("svg_slide_count")
        if actual_slides != expected_state["slide_count"]:
            warnings.append(
                f"expected_state.slide_count={expected_state['slide_count']!r} "
                f"but export produced {actual_slides!r} slides"
            )

    fields = {
        "ok": report.get("status") in ("passed", "passed-with-warnings"),
        "pptx_path": pptx_path,
        "status": report.get("status"),
        "quality_gate": report.get("checks", {}).get("quality_gate"),
        "slide_count": report.get("source", {}).get("svg_slide_count"),
        "output_bytes": report.get("output", {}).get("bytes"),
        "report_path": report_path,
    }
    return fields, warnings
