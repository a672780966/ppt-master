#!/usr/bin/env python3
"""
PPT Master - Resume-from-Disk Report (P1)

Answers "what should happen next" purely from build_state.json (plus
design_spec.md / spec_lock.md presence), never from "the model should
remember where it left off". Backward compatible: reports legacy mode when
build_state.json is absent so callers (workflows/stages/resume-execute.md)
fall back to today's file-presence-only behavior.

Usage:
    Imported by scripts/build_state.py.

Examples:
    from runtime.resume import resume_report

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

from pathlib import Path

from runtime.build_state import BuildState, load


def _slide_sort_key(slide_id: str) -> int:
    try:
        return int(slide_id[1:])
    except ValueError:
        return 0


def resume_report(project_path: Path) -> dict[str, object]:
    """Return the first actionable item, in priority order, or nothing_pending."""
    project_path = Path(project_path)
    state = load(project_path)
    if state is None:
        return {"mode": "legacy", "reason": "no build_state.json"}

    ordered_slide_ids = sorted(state.slides, key=_slide_sort_key)

    for slide_id in ordered_slide_ids:
        slide = state.slides[slide_id]
        if slide.status in ("planned", "building", "dirty", "stale", "failed"):
            return {
                "mode": "build-state",
                "next_action": "author_slide",
                "slide": slide_id,
                "status": slide.status,
            }

    all_ready = ordered_slide_ids and all(
        state.slides[slide_id].status == "ready" for slide_id in ordered_slide_ids
    )
    if all_ready and state.final_gate != "passed":
        return {"mode": "build-state", "next_action": "run_final_gate"}

    if all_ready and state.final_gate == "passed" and state.export_dirty:
        return {"mode": "build-state", "next_action": "export"}

    queued = [job for job in state.jobs if job.status == "queued"]
    if queued:
        job = min(queued, key=lambda item: item.created_at or "")
        return {
            "mode": "build-state",
            "next_action": "process_job",
            "job": {
                "id": job.id,
                "type": job.type,
                "slide": job.slide,
                "expected_revision": job.expected_revision,
                "note": job.note,
            },
        }

    return {"mode": "build-state", "next_action": "nothing_pending"}
