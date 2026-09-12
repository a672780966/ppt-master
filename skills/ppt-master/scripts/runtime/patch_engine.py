#!/usr/bin/env python3
"""
PPT Master - Deterministic Patch Engine (P6)

The one place an AI edit actually reaches disk. Given an already-validated
EditPlan (runtime.edit_plan.validate_edit_plan), applies every operation to
a deep-copied working tree, enforces the Native Object Integrity Gate,
round-trips the result through a structural well-formedness check, snapshots
before.svg, writes atomically, then reuses the exact same P4/P5 mutation
path every other direct edit uses -- record_direct_edit() (revision++,
dirty, export.dirty) followed by tools.dispatch.invoke("slide.validate",
...) (P4's PostToolUse binds validated_revision automatically). No new
revision or validation logic lives here.

Hard rule: an AI edit never touches a native chart/table/formula/shape
group except through the matching semantic_tool operation targeted at that
native object's own root id -- the Native Object Integrity Gate rejects any
other operation type, mismatched semantic tool, or descendant-targeted
native replacement with zero writes.

Usage:
    from runtime.patch_engine import apply_edit_plan, PatchEngineError
    result = apply_edit_plan(project_path, slide_id, operations,
                              scope=..., before_svg_path=...)

Dependencies:
    None beyond existing sibling PPT Master modules
"""

from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from runtime.build_state import load as load_build_state
from runtime.svg_tree import find_by_id, find_with_parent
from svg_editor.annotations import is_editable_attr, set_attributes, set_text
from svg_to_pptx.native_objects.marker_attributes import native_replacement_kind
from tools.dispatch import invoke as dispatch_invoke
from workbench.edit_wiring import record_direct_edit

_NATIVE_MARKER_ATTRS = (
    "data-pptx-replace-with", "data-pptx-native",
    "data-pptx-authoring", "data-pptx-object", "data-pptx-prst",
)
_BOUNDS_ATTRS = ("data-pptx-bounds", "data-pptx-frame")
_COMPANION_BOUNDS_ATTRS = ("data-pptx-x", "data-pptx-y", "data-pptx-width", "data-pptx-height")
_NATIVE_TOOL_BY_REPLACEMENT_KIND = {
    "chart": "chart.create",
    "table": "table.create",
    "formula": "formula.create",
}


