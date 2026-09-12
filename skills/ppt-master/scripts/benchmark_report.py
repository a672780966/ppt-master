#!/usr/bin/env python3
"""
PPT Master - P0 Benchmark Report Analyzer

Reads a project's own on-disk artifacts (validation/workflow.log's NOTE
"benchmark-start"/"benchmark-end" markers, validation/svg_quality_report.json,
validation/<stem>.report.json, svg_output/P01.svg, live_preview/lock.json)
and derives every metric measurable without a new instrumentation mechanism.
Fields with no on-disk source (token counts, manual review scores) are
supplied via repeatable --set field=value and never guessed.

Read-only against the project: this never writes into validation/workflow.log
or any other project artifact, only its own output record. See
references/benchmarking/README.md for the full procedure and the
auto-vs-manual rationale for every field.

Usage:
    python3 scripts/benchmark_report.py <project_path> --case-id <id> --route <route>
        [--set field=value ...] [--out <path>] [--notes <text>]

Examples:
    python3 scripts/benchmark_report.py projects/demo --case-id text-teaching --route quick \
        --set input_tokens=12000 --set output_tokens=4000 \
        --set manual_correction_count=0 --set visual_score=4 --set resume_success=N/A

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from attribution_guard import require_skill_integrity  # noqa: E402
from console_encoding import configure_utf8_stdio  # noqa: E402

configure_utf8_stdio()

_SKILL_DIR = _SCRIPTS_DIR.parent
SCHEMA = "ppt-master.benchmark-run.v1"
WORKFLOW_LOG_RELATIVE_PATH = Path("validation/workflow.log")
DEFAULT_RESULTS_DIR = _SKILL_DIR / "references" / "benchmarking" / "results"

_NOTE_HEADER_RE = re.compile(r"^=== (?P<ts>\S+) NOTE id=\S+ ===$")
_PY_START_RE = re.compile(r"^=== (?P<ts>\S+) PYTHON run=(?P<run_id>\S+) ===$")
_PY_END_RE = re.compile(
    r"^=== (?P<ts>\S+) END run=(?P<run_id>\S+) elapsed_ms=(?P<elapsed_ms>\d+) "
    r"output_lines=\d+ retained=\d+ omitted=\d+ ===$"
)
_ARGV_RE = re.compile(r"^argv: (?P<json>\[.*\])$")

# Field name -> coercion applied to a raw --set string.
_METRIC_FIELD_TYPES: dict[str, type] = {
    "input_tokens": int,
    "output_tokens": int,
    "wall_clock_seconds": float,
    "tool_call_count": int,
    "time_to_first_slide_seconds": float,
    "time_to_usable_preview_seconds": float,
    "total_build_seconds": float,
    "checker_error_count": int,
    "checker_warning_count": int,
    "rework_count": int,
    "pptx_postflight_result": str,
    "manual_correction_count": int,
    "visual_score": float,
    "resume_success": str,
}


class BenchmarkReportError(RuntimeError):
    """Raised when the run cannot be analyzed from the files on disk."""


def _parse_timestamp(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _read_log_lines(project_path: Path) -> list[str]:
    log_path = project_path / WORKFLOW_LOG_RELATIVE_PATH
    if not log_path.is_file():
        raise BenchmarkReportError(f"workflow log not found: {log_path}")
    return log_path.read_text(encoding="utf-8", errors="replace").splitlines()


def _find_note_timestamp(lines: list[str], marker_text: str) -> datetime:
    """Return the timestamp of the last NOTE record whose body matches marker_text."""
    found: datetime | None = None
    for index, line in enumerate(lines):
        match = _NOTE_HEADER_RE.match(line.strip())
        if not match:
            continue
        body = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if body == marker_text:
            found = _parse_timestamp(match.group("ts"))
    if found is None:
        raise BenchmarkReportError(
            f"no NOTE record with body {marker_text!r} found in workflow.log; "
            "run workflow_log.py with that exact message first"
        )
    return found


def _iter_python_runs(lines: list[str]) -> list[dict[str, object]]:
    """Return one record per PYTHON run=... envelope: start/end timestamps + argv."""
    runs: list[dict[str, object]] = []
    pending: dict[str, object] | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        start_match = _PY_START_RE.match(stripped)
        if start_match:
            pending = {
                "run_id": start_match.group("run_id"),
                "start_ts": _parse_timestamp(start_match.group("ts")),
                "argv": [],
            }
            for lookahead in lines[index + 1 : index + 4]:
                argv_match = _ARGV_RE.match(lookahead.strip())
                if argv_match:
                    try:
                        pending["argv"] = json.loads(argv_match.group("json"))
                    except json.JSONDecodeError:
                        pending["argv"] = []
                    break
            continue
        end_match = _PY_END_RE.match(stripped)
        if (
            end_match
            and pending is not None
            and end_match.group("run_id") == pending["run_id"]
        ):
            pending["end_ts"] = _parse_timestamp(end_match.group("ts"))
            runs.append(pending)
            pending = None
    return runs


def _script_name(argv: list[object]) -> str | None:
    for token in argv:
        text = str(token)
        if text.endswith(".py"):
            return Path(text).name
    return None


def derive_auto_metrics(project_path: Path) -> dict[str, object]:
    """Derive every metric this tool can read from files already on disk."""
    lines = _read_log_lines(project_path)
    start = _find_note_timestamp(lines, "benchmark-start")
    end = _find_note_timestamp(lines, "benchmark-end")
    if end < start:
        raise BenchmarkReportError("benchmark-end timestamp precedes benchmark-start")
    wall_clock = (end - start).total_seconds()

    all_runs = _iter_python_runs(lines)
    runs = [run for run in all_runs if start <= run["start_ts"] <= end]

    early_gate_calls = 0
    final_gate_calls = 0
    for run in runs:
        if _script_name(run["argv"]) != "svg_quality_checker.py":
            continue
        argv_text = " ".join(str(token) for token in run["argv"])
        if "--stage early" in argv_text:
            early_gate_calls += 1
        elif "--stage final" in argv_text:
            final_gate_calls += 1
    rework_count = max(0, early_gate_calls - 1) + max(0, final_gate_calls - 1)

    metrics: dict[str, object] = {
        "wall_clock_seconds": wall_clock,
        "total_build_seconds": wall_clock,
        "tool_call_count": len(runs),
        "rework_count": rework_count,
    }

    # Real authoring uses the "<index>_<page_name>.svg" convention documented
    # in executor-base.md's "SVG File Naming Convention" (e.g. "01_cover.svg"),
    # not a literal "P01.svg" — sort lexicographically to find the first page.
    svg_output_dir = project_path / "svg_output"
    first_slide = (
        sorted(svg_output_dir.glob("*.svg"))[0]
        if svg_output_dir.is_dir() and any(svg_output_dir.glob("*.svg"))
        else None
    )
    metrics["time_to_first_slide_seconds"] = (
        (
            datetime.fromtimestamp(first_slide.stat().st_mtime, tz=timezone.utc) - start
        ).total_seconds()
        if first_slide is not None
        else None
    )

    preview_lock = project_path / "live_preview" / "lock.json"
    metrics["time_to_usable_preview_seconds"] = (
        (
            datetime.fromtimestamp(preview_lock.stat().st_mtime, tz=timezone.utc) - start
        ).total_seconds()
        if preview_lock.is_file()
        else None
    )

    quality_report = project_path / "validation" / "svg_quality_report.json"
    metrics["checker_error_count"] = None
    metrics["checker_warning_count"] = None
    if quality_report.is_file():
        try:
            data = json.loads(quality_report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        categories = data.get("categories", {})
        metrics["checker_error_count"] = categories.get("blocking", {}).get("count")
        metrics["checker_warning_count"] = categories.get("introduced", {}).get("count")

    metrics["pptx_postflight_result"] = None
    validation_dir = project_path / "validation"
    if validation_dir.is_dir():
        for candidate in sorted(validation_dir.glob("*.report.json"), reverse=True):
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("schema") == "ppt-master.pptx-postflight-report.v1":
                metrics["pptx_postflight_result"] = data.get("status")
                break

    return metrics


def _coerce(field: str, raw: str) -> object:
    field_type = _METRIC_FIELD_TYPES.get(field)
    if field_type is None:
        raise BenchmarkReportError(
            f"unknown metric field {field!r}; see templates/schemas/benchmark_run.schema.json"
        )
    if field_type is int:
        return int(raw)
    if field_type is float:
        return float(raw)
    return raw


def _parse_set_options(pairs: list[str]) -> dict[str, object]:
    overrides: dict[str, object] = {}
    for pair in pairs:
        if "=" not in pair:
            raise BenchmarkReportError(f"--set expects field=value, got {pair!r}")
        field, raw_value = pair.split("=", 1)
        overrides[field.strip()] = _coerce(field.strip(), raw_value.strip())
    return overrides


def build_record(
    project_path: Path,
    case_id: str,
    route: str,
    overrides: dict[str, object],
    notes: str | None,
) -> dict[str, object]:
    metrics = derive_auto_metrics(project_path)
    for field in _METRIC_FIELD_TYPES:
        metrics.setdefault(field, None)
    metrics.update(overrides)
    record: dict[str, object] = {
        "schema": SCHEMA,
        "case_id": case_id,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "route": route,
        "project_path": str(project_path.resolve()),
        "metrics": metrics,
    }
    if notes:
        record["notes"] = notes
    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Derive P0 benchmark metrics for one project from its own on-disk "
            "artifacts, plus explicit --set overrides for fields with no repo-file "
            "source (tokens, manual review)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_path", help="Existing project directory")
    parser.add_argument(
        "--case-id", required=True, help="Matches a row id in references/benchmarking/corpus.md"
    )
    parser.add_argument(
        "--route", required=True, help="Route/profile actually used, e.g. quick, default"
    )
    parser.add_argument(
        "--set",
        dest="set_pairs",
        action="append",
        default=[],
        metavar="field=value",
        help="Repeatable; supplies or overrides one metrics field",
    )
    parser.add_argument("--notes", default=None, help="Any deviation from corpus.md's instruction")
    parser.add_argument(
        "--out",
        default=None,
        help="Output path; defaults to references/benchmarking/results/<case_id>_<ts>.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_path = Path(args.project_path)
    if not project_path.is_dir():
        parser.error(f"project path does not exist: {project_path}")

    try:
        overrides = _parse_set_options(args.set_pairs)
        record = build_record(project_path, args.case_id, args.route, overrides, args.notes)
    except BenchmarkReportError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if args.out:
        out_path = Path(args.out)
    else:
        DEFAULT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = DEFAULT_RESULTS_DIR / f"{args.case_id}_{timestamp}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[OK] Benchmark record written: {out_path}")
    return 0


if __name__ == "__main__":
    require_skill_integrity()
    raise SystemExit(main())
