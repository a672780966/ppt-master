#!/usr/bin/env python3
"""
PPT Master - AI Edit CLI (P6)

The orchestrating agent's discovery-and-submission surface for the Live
AI Edit Loop -- the "host-native AIEditRunner" the P6 plan settled on.
There is no separate model-calling service anywhere in this codebase
(confirmed by investigation before P6 was designed): every AI decision in
this Skill is the orchestrating agent's own reasoning, expressed as a tool
call. This CLI is that tool call for AI Edit jobs, exactly the same shape
as check_annotations.py already established for the pre-P6 manual
Annotate-apply flow (see workflows/stages/ai-edit.md for the full
discover -> read context -> reason -> submit procedure):

    1. `list` a project's queued jobs (mirrors check_annotations.py)
    2. `context <job_id>` to read the compiled AIEditContext
    3. reason about the instruction using your own native capability
    4. `submit-plan <job_id> --plan-file <path>` with the resulting EditPlan

Every subcommand calls straight into scripts/runtime/ai_edit_jobs.py --
the exact same functions scripts/workbench/ai_edit_api.py's Flask routes
call. One implementation, two entry points.

Usage:
    python3 scripts/ai_edit_cli.py list <project_path> [--slide P01] [--status queued] [--json]
    python3 scripts/ai_edit_cli.py create <project_path> --slide P01 --scope selection
        --selection title-01,chart-02 --instruction "..." [--origin inline|annotation] [--annotation-id ID]
    python3 scripts/ai_edit_cli.py context <project_path> <job_id> [--json]
    python3 scripts/ai_edit_cli.py submit-plan <project_path> <job_id> --plan-file <path> [--json]
    python3 scripts/ai_edit_cli.py cancel <project_path> <job_id>
    python3 scripts/ai_edit_cli.py retry <project_path> <job_id>
    python3 scripts/ai_edit_cli.py undo <project_path> <job_id>

Examples:
    python3 scripts/ai_edit_cli.py list projects/demo --status queued --json
    python3 scripts/ai_edit_cli.py context projects/demo a1b2c3d4e5f6 --json

Exit codes:
    0  success
    1  the job/request was rejected (see the printed error's "code" field)

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from attribution_guard import require_skill_integrity  # noqa: E402
from console_encoding import configure_utf8_stdio  # noqa: E402

configure_utf8_stdio()

from runtime import ai_edit_jobs as jobs  # noqa: E402


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_error(exc: jobs.AIEditJobError) -> None:
    error = {"code": exc.code, "message": exc.message}
    error.update(exc.details)
    _print_json({"ok": False, "errors": [error]})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PPT Master AI Edit CLI (P6)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list")
    p_list.add_argument("project_path")
    p_list.add_argument("--slide", default=None)
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--json", action="store_true")

    p_create = sub.add_parser("create")
    p_create.add_argument("project_path")
    p_create.add_argument("--slide", required=True)
    p_create.add_argument("--scope", choices=["element", "selection", "slide"], default="selection")
    p_create.add_argument("--selection", default="", help="comma-separated element ids")
    p_create.add_argument("--instruction", required=True)
    p_create.add_argument("--origin", choices=["inline", "annotation"], default="inline")
    p_create.add_argument("--annotation-id", default=None)
    p_create.add_argument("--json", action="store_true")

    p_context = sub.add_parser("context")
    p_context.add_argument("project_path")
    p_context.add_argument("job_id")
    p_context.add_argument("--json", action="store_true")

    p_submit = sub.add_parser("submit-plan")
    p_submit.add_argument("project_path")
    p_submit.add_argument("job_id")
    p_submit.add_argument("--plan-file", required=True)
    p_submit.add_argument("--json", action="store_true")

    for name in ("cancel", "retry", "undo"):
        p = sub.add_parser(name)
        p.add_argument("project_path")
        p.add_argument("job_id")
        p.add_argument("--json", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_path = Path(args.project_path)

    try:
        if args.command == "list":
            job_list = jobs.list_jobs(project_path, slide_id=args.slide, status=args.status)
            payload = {"ok": True, "jobs": [j.to_dict() for j in job_list]}
            _print_json(payload) if args.json else print(
                "\n".join(f"{j.job_id}  {j.slide_id}  {j.status}  {j.instruction!r}" for j in job_list) or "(no jobs)"
            )
            return 0

        if args.command == "create":
            selection_ids = [s for s in args.selection.split(",") if s]
            job = jobs.create_job(
                project_path, slide_id=args.slide, scope=args.scope, selection_ids=selection_ids,
                instruction=args.instruction, origin=args.origin, annotation_id=args.annotation_id,
            )
            _print_json({"ok": True, "job": job.to_dict()}) if args.json else print(f"[OK] created job {job.job_id} ({job.status})")
            return 0

        if args.command == "context":
            context = jobs.get_context(project_path, args.job_id)
            _print_json({"ok": True, "context": context})
            return 0

        if args.command == "submit-plan":
            plan = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
            job = jobs.submit_plan(project_path, args.job_id, plan)
            _print_json({"ok": True, "job": job.to_dict()}) if args.json else print(f"[OK] job {job.job_id} -> {job.status}")
            return 0

        if args.command == "cancel":
            job = jobs.cancel_job(project_path, args.job_id)
            _print_json({"ok": True, "job": job.to_dict()}) if args.json else print(f"[OK] job {job.job_id} -> {job.status}")
            return 0

        if args.command == "retry":
            job = jobs.retry_job(project_path, args.job_id)
            _print_json({"ok": True, "job": job.to_dict()}) if args.json else print(f"[OK] created job {job.job_id} ({job.status})")
            return 0

        if args.command == "undo":
            job = jobs.undo_job(project_path, args.job_id)
            _print_json({"ok": True, "job": job.to_dict()}) if args.json else print(f"[OK] job {job.job_id} -> {job.status}")
            return 0

    except jobs.AIEditJobError as exc:
        _print_error(exc)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        _print_json({"ok": False, "errors": [{"code": "INVALID_EDIT_PLAN", "message": str(exc)}]})
        return 1

    parser.error(f"unknown command: {args.command}")
    return 1


if __name__ == "__main__":
    require_skill_integrity()
    raise SystemExit(main())
