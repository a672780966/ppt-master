#!/usr/bin/env python3
"""
PPT Master - Build Phase / Gate / Export Transitions (P1)

Small state transitions layered on runtime.build_state: project phase,
validation-gate mirror, and export readiness. Phase transitions are the seam
where a future P4 Lifecycle Hooks layer would add pre/post-transition
callbacks; nothing here builds a callback mechanism.

Usage:
    Imported by scripts/build_state.py.

Examples:
    from runtime.controller import set_phase, set_gate, set_export

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from runtime.build_state import (
    EARLY_GATE_STATUSES,
    GATE_STATUSES,
    HOOKS_MODES,
    PHASES,
    ROUTES,
    BuildStateError,
    locked_state,
    utc_timestamp,
)


def init_state(
    project_path: Path,
    *,
    route: str,
    pages: list[str] | None = None,
) -> None:
    """Create build_state.json for a project. Refuses to overwrite one."""
    from runtime.build_state import BuildState, SlideState, save, state_path

    if route not in ROUTES:
        raise BuildStateError(f"invalid route: {route!r}")
    path = state_path(project_path)
    if path.is_file():
        raise BuildStateError(f"{path} already exists; build_state.py never overwrites it")
    state = BuildState(route=route, project_updated_at=utc_timestamp())
    for page in pages or []:
        state.slides[page] = SlideState(plan_revision=state.plan_revision)
    save(project_path, state)


def set_phase(project_path: Path, phase: str) -> None:
    if phase not in PHASES:
        raise BuildStateError(f"invalid phase: {phase!r}")
    with locked_state(project_path, create_if_missing=False) as state:
        state.phase = phase
        state.project_updated_at = utc_timestamp()


def set_gate(
    project_path: Path,
    gate: str,
    status: str,
    *,
    report_path: Path | None = None,
) -> None:
    if gate not in ("early", "final"):
        raise BuildStateError(f"invalid gate: {gate!r}")
    allowed = EARLY_GATE_STATUSES if gate == "early" else GATE_STATUSES
    if status not in allowed:
        raise BuildStateError(f"invalid status {status!r} for gate {gate!r}")
    report_sha256 = None
    if report_path is not None:
        digest = hashlib.sha256()
        with Path(report_path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        report_sha256 = digest.hexdigest()
    with locked_state(project_path, create_if_missing=False) as state:
        if gate == "early":
            state.early_gate = status
        else:
            state.final_gate = status
            if report_sha256 is not None:
                state.final_gate_report_sha256 = report_sha256


def set_export(
    project_path: Path,
    *,
    dirty: bool,
    last_export: str | None = None,
) -> None:
    with locked_state(project_path, create_if_missing=False) as state:
        state.export_dirty = dirty
        if last_export is not None:
            state.last_export = last_export
        if not dirty:
            state.export_plan_revision = state.plan_revision
            state.export_deck_revision = sum(slide.revision for slide in state.slides.values())


def set_hooks_mode(project_path: Path, mode: str) -> None:
    if mode not in HOOKS_MODES:
        raise BuildStateError(f"invalid hooks mode: {mode!r}")
    with locked_state(project_path, create_if_missing=False) as state:
        state.hooks_mode = mode


_UNSET = object()


def set_ai_edit_status(
    project_path: Path,
    slide_id: str,
    *,
    active_job: str | None = _UNSET,
    last_job: str | None = _UNSET,
) -> None:
    """P6: record which AI Edit job (if any) currently owns a slide.

    Never writes AI Edit job content itself -- only the two small
    pointers runtime/ai_edits/<job_id>/ needs a caller to find (SS23).
    """
    with locked_state(project_path, create_if_missing=False) as state:
        slide = state.slides.get(slide_id)
        if slide is None:
            raise BuildStateError(f"unknown slide id: {slide_id!r}")
        if active_job is not _UNSET:
            slide.ai_edit_active_job = active_job
        if last_job is not _UNSET:
            slide.ai_edit_last_job = last_job
