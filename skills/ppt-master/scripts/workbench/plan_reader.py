#!/usr/bin/env python3
"""
PPT Master - Plan Panel data reader (P5)

No new plan schema (per the user's own SS24/SS25 constraint): reads the one
already-structured, already-written artifact with the Plan Panel's field
set -- confirm_ui/result.json (Stage-2 confirmation; Default route only) --
plus plan_revision/slide-count from build_state.json. Quick never writes
result.json (references/artifact-ownership.md), so a Quick project falls
back to just the build_state-derived fields rather than a fabricated plan
summary.

Usage:
    from workbench.plan_reader import plan_view

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

import json
from pathlib import Path

from runtime.build_state import BuildState

_PLAN_FIELDS = (
    "audience", "communication_intent", "canvas", "design_directions",
    "generation_mode", "design_spec_depth",
)


def _read_result_json(project_path: Path) -> dict[str, object] | None:
    result_path = project_path / "confirm_ui" / "result.json"
    if not result_path.is_file():
        return None
    try:
        return json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def plan_view(project_path: Path, state: BuildState | None) -> dict[str, object]:
    project_path = Path(project_path)
    result = _read_result_json(project_path)
    view: dict[str, object] = {
        "plan_revision": state.plan_revision if state else None,
        "slide_count": len(state.slides) if state else None,
        "route": state.route if state else None,
    }
    if result is None:
        view["confirmed"] = False
        view["summary"] = "Quick generate — no confirmed plan" if state and state.route == "quick" else "No confirmed plan yet"
        return view

    view["confirmed"] = True
    for field in _PLAN_FIELDS:
        if field in result:
            view[field] = result[field]
    return view
