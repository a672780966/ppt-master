#!/usr/bin/env python3
"""
PPT Master - Semantic Tool: table.create

Same situation as chart.create: scripts/semantic_table.py only
compacts/expands the ppt-master.semantic-table.v2 JSON payload — it has no
SVG grid renderer. This module renders the visible grid (header/body rects,
divider lines, cell text) plus the exact schema payload from
native-data-interface.md §"Table schema", then self-stamps the fallback
fingerprint the same way chart.create does.

The agent supplies columns + rows + frame + style; it never writes a
data-pptx-replace-with="table" marker or a ppt-master.semantic-table.v2
payload by hand.

Dependencies:
    None beyond existing sibling PPT Master modules
"""

from __future__ import annotations

import json
from xml.etree import ElementTree as ET

from pptx_shapes import NATIVE_FALLBACK_SHA256_ATTR
from svg_to_pptx.native_objects.fallback_hash import stamp_native_fallback_baseline

from .errors import ToolError

SEMANTIC_TABLE_SCHEMA = "ppt-master.semantic-table.v2"
_ALIGN_ANCHOR = {"l": "start", "ctr": "middle", "r": "end"}


def _esc(value: object) -> str:
    text = str(value)
    return (
        text.replace("&", "&amp;").replace('"', "&quot;")
        .replace("<", "&lt;").replace(">", "&gt;")
    )


def _cell_text(cell: object) -> str:
    if isinstance(cell, dict):
        return str(cell.get("text", ""))
    return str(cell)


def _cell_align(cell: object, default: str) -> str:
    if isinstance(cell, dict):
        return str(cell.get("align", default))
    return default


