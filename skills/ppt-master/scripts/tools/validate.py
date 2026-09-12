#!/usr/bin/env python3
"""
PPT Master - Semantic Tool: slide.validate

Wraps scripts/svg_quality_checker.py. The checker itself has no stable error
codes (confirmed by direct inspection: its JSON report's categories.*.issues
entries are plain {"file", "message"} pairs with a free-form English
sentence, and its own internal `_categorize_issue` only buckets into five
coarse labels for a CLI tip list). This module adds the classification layer
the agent needs: a short, stable code + severity + suggested_action per
issue, built from the real message patterns the checker is known to emit.
Unmatched messages get code "UNCLASSIFIED" with the original message
preserved, never a fabricated code.

The full human-readable report stays on disk exactly as the checker writes
it (validation/svg_quality_report.json, svg_quality_page_report.json, ...);
this tool never deletes or replaces it, only reads and summarizes it.

Dependencies:
    None beyond existing sibling PPT Master modules (invokes the checker as
    a subprocess to avoid re-implementing or importing its ~9,600-line
    internal engine).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from .errors import ToolError

_REPORT_FILENAME_BY_STAGE = {
    "final": "svg_quality_report.json",
    "first-page": "svg_quality_first_page_report.json",
    "early": "svg_quality_early_report.json",
    "page": "svg_quality_page_report.json",
}

_ELEMENT_RE = re.compile(r'<[a-zA-Z]+\s+\([^)]*\)|id="(?P<id>[^"]+)"')

_CLASSIFIERS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"^<text>.*exceeds.*data-pptx-bounds"), "TEXT_OVERFLOW", "relayout"),
    (re.compile(r"^<\w+>.*exceeds.*(?:data-pptx-bounds|bounds)"), "BOUNDS_OVERFLOW", "relayout"),
    (re.compile(r"overlap", re.IGNORECASE), "ROOT_GROUP_OVERLAP", "relayout"),
    (re.compile(r"paragraph-like line run"), "SPLIT_PARAGRAPH", "merge_into_one_text_frame"),
    (re.compile(r"Noncanonical compact authoring"), "NONCANONICAL_STYLE", "run_compact_svg_styles"),
    (re.compile(r"Invalid XML|not well-formed"), "XML_MALFORMED", "fix_xml_syntax"),
    (re.compile(r"viewBox"), "VIEWBOX_ISSUE", "fix_viewbox"),
    (re.compile(r"foreignObject", re.IGNORECASE), "FOREIGN_OBJECT", "remove_foreign_object"),
    (re.compile(r"font", re.IGNORECASE), "FONT_ISSUE", "fix_font"),
    (re.compile(r"paint|color value", re.IGNORECASE), "PAINT_ISSUE", "fix_paint"),
    (re.compile(r"fallback.*(?:stale|sha256)|sha256.*fallback", re.IGNORECASE), "STALE_NATIVE_FALLBACK", "rerun_stamp_native_fallbacks"),
)


def _classify(message: str) -> tuple[str, str]:
    for pattern, code, action in _CLASSIFIERS:
        if pattern.search(message):
            return code, action
    return "UNCLASSIFIED", "read_full_report"


def _element_from_message(message: str) -> str | None:
    match = re.search(r'id="([^"]+)"', message)
    return match.group(1) if match else None


def _envelope_issue(message: str, severity: str) -> dict[str, object]:
    code, action = _classify(message)
    issue: dict[str, object] = {
        "code": code,
        "severity": severity,
        "suggested_action": action,
        "message": message,
    }
    element = _element_from_message(message)
    if element:
        issue["element"] = element
    return issue


def slide_validate(payload: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """Run the real checker, return a short structured envelope.

    payload:
        project (required): project directory path
        page (optional): one page basename/path under svg_output/, e.g. "05_comparison.svg";
            omit to validate the whole deck at the given stage
        stage (optional): "early" | "page" | "first-page" | "final"; defaults to
            "page" when `page` is given, else "final"
        quick_generate (optional bool): pass --quick-generate to the checker
    """
    project = payload.get("project")
    if not isinstance(project, str) or not project:
        raise ToolError("INVALID_PROJECT", "project must be a non-empty path string")
    project_path = Path(project)
    if not project_path.is_dir():
        raise ToolError("INVALID_PROJECT", f"project directory not found: {project_path}")

    page = payload.get("page")
    stage = payload.get("stage") or ("page" if page else "final")
    if stage not in _REPORT_FILENAME_BY_STAGE:
        raise ToolError("INVALID_STAGE", f"stage must be one of {tuple(_REPORT_FILENAME_BY_STAGE)}")
    if stage == "page" and not page:
        raise ToolError("INVALID_STAGE", "stage 'page' requires payload.page")

    checker_script = Path(__file__).resolve().parents[1] / "svg_quality_checker.py"
    command = [
        sys.executable, str(checker_script), str(project_path),
        "--canonical-authoring", "--stage", stage, "--json",
    ]
    if page:
        command += ["--page", str(page)]
    if payload.get("quick_generate"):
        command.append("--quick-generate")

    result = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )

    report_path = project_path / "validation" / _REPORT_FILENAME_BY_STAGE[stage]
    if not report_path.is_file():
        raise ToolError(
            "CHECKER_FAILED",
            "svg_quality_checker.py did not produce a JSON report",
            exit_code=result.returncode,
            stderr_tail=result.stderr[-2000:],
        )

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError("CHECKER_FAILED", f"cannot read {report_path}: {exc}") from exc

    errors: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    files = report.get("files", [])
    if page:
        page_name = Path(page).name
        files = [f for f in files if Path(f.get("file", "")).name == page_name]
    for file_entry in files:
        for message in file_entry.get("errors", []):
            errors.append(_envelope_issue(message, "blocking"))
        for message in file_entry.get("warnings", []):
            warnings.append(_envelope_issue(message, "advisory"))

    fields = {
        "ok": len(errors) == 0,
        "errors": errors,
        "report_path": str(report_path),
    }
    warning_messages = [f"{w['code']}: {w['message']}" for w in warnings]
    return fields, warning_messages
