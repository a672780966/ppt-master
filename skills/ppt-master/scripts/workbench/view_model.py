#!/usr/bin/env python3
"""
PPT Master - Workbench View-Model Mapping (P5)

Pure functions only: no Flask, no filesystem, no build_state.json access.
Takes the runtime.build_state dataclasses (already loaded by a caller) and
maps their internal vocabulary onto the plain-language labels a normal user
should see (P5 spec SS5/SS6/SS17) -- never the other way around. The
Workbench UI displays Runtime Truth; this module is the one place that
translation happens, so it can be unit-tested in complete isolation from
Flask/HTTP and never duplicated ad hoc inside a route handler.

Usage:
    from workbench.view_model import slide_view, build_progress, export_status

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

from runtime.build_state import BuildState, SlideState

_STATUS_LABEL = {
    "planned": "Planned",
    "building": "Building",
    "ready": "Ready",
    "dirty": "Modified",
    "validating": "Validating",
    "failed": "Failed",
    "stale": "Stale",
}

# Statuses where a stale/missing/failed validation should override the base
# status label in the compact list view -- a "ready" slide whose validation
# no longer covers its current revision is shown as "Needs validation," not
# as a plain "Ready" that would mislead the user into exporting it.
_VALIDATION_OVERRIDE_ELIGIBLE = ("ready", "dirty")


def validation_state(slide: SlideState) -> str:
    """"not_validated" | "valid" | "stale_validation" | "failed"."""
    if slide.validated_revision is None:
        return "not_validated"
    if slide.revision != slide.validated_revision:
        return "stale_validation"
    if slide.validation_status == "failed":
        return "failed"
    return "valid"


_VALIDATION_LABEL = {
    "not_validated": "Not validated",
    "valid": "Validated",
    "stale_validation": "Outdated",
    "failed": "Validation failed",
}


def slide_view(slide_id: str, slide: SlideState) -> dict[str, object]:
    """One slide's complete UI-facing view model."""
    v_state = validation_state(slide)
    status_label = _STATUS_LABEL.get(slide.status, slide.status)
    display_label = status_label
    if slide.status in _VALIDATION_OVERRIDE_ELIGIBLE and v_state != "valid":
        display_label = _VALIDATION_LABEL[v_state] if v_state == "failed" else "Needs validation"
    return {
        "id": slide_id,
        "revision": slide.revision,
        "status": slide.status,
        "status_label": status_label,
        "dirty": slide.dirty,
        "validation_state": v_state,
        "validation_label": _VALIDATION_LABEL[v_state],
        "validated_revision": slide.validated_revision,
        "validation_status": slide.validation_status,
        "display_label": display_label,
        "svg_path": slide.svg_path,
    }


def slides_view(state: BuildState) -> list[dict[str, object]]:
    def sort_key(slide_id: str) -> int:
        try:
            return int(slide_id[1:])
        except ValueError:
            return 0

    return [
        slide_view(slide_id, state.slides[slide_id])
        for slide_id in sorted(state.slides, key=sort_key)
    ]


def build_progress(state: BuildState) -> dict[str, object]:
    """The always-visible build-progress summary (P5 SS6) -- never derived
    from counting SVG files, only from real per-slide status fields."""
    total = len(state.slides)
    counts = {status: 0 for status in _STATUS_LABEL}
    needs_validation = 0
    for slide in state.slides.values():
        counts[slide.status] = counts.get(slide.status, 0) + 1
        if slide.status in _VALIDATION_OVERRIDE_ELIGIBLE and validation_state(slide) == "stale_validation":
            needs_validation += 1
    return {
        "total": total,
        "ready": counts.get("ready", 0),
        "building": counts.get("building", 0),
        "planned": counts.get("planned", 0),
        "dirty": counts.get("dirty", 0),
        "failed": counts.get("failed", 0),
        "stale": counts.get("stale", 0),
        "needs_validation": needs_validation,
        "final_gate": state.final_gate,
        "export_dirty": state.export_dirty,
    }


_EXPORT_STATUS_LABEL = {
    "never": "Never exported",
    "outdated": "Export outdated",
    "ready": "Export ready",
}


def export_status(state: BuildState) -> dict[str, object]:
    """Never just 'does the pptx file exist' -- driven by export.dirty plus
    the plan/deck revision snapshot recorded at the last successful export
    (P5 SS17)."""
    if state.last_export is None:
        code = "never"
    elif state.export_dirty:
        code = "outdated"
    else:
        code = "ready"
    return {
        "code": code,
        "label": _EXPORT_STATUS_LABEL[code],
        "last_export": state.last_export,
        "export_plan_revision": state.export_plan_revision,
        "export_deck_revision": state.export_deck_revision,
        "current_plan_revision": state.plan_revision,
        "current_deck_revision": sum(slide.revision for slide in state.slides.values()),
    }


_HOOKS_MODE_LABEL = {
    "shadow": "Advisory",
    "enforce": "Enforced",
}


def hooks_mode_view(state: BuildState) -> dict[str, object]:
    return {"mode": state.hooks_mode, "label": _HOOKS_MODE_LABEL.get(state.hooks_mode, state.hooks_mode)}


def project_view(state: BuildState) -> dict[str, object]:
    return {
        "phase": state.phase,
        "route": state.route,
        "plan_revision": state.plan_revision,
        "deck_revision": sum(slide.revision for slide in state.slides.values()),
        "hooks": hooks_mode_view(state),
    }
