#!/usr/bin/env python3
"""
PPT Master - PostToolUse Rules: slide.validate binding + *.create contract (P4)

Two unrelated PostToolUse rules that happen to share a module because both
are "after a Semantic Tool succeeds, record or check something deterministic,
never call a model, never mutate a page SVG":

- slide.validate: bind the check to the slide's *current* revision
  (validated_revision) so a later edit that bumps revision without a fresh
  validate is visible as staleness by comparison alone -- no separate
  "is this stale" inference logic anywhere else.
- shape.create / formula.create / chart.create / table.create: verify the
  tool's own result-contract fields are present. These four tools have no
  project of their own (they return a fragment, never write a page file --
  references/artifact-ownership.md), so this check is entirely local: no
  filesystem, no build_state.json.

Usage:
    Imported by scripts/runtime/hook_registry.py.

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

from pathlib import Path

from runtime.build_state import load, locked_state, utc_timestamp
from runtime.hook_types import HookResult, allow, warn
from runtime.slide_id import slide_id_from_page

_CREATE_RESULT_FIELDS = {
    "shape.create": ("artifact_id", "native_type", "svg_fragment"),
    "formula.create": ("artifact_id", "native_type", "svg_fragment"),
    "chart.create": ("artifact_id", "native_type", "svg_fragment"),
    "table.create": ("artifact_id", "native_type", "svg_fragment"),
}


def post_slide_validate(
    payload: dict[str, object],
    fields: dict[str, object],
    warnings: list[str] | None = None,
) -> HookResult:
    project = payload.get("project")
    if not isinstance(project, str) or not project:
        return allow("PostToolUse", "slide.validate")
    project_path = Path(project)
    state = load(project_path)
    if state is None:
        return allow("PostToolUse", "slide.validate")

    ok = bool(fields.get("ok"))
    status = "passed" if ok else "failed"
    if ok and warnings:
        status = "passed-with-warnings"

    page = payload.get("page")
    slide_ids: list[str]
    if isinstance(page, str) and page:
        slide_id = slide_id_from_page(page)
        slide_ids = [slide_id] if slide_id else []
    else:
        slide_ids = list(state.slides)

    if not slide_ids:
        return allow("PostToolUse", "slide.validate")

    bound: list[str] = []
    with locked_state(project_path, create_if_missing=False) as locked:
        for slide_id in slide_ids:
            slide = locked.slides.get(slide_id)
            if slide is None:
                continue
            slide.validated_revision = slide.revision
            slide.validation_status = status
            slide.updated_at = utc_timestamp()
            bound.append(slide_id)

    if not bound:
        return allow("PostToolUse", "slide.validate")
    # A record, not a checkpoint: PostToolUse(slide.validate) never blocks or
    # even warns on a failing slide -- a blocking result already surfaces
    # through slide.validate's own {ok, errors} envelope; this hook's only
    # job is the deterministic revision-binding transition itself.
    return HookResult(
        hook="PostToolUse", tool="slide.validate", decision="allow",
        details={"bound_slides": bound, "validation_status": status},
    )


def post_create_result(tool: str, fields: dict[str, object]) -> HookResult:
    required = _CREATE_RESULT_FIELDS.get(tool)
    if required is None:
        return allow("PostToolUse", tool)
    missing = [name for name in required if not fields.get(name)]
    if missing:
        return warn(
            "PostToolUse", tool, "RESULT_CONTRACT_INCOMPLETE",
            f"{tool} result is missing expected field(s): {missing}",
            missing=missing,
        )
    return allow("PostToolUse", tool)
