#!/usr/bin/env python3
"""
PPT Master - Build State CLI (P1 Persistent Build State)

Stable CLI entry point for the project's execution/orchestration state:
phase, per-slide revision/status/dirty tracking, job queue, validation-gate
mirror, export readiness. Implementation lives in ``runtime/``. This file
never carries design intent, content, quality provenance, or audit history —
see references/artifact-ownership.md.

Usage:
    python3 scripts/build_state.py init <project_path> [--route default|quick|beautify|image-to-pptx|edit-native] [--pages P01,P02,...]
    python3 scripts/build_state.py show <project_path> [--slide P01] [--json]
    python3 scripts/build_state.py set-phase <project_path> <planned|building|ready|exporting|exported>
    python3 scripts/build_state.py bump-plan-revision <project_path> [--note TEXT]
    python3 scripts/build_state.py submit-slide <project_path> <slide_id> --expected-revision N
        [--status planned|building|ready|dirty|validating|failed|stale]
        [--from-file <path-to-svg>] [--content-hash sha256:<hex>] [--dirty true|false]
    python3 scripts/build_state.py set-gate <project_path> <early|final> <pending|passed|failed|skipped> [--report <path>]
    python3 scripts/build_state.py set-export <project_path> --dirty true|false [--last-export <path>]
    python3 scripts/build_state.py job enqueue <project_path> --type ai-edit|direct-edit|regen|export [--slide P01] [--expected-revision N] [--note TEXT]
    python3 scripts/build_state.py job list <project_path> [--status queued|in_progress|done|rejected|cancelled]
    python3 scripts/build_state.py job dequeue <project_path>
    python3 scripts/build_state.py job done <project_path> <job_id>
    python3 scripts/build_state.py job reject <project_path> <job_id> --reason TEXT
    python3 scripts/build_state.py resume <project_path> [--json] [--apply-reconciliation]
    python3 scripts/build_state.py stop-check <project_path> [--json] [--max-blocks 3]
    python3 scripts/build_state.py set-hooks-mode <project_path> <shadow|enforce>

Examples:
    python3 scripts/build_state.py init projects/demo --route quick
    python3 scripts/build_state.py submit-slide projects/demo P01 --expected-revision 0 --status ready --from-file projects/demo/svg_output/01_cover.svg
    python3 scripts/build_state.py resume projects/demo --json

Exit codes:
    0  success
    1  generic error (bad args, missing project, validation failure)
    3  STALE_EDIT — a slide mutation's --expected-revision did not match the on-disk revision
    4  build state is locked by another in-flight invocation (timeout exceeded)
    5  stop-check: BUILD_INCOMPLETE (blocked, still within --max-blocks)
    6  stop-check: QUALITY_GATE_UNRESOLVED (gave up after --max-blocks consecutive blocks)

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from attribution_guard import require_skill_integrity  # noqa: E402
from console_encoding import configure_utf8_stdio  # noqa: E402

configure_utf8_stdio()

from runtime.build_state import (  # noqa: E402
    BuildStateError,
    BuildStateLockedError,
    StaleEditError,
    load,
)
from runtime import controller  # noqa: E402
from runtime import jobs as job_ops  # noqa: E402
from runtime import resume as resume_ops  # noqa: E402
from runtime import revisions  # noqa: E402
from runtime.hook_rules import resume as resume_hook  # noqa: E402
from runtime.hook_rules import stop as stop_hook  # noqa: E402


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _parse_bool(raw: str) -> bool:
    if raw.lower() in ("true", "1", "yes"):
        return True
    if raw.lower() in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got {raw!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PPT Master persistent build/execution state.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create build_state.json for a project")
    p_init.add_argument("project_path")
    p_init.add_argument("--route", default="default", choices=[
        "default", "quick", "beautify", "image-to-pptx", "edit-native",
    ])
    p_init.add_argument("--pages", default=None, help="Comma-separated P<NN> ids")

    p_show = sub.add_parser("show", help="Print the current build state")
    p_show.add_argument("project_path")
    p_show.add_argument("--slide", default=None)
    p_show.add_argument("--json", action="store_true")

    p_phase = sub.add_parser("set-phase", help="Transition project.phase")
    p_phase.add_argument("project_path")
    p_phase.add_argument("phase", choices=[
        "planned", "building", "ready", "exporting", "exported",
    ])

    p_bump = sub.add_parser("bump-plan-revision", help="Increment plan.revision; cascade stale")
    p_bump.add_argument("project_path")
    p_bump.add_argument("--note", default=None)

    p_submit = sub.add_parser("submit-slide", help="Apply one revision-safe slide mutation")
    p_submit.add_argument("project_path")
    p_submit.add_argument("slide_id")
    p_submit.add_argument("--expected-revision", type=int, required=True)
    p_submit.add_argument("--status", default=None, choices=[
        "planned", "building", "ready", "dirty", "validating", "failed", "stale",
    ])
    p_submit.add_argument("--from-file", default=None)
    p_submit.add_argument("--content-hash", default=None)
    p_submit.add_argument("--dirty", type=_parse_bool, default=None)

    p_gate = sub.add_parser("set-gate", help="Mirror a validation gate's outcome")
    p_gate.add_argument("project_path")
    p_gate.add_argument("gate", choices=["early", "final"])
    p_gate.add_argument("status", choices=["pending", "passed", "failed", "skipped"])
    p_gate.add_argument("--report", default=None)

    p_export = sub.add_parser("set-export", help="Set export dirty flag / last export path")
    p_export.add_argument("project_path")
    p_export.add_argument("--dirty", type=_parse_bool, required=True)
    p_export.add_argument("--last-export", default=None)

    p_job = sub.add_parser("job", help="Job queue operations")
    job_sub = p_job.add_subparsers(dest="job_command", required=True)

    p_job_enqueue = job_sub.add_parser("enqueue")
    p_job_enqueue.add_argument("project_path")
    p_job_enqueue.add_argument("--type", dest="job_type", required=True, choices=[
        "ai-edit", "direct-edit", "regen", "export",
    ])
    p_job_enqueue.add_argument("--slide", default=None)
    p_job_enqueue.add_argument("--expected-revision", type=int, default=None)
    p_job_enqueue.add_argument("--note", default="")

    p_job_list = job_sub.add_parser("list")
    p_job_list.add_argument("project_path")
    p_job_list.add_argument("--status", default=None, choices=[
        "queued", "in_progress", "done", "rejected", "cancelled",
    ])

    p_job_dequeue = job_sub.add_parser("dequeue")
    p_job_dequeue.add_argument("project_path")

    p_job_done = job_sub.add_parser("done")
    p_job_done.add_argument("project_path")
    p_job_done.add_argument("job_id")

    p_job_reject = job_sub.add_parser("reject")
    p_job_reject.add_argument("project_path")
    p_job_reject.add_argument("job_id")
    p_job_reject.add_argument("--reason", required=True)

    p_resume = sub.add_parser("resume", help="Report what to do next, from disk alone")
    p_resume.add_argument("project_path")
    p_resume.add_argument("--json", action="store_true")
    p_resume.add_argument(
        "--apply-reconciliation", action="store_true",
        help="Flip any EXTERNAL_ARTIFACT_CHANGE slide to dirty (default: report-only, no mutation)",
    )

    p_stop = sub.add_parser("stop-check", help="Refuse a premature 'done' (P4 Stop Hook)")
    p_stop.add_argument("project_path")
    p_stop.add_argument("--json", action="store_true")
    p_stop.add_argument("--max-blocks", type=int, default=3)

    p_hooks_mode = sub.add_parser("set-hooks-mode", help="Set hooks.mode (shadow|enforce)")
    p_hooks_mode.add_argument("project_path")
    p_hooks_mode.add_argument("mode", choices=["shadow", "enforce"])

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_path = Path(args.project_path)

    try:
        if args.command == "init":
            if not project_path.is_dir():
                parser.error(f"project path does not exist: {project_path}")
            pages = args.pages.split(",") if args.pages else []
            controller.init_state(project_path, route=args.route, pages=[p.strip() for p in pages if p.strip()])
            print(f"[OK] build_state.json created: {project_path}")
            return 0

        if args.command == "show":
            state = load(project_path)
            if state is None:
                print(json.dumps({"mode": "legacy", "reason": "no build_state.json"}))
                return 0
            payload = state.to_json()
            if args.slide:
                slide = payload["slides"].get(args.slide)
                if slide is None:
                    print(f"[ERROR] no such slide: {args.slide}", file=sys.stderr)
                    return 1
                _print_json({args.slide: slide})
                return 0
            _print_json(payload)
            return 0

        if args.command == "set-phase":
            controller.set_phase(project_path, args.phase)
            print(f"[OK] phase -> {args.phase}")
            return 0

        if args.command == "bump-plan-revision":
            affected = revisions.bump_plan_revision(project_path, note=args.note)
            print(f"[OK] plan revision bumped; stale slides: {affected or '(none)'}")
            return 0

        if args.command == "submit-slide":
            slide = revisions.submit_slide(
                project_path,
                args.slide_id,
                expected_revision=args.expected_revision,
                status=args.status,
                content_hash=args.content_hash,
                from_file=Path(args.from_file) if args.from_file else None,
                dirty=args.dirty,
            )
            print(f"[OK] {args.slide_id} -> revision {slide.revision} ({slide.status})")
            return 0

        if args.command == "set-gate":
            controller.set_gate(
                project_path,
                args.gate,
                args.status,
                report_path=Path(args.report) if args.report else None,
            )
            print(f"[OK] {args.gate} gate -> {args.status}")
            return 0

        if args.command == "set-export":
            controller.set_export(project_path, dirty=args.dirty, last_export=args.last_export)
            print(f"[OK] export.dirty -> {args.dirty}")
            return 0

        if args.command == "job":
            if args.job_command == "enqueue":
                job = job_ops.enqueue(
                    project_path,
                    job_type=args.job_type,
                    slide=args.slide,
                    expected_revision=args.expected_revision,
                    note=args.note,
                )
                print(f"[OK] enqueued {job.id} ({job.type})")
                return 0
            if args.job_command == "list":
                jobs = job_ops.list_jobs(project_path, status=args.status)
                _print_json([asdict(job) for job in jobs])
                return 0
            if args.job_command == "dequeue":
                job = job_ops.dequeue(project_path)
                _print_json(asdict(job) if job else None)
                return 0
            if args.job_command == "done":
                job = job_ops.mark_done(project_path, args.job_id)
                print(f"[OK] {job.id} -> done")
                return 0
            if args.job_command == "reject":
                job = job_ops.mark_rejected(project_path, args.job_id, reason=args.reason)
                print(f"[OK] {job.id} -> rejected ({args.reason})")
                return 0
            parser.error(f"unknown job subcommand: {args.job_command}")
            return 1

        if args.command == "resume":
            report = resume_ops.resume_report(project_path)
            reconciliation = resume_hook.reconcile(project_path, apply=args.apply_reconciliation)
            if reconciliation:
                report["reconciliation"] = reconciliation
            if args.json:
                _print_json(report)
            else:
                print(f"[OK] {report}")
            return 0

        if args.command == "stop-check":
            result = stop_hook.stop_check(project_path, max_blocks=args.max_blocks)
            if args.json:
                _print_json(result)
            else:
                print(f"[OK] {result}")
            if result.get("status") == "failed":
                return 6
            if result.get("decision") == "block":
                return 5
            return 0

        if args.command == "set-hooks-mode":
            controller.set_hooks_mode(project_path, args.mode)
            print(f"[OK] hooks.mode -> {args.mode}")
            return 0

        parser.error(f"unknown command: {args.command}")
        return 1
    except StaleEditError as exc:
        _print_json({
            "error": "STALE_EDIT",
            "slide": exc.slide_id,
            "expected_revision": exc.expected,
            "actual_revision": exc.actual,
            "message": (
                f"{exc.slide_id} is at revision {exc.actual}; re-read and retry "
                f"with --expected-revision {exc.actual}"
            ),
        })
        return 3
    except BuildStateLockedError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 4
    except BuildStateError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    require_skill_integrity()
    raise SystemExit(main())
