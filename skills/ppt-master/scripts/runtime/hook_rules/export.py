#!/usr/bin/env python3
"""
PPT Master - PreToolUse(deck.export) Rule (P4)

The one hook the user asked for first: before deck.export actually runs,
check that the deck is exportable, deterministically, from build_state.json
alone. Never touches the filesystem beyond loading that one file; never
calls a model; never mutates anything (PreToolUse only inspects).

A project with no build_state.json (legacy, or a route/session that never
adopted it) always allows -- P4 never makes build_state.json mandatory,
matching resume_report()'s own "legacy" fallback philosophy.

Usage:
    Imported by scripts/runtime/hook_registry.py.

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

from pathlib import Path

from runtime import controller
from runtime.build_state import BuildState, load
from runtime.hook_types import HookResult, allow, block, warn

_BLOCKING_SLIDE_STATUS_REASON = {
    "stale": "stale_slide",
    "building": "building_slide",
    "failed": "failed_slide",
}


def _reasons(state: BuildState) -> list[dict[str, object]]:
    reasons: list[dict[str, object]] = []
    for slide_id, slide in sorted(state.slides.items()):
        reason_type = _BLOCKING_SLIDE_STATUS_REASON.get(slide.status)
        if reason_type:
            reasons.append({"type": reason_type, "slide": slide_id, "status": slide.status})
        if slide.status in ("ready", "dirty") and slide.revision != slide.validated_revision:
            reasons.append({
                "type": "stale_validation",
                "slide": slide_id,
                "revision": slide.revision,
                "validated_revision": slide.validated_revision,
            })
        if slide.plan_revision != state.plan_revision:
            reasons.append({
                "type": "plan_revision_mismatch",
                "slide": slide_id,
                "slide_plan_revision": slide.plan_revision,
                "current_plan_revision": state.plan_revision,
            })
    if state.final_gate != "passed":
        reasons.append({"type": "final_gate_not_passed", "final_gate": state.final_gate})
    for job in state.jobs:
        if job.status in ("queued", "in_progress"):
            reasons.append({"type": "pending_job", "job": job.id, "job_status": job.status})
    return reasons


def pre_deck_export(payload: dict[str, object]) -> HookResult:
    project = payload.get("project")
    if not isinstance(project, str) or not project:
        return allow("PreToolUse", "deck.export")
    state = load(Path(project))
    if state is None:
        return allow("PreToolUse", "deck.export")

    reasons = _reasons(state)
    if not reasons:
        if state.early_gate == "pending" and len(state.slides) > 6:
            return warn(
                "PreToolUse", "deck.export", "EARLY_GATE_PENDING",
                "early_gate is still pending on a roster larger than 6 pages",
            )
        return allow("PreToolUse", "deck.export")

    return block(
        "PreToolUse", "deck.export", "DECK_NOT_EXPORTABLE",
        "deck is not exportable: see details.reasons",
        reasons=reasons,
    )


def post_deck_export(payload: dict[str, object], fields: dict[str, object]) -> HookResult:
    """Auto-transition export.dirty/last_export/phase on a successful export.

    The exact bookkeeping generate-pptx.md Step 7.3 already asks the
    Executor to do by hand (`set-export --dirty false ...` + `set-phase
    exported`) -- this hook does it automatically instead, the literal
    "harness executes the discipline" P4 is about. Never runs on a failed
    export (fields["ok"] is False): a failed export leaves build_state
    exactly as it was, so a retry sees the same pre-export state.
    """
    project = payload.get("project")
    if not isinstance(project, str) or not project:
        return allow("PostToolUse", "deck.export")
    state = load(Path(project))
    if state is None:
        return allow("PostToolUse", "deck.export")
    if not fields.get("ok"):
        return allow("PostToolUse", "deck.export")

    controller.set_export(Path(project), dirty=False, last_export=fields.get("pptx_path"))
    controller.set_phase(Path(project), "exported")
    return HookResult(
        hook="PostToolUse", tool="deck.export", decision="allow",
        details={"pptx_path": fields.get("pptx_path"), "phase": "exported"},
    )
