#!/usr/bin/env python3
"""
PPT Master - Selection Context Compiler (P6)

Builds on top of P2's Context Compiler (project_management.page_context.
build_page_context) rather than duplicating it -- a second parallel page
context is explicitly forbidden by the P6 spec. This module only adds what
build_page_context does not have: a frozen selection snapshot, and one
Selected Object Facts record per selected id, read fresh from the current
on-disk SVG every time (never trusted from the browser -- the Workbench
only ever submits ids/instruction/scope/revision; see workbench/ai_edit_api.py).

Object kind/native-object classification reuses the exact marker
vocabulary already established by P3's Semantic Tools and svg_to_pptx's
native-object machinery -- no new attribute vocabulary:
  data-pptx-replace-with (chart|table|formula, incl. legacy alias)
    -> data-pptx-object (shape|connector)
    -> data-pptx-role / data-pptx-page-role (chrome kinds)
    -> tag-based fallback (image/text/group)

Usage:
    from runtime.ai_edit_context import build_selection_context
    ctx = build_selection_context(project_path, slide_id="P05", scope="selection",
                                   selection_ids=["title-01"], instruction="...")

Dependencies:
    None beyond existing sibling PPT Master modules
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from project_management.page_context import PageContextError, build_page_context
from runtime.svg_tree import find_by_id, find_with_parent
from svg_to_pptx.native_objects.marker_attributes import native_replacement_kind

_STYLE_ATTRS = ("fill", "stroke", "font-size", "font-weight", "font-family", "opacity", "text-anchor")

# Non-essential neighbor facts trimmed first under context-budget pressure
# (SS50) -- a selected object's own facts are never dropped, only this.
_MAX_CONTEXT_CHARS = 60_000


class SelectionContextError(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _local_tag(elem: ET.Element) -> str:
    return elem.tag.split("}", 1)[1] if "}" in elem.tag else elem.tag


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _svg_path_for_slide(project_path: Path, slide_id: str) -> Path | None:
    svg_dir = project_path / "svg_output"
    if not svg_dir.is_dir():
        return None
    digits = slide_id[1:]
    matches = sorted(svg_dir.glob(f"{digits}_*.svg")) or sorted(svg_dir.glob(f"0{digits}_*.svg"))
    return matches[0] if matches else None


def object_kind(elem: ET.Element) -> str:
    """"text" | "shape" | "image" | "chart" | "table" | "formula" | "group" | "unknown" (SS8)."""
    native = native_replacement_kind(elem)
    if native:
        return native
    if elem.get("data-pptx-object") in ("shape", "connector"):
        return "shape"
    role = elem.get("data-pptx-role") or elem.get("data-pptx-page-role")
    if role:
        return role
    tag = _local_tag(elem)
    if tag == "image":
        return "image"
    if tag == "text":
        return "text"
    if tag == "g":
        children = list(elem)
        if children and all(_local_tag(c) in ("text", "tspan") for c in children):
            return "text"
        return "group"
    return "unknown"


def _bounds(elem: ET.Element) -> dict[str, float] | None:
    raw = elem.get("data-pptx-bounds") or elem.get("data-pptx-frame")
    if raw:
        parts = raw.split()
        if len(parts) == 4:
            try:
                x, y, w, h = (float(p) for p in parts)
                return {"x": x, "y": y, "width": w, "height": h}
            except ValueError:
                pass
    geometry_attrs = ("x", "y", "width", "height", "cx", "cy", "r")
    found = {k: elem.get(k) for k in geometry_attrs if elem.get(k) is not None}
    if not found:
        return None
    out: dict[str, float] = {}
    for key, value in found.items():
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            continue
    return out or None


def _text_of(elem: ET.Element) -> str | None:
    parts: list[str] = []
    if elem.text and elem.text.strip():
        parts.append(elem.text.strip())
    for child in elem:
        if _local_tag(child) == "tspan" and child.text and child.text.strip():
            parts.append(child.text.strip())
    return " ".join(parts) if parts else None


def _style_of(elem: ET.Element) -> dict[str, str]:
    return {k: elem.get(k) for k in _STYLE_ATTRS if elem.get(k) is not None}


def selected_object_facts(root: ET.Element, element_id: str) -> dict[str, object]:
    elem, parent = find_with_parent(root, element_id)
    if elem is None:
        elem = find_by_id(root, element_id)  # covers the root's direct children edge case
        parent = None
    if elem is None:
        raise SelectionContextError("SELECTION_NO_LONGER_EXISTS", f"selection id not found: {element_id}", id=element_id)
    native_kind = native_replacement_kind(elem)
    return {
        "id": element_id,
        "kind": object_kind(elem),
        "text": _text_of(elem),
        "bounds": _bounds(elem),
        "style": _style_of(elem),
        "parent_id": parent.get("id") if parent is not None else None,
        "semantic_role": elem.get("data-pptx-role") or elem.get("data-pptx-page-role"),
        "native_object_type": native_kind or (elem.get("data-pptx-object") or None),
    }


@dataclass
class SelectionContext:
    slide_id: str
    expected_revision: int
    plan_revision: int
    scope: str
    selection_ids: list[str]
    instruction: str
    origin: str
    created_at: str
    page_context: dict[str, object] | None
    object_facts: list[dict[str, object]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "slide_id": self.slide_id,
            "expected_revision": self.expected_revision,
            "plan_revision": self.plan_revision,
            "scope": self.scope,
            "selection_ids": self.selection_ids,
            "instruction": self.instruction,
            "origin": self.origin,
            "created_at": self.created_at,
            "page_context": self.page_context,
            "object_facts": self.object_facts,
            "warnings": self.warnings,
        }


def build_selection_context(
    project_path: Path,
    *,
    slide_id: str,
    expected_revision: int,
    plan_revision: int,
    scope: str,
    selection_ids: list[str],
    instruction: str,
    origin: str = "inline",
) -> SelectionContext:
    project_path = Path(project_path)
    warnings: list[str] = []

    page_context: dict[str, object] | None = None
    try:
        result = build_page_context(project_path, slide_id)
        page_context = result.context
    except PageContextError as exc:
        # Quick route (and any project with no design_spec.md/spec_lock.md
        # yet) has no P2 lock to project -- fall back to selection/object
        # facts alone rather than failing the whole compile (SS7).
        warnings.append(f"page_context_unavailable: {exc}")

    svg_path = _svg_path_for_slide(project_path, slide_id)
    if svg_path is None or not svg_path.is_file():
        raise SelectionContextError("TARGET_NOT_FOUND", f"no svg_output file found for {slide_id}", slide=slide_id)
    root = ET.parse(str(svg_path)).getroot()

    object_facts: list[dict[str, object]] = []
    if scope != "slide":
        for element_id in selection_ids:
            object_facts.append(selected_object_facts(root, element_id))

    context = SelectionContext(
        slide_id=slide_id,
        expected_revision=expected_revision,
        plan_revision=plan_revision,
        scope=scope,
        selection_ids=list(selection_ids),
        instruction=instruction,
        origin=origin,
        created_at=_utc_timestamp(),
        page_context=page_context,
        object_facts=object_facts,
        warnings=warnings,
    )

    payload = context.to_dict()
    size = len(str(payload))
    if size > _MAX_CONTEXT_CHARS and page_context is not None:
        # Trim non-essential neighbor facts first -- object_facts (the
        # selection itself) are never dropped (SS50).
        trimmed = dict(page_context)
        trimmed.pop("reference_set", None)
        context.page_context = trimmed
        context.warnings.append("page_context_trimmed_for_budget")
        payload = context.to_dict()
        if len(str(payload)) > _MAX_CONTEXT_CHARS:
            raise SelectionContextError("AI_EDIT_CONTEXT_TOO_LARGE", "compiled context exceeds budget even after trimming")

    return context