def table_create(payload: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """Render one native-ready table marker (visible grid + JSON metadata).

    Never writes any file; the returned fragment is self-contained and its
    data-pptx-fallback-sha256 is already stamped.
    """
    element_id = payload.get("id")
    if not isinstance(element_id, str) or not element_id:
        raise ToolError("INVALID_ID", "id must be a non-empty string")

    frame = payload.get("frame")
    if not isinstance(frame, dict):
        raise ToolError("INVALID_FRAME", "frame must be an object with x/y/width/height")
    try:
        x, y, width, height = (
            float(frame["x"]), float(frame["y"]),
            float(frame["width"]), float(frame["height"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("INVALID_FRAME", f"frame must have numeric x/y/width/height: {exc}") from exc
    if width <= 0 or height <= 0:
        raise ToolError("INVALID_FRAME", "frame width/height must be positive")

    columns = payload.get("columns")
    rows = payload.get("rows")
    if columns is not None and not isinstance(columns, list):
        raise ToolError("INVALID_DATA", "columns must be an array of header cells")
    if not isinstance(rows, list) or not rows:
        raise ToolError("INVALID_DATA", "rows must be a non-empty array of row arrays")
    n_cols = len(columns) if columns else len(rows[0])
    if n_cols == 0:
        raise ToolError("INVALID_DATA", "table needs at least one column")
    for row in rows:
        if not isinstance(row, list) or len(row) != n_cols:
            raise ToolError("INVALID_DATA", f"every row must have {n_cols} cells")

    style = payload.get("style") or {}
    if not isinstance(style, dict):
        raise ToolError("INVALID_STYLE", "style must be an object")
    header_fill = str(style.get("header_fill", "#1E5FFF"))
    header_text = str(style.get("header_text", "#FFFFFF"))
    body_fill = str(style.get("body_fill", "#FFFFFF"))
    band_fill = str(style.get("band_fill", "#F5F5F7"))
    body_text = str(style.get("body_text", "#1D1D1F"))
    border_color = str(style.get("border_color", "#D8D8DC"))
    font_size = float(style.get("font_size", 16))
    header_font_size = float(style.get("header_font_size", font_size))
    padding = float(style.get("padding", 8))

    n_rows = len(rows)
    header_height = header_font_size + padding * 2 if columns else 0
    body_row_height = (height - header_height) / n_rows
    col_width = width / n_cols

    parts: list[str] = []
    row_top = y
    row_fills: list[str] = []
    if columns:
        parts.append(f'<rect x="{x:g}" y="{row_top:g}" width="{width:g}" height="{header_height:g}" fill="{_esc(header_fill)}"/>')
        for c in range(n_cols):
            cx = x + c * col_width + col_width / 2
            cy = row_top + header_height / 2 + header_font_size * 0.35
            parts.append(f'<text x="{cx:g}" y="{cy:g}" text-anchor="middle" font-size="{header_font_size:g}" font-weight="bold" fill="{_esc(header_text)}">{_esc(_cell_text(columns[c]))}</text>')
        row_top += header_height

    for r, row in enumerate(rows):
        fill = body_fill if r % 2 == 0 else band_fill
        row_fills.append(fill)
        parts.append(f'<rect x="{x:g}" y="{row_top:g}" width="{width:g}" height="{body_row_height:g}" fill="{_esc(fill)}"/>')
        for c, cell in enumerate(row):
            align = _cell_align(cell, "l")
            anchor = _ALIGN_ANCHOR.get(align, "start")
            cx = {"start": x + c * col_width + padding, "middle": x + c * col_width + col_width / 2, "end": x + (c + 1) * col_width - padding}[anchor]
            cy = row_top + body_row_height / 2 + font_size * 0.35
            parts.append(f'<text x="{cx:g}" y="{cy:g}" text-anchor="{anchor}" font-size="{font_size:g}" fill="{_esc(body_text)}">{_esc(_cell_text(cell))}</text>')
        row_top += body_row_height

    # Full grid, including the outer perimeter: every cell needs all four
    # cardinal borders drawn so a uniform border_color/border_width payload
    # is a faithful projection of what the fallback actually shows (an
    # internal-dividers-only grid left the perimeter cells short two edges
    # each, which the checker's SVG-first parity check correctly rejected).
    for c in range(n_cols + 1):
        line_x = x + c * col_width
        parts.append(f'<line x1="{line_x:g}" y1="{y:g}" x2="{line_x:g}" y2="{y + height:g}" stroke="{_esc(border_color)}" stroke-width="1"/>')
    line_y_positions = [y]
    if columns:
        line_y_positions.append(y + header_height)
    for r in range(n_rows):
        line_y_positions.append(line_y_positions[-1] + body_row_height)
    for line_y in line_y_positions:
        parts.append(f'<line x1="{x:g}" y1="{line_y:g}" x2="{x + width:g}" y2="{line_y:g}" stroke="{_esc(border_color)}" stroke-width="1"/>')

    # Explicit per-cell fill/color/bold rather than relying on native
    # export's own style.band_row banding to happen to reproduce this exact
    # alternation -- the checker's SVG-first parity check compares against
    # the literal drawn fill, and a mismatched banding convention (which
    # row native banding treats as "odd") is exactly the kind of thing that
    # would otherwise fail export.
    payload_columns = None
    if columns:
        payload_columns = [
            {"text": _cell_text(c), "bold": True, "fill": header_fill, "color": header_text}
            for c in columns
        ]
    payload_rows = [
        [
            {
                "text": _cell_text(cell),
                "fill": row_fills[r],
                "color": body_text,
                **({"align": _cell_align(cell, "l")} if _cell_align(cell, "l") != "l" else {}),
            }
            for cell in row
        ]
        for r, row in enumerate(rows)
    ]

    table_payload: dict[str, object] = {
        "schema": SEMANTIC_TABLE_SCHEMA,
        "name": element_id,
        "x": x, "y": y, "width": width, "height": height,
        "rows": payload_rows,
        "style": {
            "font_size": font_size,
            "header_font_size": header_font_size,
            "header_fill": header_fill,
            "header_text": header_text,
            "body_fill": body_fill,
            "band_fill": band_fill,
            "body_text": body_text,
            "border_color": border_color,
        },
    }
    if columns:
        table_payload["columns"] = [
            {"text": _cell_text(c), "bold": True} for c in columns
        ]
        table_payload["row_heights"] = [header_height] + [body_row_height] * n_rows
    else:
        table_payload["row_heights"] = [body_row_height] * n_rows
    metadata_json = json.dumps(table_payload, ensure_ascii=False)

    fragment_xml = (
        f'<g id="{_esc(element_id)}" data-pptx-replace-with="table" '
        f'data-pptx-bounds="{x:g} {y:g} {width:g} {height:g}">'
        f'<metadata type="application/json">{_esc(metadata_json)}</metadata>'
        + "".join(parts)
        + "</g>"
    )

    element = ET.fromstring(fragment_xml)
    stamp_native_fallback_baseline(element)
    fragment = ET.tostring(element, encoding="unicode")

    return {
        "artifact_id": f"table:{element_id}",
        "native_type": "table",
        "svg_fragment": fragment,
        "fallback_sha256_attr": NATIVE_FALLBACK_SHA256_ATTR,
    }, []