class PatchEngineError(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass
class ApplyResult:
    slide_id: str
    new_revision: int
    validated_revision: int | None
    validation_status: str | None
    validate_envelope: dict[str, object]


def _local_tag(elem: ET.Element) -> str:
    return elem.tag.split("}", 1)[1] if "}" in elem.tag else elem.tag


def _has_native_fingerprint(elem: ET.Element) -> bool:
    if any(elem.get(attr) for attr in _NATIVE_MARKER_ATTRS):
        return True
    return any(_local_tag(child) == "metadata" for child in elem)


def _expected_semantic_tool(elem: ET.Element) -> str | None:
    """Return the one authoring tool allowed to replace this native object.

    Charts/tables/formulas advertise their replacement kind directly.
    Authored preset shapes/connectors use the existing data-pptx-object /
    data-pptx-authoring / data-pptx-prst contract and must go through
    shape.create. An unknown native fingerprint has no safe matching tool.
    """
    replacement_kind = native_replacement_kind(elem)
    if replacement_kind:
        return _NATIVE_TOOL_BY_REPLACEMENT_KIND.get(replacement_kind)
    if (
        elem.get("data-pptx-object") in ("shape", "connector")
        or elem.get("data-pptx-authoring") == "preset"
        or elem.get("data-pptx-prst")
    ):
        return "shape.create"
    return None


def _build_parent_map(root: ET.Element) -> dict[int, ET.Element]:
    return {id(child): parent for parent in root.iter() for child in parent}


def _check_native_integrity(
    root: ET.Element,
    parent_map: dict[int, ET.Element],
    anchor_id: str,
    op_type: str,
    *,
    semantic_tool: str | None = None,
) -> None:
    elem = find_by_id(root, anchor_id)
    if elem is None:
        return
    node = elem
    while node is not None:
        if _has_native_fingerprint(node):
            if op_type != "semantic_tool":
                raise PatchEngineError(
                    "NATIVE_OBJECT_INTEGRITY_ERROR",
                    f"{anchor_id!r} is (or is inside) a native object; only a matching semantic_tool operation may modify it",
                    target=anchor_id,
                )
            if node is not elem:
                native_root = node.get("id")
                raise PatchEngineError(
                    "NATIVE_OBJECT_INTEGRITY_ERROR",
                    f"semantic_tool must target the native object root, not descendant {anchor_id!r}",
                    target=anchor_id,
                    native_root=native_root,
                )
            expected_tool = _expected_semantic_tool(node)
            if expected_tool is None or semantic_tool != expected_tool:
                raise PatchEngineError(
                    "NATIVE_OBJECT_INTEGRITY_ERROR",
                    f"{anchor_id!r} is a native object that requires {expected_tool or 'its matching semantic tool'}, got {semantic_tool!r}",
                    target=anchor_id,
                    expected_tool=expected_tool,
                    actual_tool=semantic_tool,
                )
            return
        node = parent_map.get(id(node))


def _read_bounds(elem: ET.Element) -> tuple[str, list[float]] | tuple[None, None]:
    for attr in _BOUNDS_ATTRS:
        raw = elem.get(attr)
        if raw:
            parts = raw.split()
            if len(parts) == 4:
                try:
                    return attr, [float(p) for p in parts]
                except ValueError:
                    continue
    return None, None


def _write_bounds(elem: ET.Element, attr: str, x: float, y: float, w: float, h: float) -> None:
    elem.set(attr, f"{x:g} {y:g} {w:g} {h:g}")
    if elem.get("data-pptx-x") is not None:
        elem.set("data-pptx-x", f"{x:g}")
        elem.set("data-pptx-y", f"{y:g}")
        elem.set("data-pptx-width", f"{w:g}")
        elem.set("data-pptx-height", f"{h:g}")


def _apply_translate(root: ET.Element, target: str, dx: float, dy: float) -> None:
    elem = find_by_id(root, target)
    attr, bounds = _read_bounds(elem)
    if attr:
        x, y, w, h = bounds
        _write_bounds(elem, attr, x + dx, y + dy, w, h)
        return
    for ax, ay in (("x", "y"), ("cx", "cy")):
        if elem.get(ax) is not None:
            elem.set(ax, f"{float(elem.get(ax)) + dx:g}")
            elem.set(ay, f"{float(elem.get(ay)) + dy:g}")
            return
    existing = (elem.get("transform") or "").strip()
    elem.set("transform", f"translate({dx:g},{dy:g}) {existing}".strip())


def _apply_resize(root: ET.Element, target: str, width: float | None, height: float | None) -> None:
    elem = find_by_id(root, target)
    attr, bounds = _read_bounds(elem)
    if attr:
        x, y, w, h = bounds
        _write_bounds(elem, attr, x, y, width if width is not None else w, height if height is not None else h)
        return
    if elem.get("width") is not None or elem.get("height") is not None:
        if width is not None:
            elem.set("width", f"{width:g}")
        if height is not None:
            elem.set("height", f"{height:g}")
        return
    raise PatchEngineError("INVALID_EDIT_PLAN", f"{target!r} has no resizable geometry (no bounds marker or width/height attrs)", target=target)


def _apply_set_geometry(root: ET.Element, target: str, geometry: dict[str, object]) -> None:
    elem = find_by_id(root, target)
    attr, bounds = _read_bounds(elem)
    if attr:
        unsupported = sorted(set(geometry) - {"x", "y", "width", "height"})
        if unsupported:
            raise PatchEngineError(
                "INVALID_EDIT_PLAN",
                f"set_geometry on bounded object {target!r} only supports x/y/width/height; got {unsupported}",
                target=target,
            )
        x, y, w, h = bounds
        x = float(geometry.get("x", x))
        y = float(geometry.get("y", y))
        w = float(geometry.get("width", w))
        h = float(geometry.get("height", h))
        _write_bounds(elem, attr, x, y, w, h)
        return
    invalid_attrs = [key for key in geometry if not is_editable_attr(key)]
    if invalid_attrs:
        raise PatchEngineError(
            "INVALID_EDIT_PLAN",
            f"set_geometry on {target!r} contains protected attributes: {invalid_attrs}",
            target=target,
        )
    ok, reason = set_attributes(root, target, dict(geometry))
    if not ok:
        raise PatchEngineError("INVALID_EDIT_PLAN", f"set_geometry on {target!r} failed: {reason}", target=target)


def _splice_fragment(root: ET.Element, parent_map: dict[int, ET.Element], target: str, fragment_xml: str, *, force_id: str | None) -> None:
    try:
        new_elem = ET.fromstring(fragment_xml)
    except ET.ParseError as exc:
        raise PatchEngineError("INVALID_EDIT_PLAN", f"fragment is not well-formed XML: {exc}")
    if force_id:
        new_elem.set("id", force_id)
    old_elem, parent = find_with_parent(root, target)
    if old_elem is None or parent is None:
        raise PatchEngineError("TARGET_NOT_FOUND", f"{target!r} not found for replace_fragment", target=target)
    index = list(parent).index(old_elem)
    parent.remove(old_elem)
    parent.insert(index, new_elem)


def _apply_insert_fragment(root: ET.Element, parent_id: str, fragment_xml: str, index: int | None) -> None:
    try:
        new_elem = ET.fromstring(fragment_xml)
    except ET.ParseError as exc:
        raise PatchEngineError("INVALID_EDIT_PLAN", f"fragment is not well-formed XML: {exc}")
    parent = find_by_id(root, parent_id)
    if parent is None:
        raise PatchEngineError("TARGET_NOT_FOUND", f"{parent_id!r} not found for insert_fragment", target=parent_id)
    if index is None or index < 0 or index > len(parent):
        parent.append(new_elem)
    else:
        parent.insert(index, new_elem)


def _apply_delete_element(root: ET.Element, target: str) -> None:
    elem, parent = find_with_parent(root, target)
    if elem is None or parent is None:
        raise PatchEngineError("TARGET_NOT_FOUND", f"{target!r} not found for delete_element", target=target)
    parent.remove(elem)


def _all_ids(root: ET.Element) -> list[str]:
    return [elem.get("id") for elem in root.iter() if elem.get("id") is not None]


def apply_edit_plan(
    project_path: Path,
    slide_id: str,
    svg_path: Path,
    operations: list[dict[str, object]],
    *,
    before_svg_path: Path | None = None,
    clear_annotation_id: str | None = None,
) -> ApplyResult:
    """Apply an already-validated EditPlan's operations as one transaction.

    Callers must have already run runtime.edit_plan.validate_edit_plan and
    the caller's own revision/conflict check before this is invoked --
    this function trusts `operations` are in-scope and well-typed, and
    focuses purely on the mutation/integrity/atomicity/revision-bump steps.
    `before_svg_path`, when given, receives the pre-edit SVG content
    verbatim -- the Undo snapshot (SS32). `clear_annotation_id`, when
    given, clears that element's data-edit-target/data-edit-annotation
    attrs in the same write. This remains the documented P6 trade-off:
    cleanup is atomic with the patch and therefore precedes validation.
    """
    original_text = svg_path.read_text(encoding="utf-8")
    if before_svg_path is not None:
        before_svg_path.write_text(original_text, encoding="utf-8")
    root = ET.fromstring(original_text)
    working = deepcopy(root)
    parent_map = _build_parent_map(working)

    for op in operations:
        op_type = op["type"]
        anchor_id = op["parent"] if op_type == "insert_fragment" else op["target"]
        _check_native_integrity(
            working,
            parent_map,
            anchor_id,
            op_type,
            semantic_tool=op.get("tool") if op_type == "semantic_tool" else None,
        )

        if op_type == "set_text":
            ok, reason = set_text(working, op["target"], op["value"])
            if not ok:
                raise PatchEngineError("INVALID_EDIT_PLAN", f"set_text on {op['target']!r} failed: {reason}", target=op["target"])
        elif op_type == "set_style":
            invalid_attrs = [key for key in op["style"] if not is_editable_attr(key)]
            if invalid_attrs:
                raise PatchEngineError(
                    "INVALID_EDIT_PLAN",
                    f"set_style on {op['target']!r} contains protected attributes: {invalid_attrs}",
                    target=op["target"],
                )
            style = dict(op["style"])
            ok, reason = set_attributes(working, op["target"], style)
            if not ok:
                raise PatchEngineError("INVALID_EDIT_PLAN", f"set_style on {op['target']!r} failed: {reason}", target=op["target"])
        elif op_type == "set_geometry":
            _apply_set_geometry(working, op["target"], op["geometry"])
        elif op_type == "translate":
            _apply_translate(working, op["target"], float(op["dx"]), float(op["dy"]))
        elif op_type == "resize":
            _apply_resize(working, op["target"], op.get("width"), op.get("height"))
        elif op_type == "replace_fragment":
            _splice_fragment(working, parent_map, op["target"], op["fragment"], force_id=op["target"])
            parent_map = _build_parent_map(working)
        elif op_type == "insert_fragment":
            _apply_insert_fragment(working, op["parent"], op["fragment"], op.get("index"))
            parent_map = _build_parent_map(working)
        elif op_type == "delete_element":
            _apply_delete_element(working, op["target"])
            parent_map = _build_parent_map(working)
        elif op_type == "semantic_tool":
            target_elem = find_by_id(working, op["target"])
            arguments = dict(op["arguments"])
            if "frame" not in arguments and target_elem is not None:
                _, bounds = _read_bounds(target_elem)
                if bounds:
                    x, y, w, h = bounds
                    arguments["frame"] = {"x": x, "y": y, "width": w, "height": h}
            arguments["id"] = op["target"]
            envelope, _exit_code = dispatch_invoke(op["tool"], arguments)
            if not envelope.get("ok"):
                raise PatchEngineError(
                    "INVALID_EDIT_PLAN",
                    f"semantic_tool {op['tool']!r} failed: {envelope.get('errors')}",
                    target=op["target"], tool=op["tool"],
                )
            _splice_fragment(working, parent_map, op["target"], envelope["svg_fragment"], force_id=op["target"])
            parent_map = _build_parent_map(working)

    if clear_annotation_id:
        # Deliberately bypasses set_attributes()/is_editable_attr(): those
        # exist to protect data-edit-* from *user* edits, not from this
        # sanctioned annotation-resolution cleanup in the atomic patch.
        annotated_elem = find_by_id(working, clear_annotation_id)
        if annotated_elem is not None:
            annotated_elem.attrib.pop("data-edit-target", None)
            annotated_elem.attrib.pop("data-edit-annotation", None)

    # Structural validation: well-formed XML + no duplicate ids introduced.
    serialized = ET.tostring(working, encoding="unicode")
    try:
        ET.fromstring(serialized)
    except ET.ParseError as exc:
        raise PatchEngineError("INVALID_EDIT_PLAN", f"resulting SVG is not well-formed: {exc}")
    ids = _all_ids(working)
    if len(ids) != len(set(ids)):
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        raise PatchEngineError("INVALID_EDIT_PLAN", f"resulting SVG has duplicate ids: {duplicates}")

    # Atomic write: temp file + os.replace, so a crash mid-write never
    # leaves a half-written SVG on disk (SS57).
    fd, tmp_path = tempfile.mkstemp(dir=str(svg_path.parent), suffix=".svg.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
        os.replace(tmp_path, svg_path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    new_slide = record_direct_edit(project_path, svg_path, status="dirty")
    new_revision = new_slide.revision if new_slide is not None else load_build_state(project_path).slides[slide_id].revision

    page = svg_path.name
    validate_envelope, _exit_code = dispatch_invoke("slide.validate", {"project": str(project_path), "page": page})
    state_after = load_build_state(project_path)
    slide_after = state_after.slides.get(slide_id) if state_after else None

    return ApplyResult(
        slide_id=slide_id,
        new_revision=new_revision,
        validated_revision=slide_after.validated_revision if slide_after else None,
        validation_status=slide_after.validation_status if slide_after else None,
        validate_envelope=validate_envelope,
    )


def restore_before_svg(project_path: Path, slide_id: str, svg_path: Path, before_svg_path: Path) -> int:
    """Undo: atomically restore svg_path from a job's before.svg snapshot,
    then bump revision *forward* through the same record_direct_edit +
    slide.validate path apply_edit_plan uses -- revision is monotonic and
    never decrements back to the pre-edit number (SS34). Callers must have
    already run edit_conflicts.check_undo_conflict before calling this.
    """
    content = before_svg_path.read_text(encoding="utf-8")
    fd, tmp_path = tempfile.mkstemp(dir=str(svg_path.parent), suffix=".svg.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, svg_path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    new_slide = record_direct_edit(project_path, svg_path, status="dirty")
    new_revision = new_slide.revision if new_slide is not None else load_build_state(project_path).slides[slide_id].revision
    dispatch_invoke("slide.validate", {"project": str(project_path), "page": svg_path.name})
    return new_revision
