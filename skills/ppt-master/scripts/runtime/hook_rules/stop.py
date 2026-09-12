#!/usr/bin/env python3
"""
PPT Master - Stop Hook (P4)

Wraps runtime.resume.resume_report() (P1) -- reused, not reimplemented, per
the user's own instruction not to duplicate Resume's logic. "nothing left to
do" and "safe to call done" are the same question asked from two directions;
Stop just asks it at the moment the Executor wants to claim completion.

Repeated-block backoff (user's explicit requirement): after --max-blocks
consecutive blocks with no progress, stop blocking and surface a failure
instead -- Stop's job is refusing a false "done," not torturing the model
forever on an unresolved gate.

Usage:
    Imported by scripts/build_state.py (the `stop-check` subcommand).

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

from pathlib import Path

from runtime.build_state import locked_state
from runtime.resume import resume_report
from workflow_log import append_note

_REMAINING_DESCRIPTIONS = {
    "author_slide": "{slide} is {status}",
    "run_final_gate": "final validation is pending",
    "export": "PPTX export is dirty",
    "process_job": "job {job[id]} ({job[type]}) is still queued",
}


def _describe(report: dict[str, object]) -> str:
    action = report.get("next_action", "")
    template = _REMAINING_DESCRIPTIONS.get(action, action)
    try:
        return template.format(**report)
    except (KeyError, IndexError):
        return action


def _log(project_path: Path, message: str) -> None:
    try:
        append_note(project_path, f"HOOK_STOP {message}")
    except OSError:
        pass  # advisory only -- artifact-ownership.md: recording failure never blocks


def stop_check(project_path: Path, *, max_blocks: int = 3) -> dict[str, object]:
    project_path = Path(project_path)
    report = resume_report(project_path)

    if report.get("mode") == "legacy":
        return {"decision": "allow"}

    if report.get("next_action") == "nothing_pending":
        with locked_state(project_path, create_if_missing=False) as state:
            state.stop_block_count = 0
        return {"decision": "allow"}

    remaining = [_describe(report)]
    with locked_state(project_path, create_if_missing=False) as state:
        state.stop_block_count += 1
        count = state.stop_block_count

    if count > max_blocks:
        _log(project_path, f"status=failed reason=QUALITY_GATE_UNRESOLVED remaining={remaining}")
        return {
            "status": "failed",
            "reason": "QUALITY_GATE_UNRESOLVED",
            "blocking_errors": remaining,
            "attempts": count,
        }
    _log(project_path, f"decision=block code=BUILD_INCOMPLETE remaining={remaining} attempts={count}")
    return {
        "decision": "block",
        "code": "BUILD_INCOMPLETE",
        "remaining": remaining,
        "attempts": count,
        "max_blocks": max_blocks,
    }
