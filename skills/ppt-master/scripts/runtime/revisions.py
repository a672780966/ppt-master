#!/usr/bin/env python3
"""
PPT Master - Slide Revision Control (P1)

expected_revision compare-and-swap for one slide's build-state entry. This is
the semantic-staleness half of the dual concurrency mechanism: the physical
critical section is runtime.build_state.locked_state's file lock; this module
rejects a write whose expected_revision no longer matches the on-disk
revision, so an agent's stale in-flight edit can never silently clobber a
user's just-made Live Preview direct edit.

Usage:
    Imported by scripts/build_state.py.

Examples:
    from runtime.revisions import submit_slide

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from runtime.build_state import (
    SLIDE_ID_RE,
    SLIDE_STATUSES,
    BuildState,
    BuildStateError,
    SlideState,
    StaleEditError,
    locked_state,
    utc_timestamp,
)


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def submit_slide(
    project_path: Path,
    slide_id: str,
    *,
    expected_revision: int,
    status: str | None = None,
    content_hash: str | None = None,
    from_file: Path | None = None,
    dirty: bool | None = None,
) -> SlideState:
    """Apply one slide mutation under CAS; raise StaleEditError on mismatch.

    On a match: revision += 1, status/content_hash/dirty updated, the
    slide's plan_revision stamped to the state's current plan revision,
    export.dirty forced true, and the write saved atomically. On a
    mismatch: zero writes -- the file is left byte-for-byte unchanged.

    --from-file also records svg_path (relative to project_path when
    possible) -- the one fact the P4 Resume Hook needs to re-hash the file
    on disk and detect drift; content_hash alone has nothing to re-hash.
    """
    if not SLIDE_ID_RE.match(slide_id):
        raise BuildStateError(f"invalid slide id: {slide_id!r}")
    if status is not None and status not in SLIDE_STATUSES:
        raise BuildStateError(f"invalid status: {status!r}")
    if from_file is not None and content_hash is not None:
        raise BuildStateError("pass only one of --from-file or --content-hash")

    resolved_hash = content_hash
    resolved_svg_path: str | None = None
    if from_file is not None:
        resolved_hash = sha256_of_file(from_file)
        try:
            resolved_svg_path = Path(from_file).resolve().relative_to(Path(project_path).resolve()).as_posix()
        except ValueError:
            resolved_svg_path = Path(from_file).as_posix()

    with locked_state(project_path) as state:
        slide = state.slides.get(slide_id)
        actual_revision = slide.revision if slide is not None else 0
        if actual_revision != expected_revision:
            # Raise before any mutation of `state` -- locked_state only saves
            # on a clean (non-raising) exit, so the file stays untouched.
            raise StaleEditError(slide_id, expected_revision, actual_revision)

        if slide is None:
            slide = SlideState(plan_revision=state.plan_revision)
            state.slides[slide_id] = slide

        slide.revision += 1
        slide.plan_revision = state.plan_revision
        if status is not None:
            slide.status = status
        if resolved_hash is not None:
            slide.content_hash = resolved_hash
        if resolved_svg_path is not None:
            slide.svg_path = resolved_svg_path
        slide.dirty = dirty if dirty is not None else (status != "ready")
        slide.updated_at = utc_timestamp()
        state.export_dirty = True
        return slide


def bump_plan_revision(project_path: Path, *, note: str | None = None) -> list[str]:
    """Increment plan.revision; cascade ready/dirty/building slides to stale.

    A `planned` slide (never built against the old plan) is left alone; an
    already-`stale` slide is idempotently left `stale`. Returns the affected
    slide ids.
    """
    affected: list[str] = []
    with locked_state(project_path) as state:
        state.plan_revision += 1
        state.plan_updated_at = utc_timestamp()
        for slide_id, slide in state.slides.items():
            if slide.plan_revision == state.plan_revision:
                continue
            if slide.status in ("ready", "dirty", "building"):
                slide.status = "stale"
                slide.dirty = True
                slide.updated_at = utc_timestamp()
                affected.append(slide_id)
        if affected:
            state.export_dirty = True
    return affected
