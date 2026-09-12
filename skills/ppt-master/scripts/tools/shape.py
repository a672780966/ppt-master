#!/usr/bin/env python3
"""
PPT Master - Semantic Tool: shape.create

Wraps pptx_to_svg.preset_authoring.render_preset_shape_fragment. The agent
supplies preset + frame + fill + stroke + adjustments; it never names
preset_shape_svg.py, writes data-pptx-authoring metadata by hand, or reads
the registry itself.

Dependencies:
    None beyond existing sibling PPT Master modules
"""

from __future__ import annotations

from pptx_to_svg.preset_authoring import render_preset_shape_fragment

from .errors import ToolError

_ERROR_CODE_BY_MESSAGE_PREFIX = (
    ("Unknown DrawingML preset shape", "UNKNOWN_PRESET"),
    ("Invalid SVG element id", "INVALID_ID"),
    ("object_kind must be", "INVALID_OBJECT_KIND"),
    ("requires object_kind=", "INVALID_OBJECT_KIND"),
    ("Authored connector requires a connector preset", "INVALID_OBJECT_KIND"),
)


def _classify(message: str) -> str:
    for prefix, code in _ERROR_CODE_BY_MESSAGE_PREFIX:
        if message.startswith(prefix):
            return code
    return "INVALID_SHAPE_INPUT"


def _frame_tuple(frame: dict[str, object]) -> tuple[float, float, float, float]:
    try:
        return (
            float(frame["x"]),
            float(frame["y"]),
            float(frame["width"]),
            float(frame["height"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(
            "INVALID_FRAME",
            f"frame must have numeric x/y/width/height: {exc}",
        ) from exc


def shape_create(payload: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """Render one native preset shape fragment. Never writes any file.

    Returns (fields, warnings); raises ToolError on invalid input.
    """
    element_id = payload.get("id")
    preset = payload.get("preset")
    if not isinstance(element_id, str) or not element_id:
        raise ToolError("INVALID_ID", "id must be a non-empty string")
    if not isinstance(preset, str) or not preset:
        raise ToolError("UNKNOWN_PRESET", "preset must be a non-empty string")

    frame_raw = payload.get("frame")
    if not isinstance(frame_raw, dict):
        raise ToolError("INVALID_FRAME", "frame must be an object with x/y/width/height")
    frame = _frame_tuple(frame_raw)

    style: dict[str, str] = {
        "fill": str(payload.get("fill", "none")),
        "stroke": str(payload.get("stroke", "none")),
    }
    if style["stroke"] != "none":
        style["stroke-width"] = str(payload.get("stroke_width", 1.0))
    fill_opacity = payload.get("fill_opacity")
    if fill_opacity is not None:
        style["fill-opacity"] = str(fill_opacity)

    adjustments = payload.get("adjustments")
    if adjustments is not None and not isinstance(adjustments, dict):
        raise ToolError("INVALID_SHAPE_INPUT", "adjustments must be an object")

    object_kind = payload.get("object_kind", "shape")
    filter_id = payload.get("filter_id")

    try:
        fragment = render_preset_shape_fragment(
            preset,
            frame,
            adjustments=adjustments,
            object_kind=object_kind,
            element_id=element_id,
            style=style,
            filter_id=filter_id,
        )
    except ValueError as exc:
        raise ToolError(_classify(str(exc)), str(exc)) from exc

    return {
        "artifact_id": f"shape:{element_id}",
        "native_type": "shape",
        "svg_fragment": fragment,
    }, []
