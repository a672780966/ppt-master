#!/usr/bin/env python3
"""
PPT Master - Semantic Tool: chart.create

There is no existing "render a NEW chart's visible SVG from data" script in
PPT Master (confirmed by direct inspection: agents currently hand-draw new
chart geometry using a templates/charts/*.svg as a visual reference and
hand-write the native-data-interface.md marker JSON). This module is a new,
deliberately small geometry renderer for the five most common chart types
(column, bar, line, pie, donut) plus the existing native-data-interface.md
marker/metadata assembly and self-contained fallback-fingerprint stamping
(scripts/svg_to_pptx/native_objects/fallback_hash.py) that already exists.
Any other `type` is a structured UNSUPPORTED_CHART_TYPE error, never a
silent downgrade.

The agent supplies type + frame + categories + series + style; it never
writes a data-pptx-replace-with="chart" marker, a <metadata> JSON payload,
or the chart's bar/line/wedge geometry by hand.

Dependencies:
    None beyond existing sibling PPT Master modules
"""

from __future__ import annotations

import json
import math
from xml.etree import ElementTree as ET

from pptx_shapes import NATIVE_FALLBACK_SHA256_ATTR
from svg_to_pptx.native_objects.fallback_hash import stamp_native_fallback_baseline

from .errors import ToolError

SUPPORTED_CHART_TYPES = ("column", "bar", "line", "pie", "donut")
_DEFAULT_PALETTE = (
    "#1E5FFF", "#F59E0B", "#10B981", "#EF4444",
    "#8B5CF6", "#06B6D4", "#F472B6", "#84CC16",
)


def _require_frame(frame: object) -> tuple[float, float, float, float]:
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
    return x, y, width, height


def _require_series(series: object) -> list[dict[str, object]]:
    if not isinstance(series, list) or not series:
        raise ToolError("INVALID_DATA", "series must be a non-empty array")
    for entry in series:
        if not isinstance(entry, dict) or "values" not in entry:
            raise ToolError("INVALID_DATA", "each series entry needs a name and values")
        if not isinstance(entry["values"], list) or not entry["values"]:
            raise ToolError("INVALID_DATA", "each series.values must be a non-empty array")
    return series


def _palette(style: dict[str, object]) -> list[str]:
    colors = style.get("colors") if isinstance(style, dict) else None
    if isinstance(colors, list) and colors:
        return [str(c) for c in colors]
    return list(_DEFAULT_PALETTE)


def _polar(cx: float, cy: float, r: float, angle_deg: float) -> tuple[float, float]:
    angle = math.radians(angle_deg)
    return cx + r * math.sin(angle), cy - r * math.cos(angle)


def _esc(value: object) -> str:
    text = str(value)
    return (
        text.replace("&", "&amp;").replace('"', "&quot;")
        .replace("<", "&lt;").replace(">", "&gt;")
    )


def _value_to_y(value: float, vmin: float, vmax: float, plot_y: float, plot_h: float) -> float:
    span = vmax - vmin
    if span == 0:
        span = 1
    return plot_y + plot_h * (vmax - value) / span


def _render_column_or_bar(
    *, horizontal: bool, categories: list[str], series: list[dict[str, object]],
    x: float, y: float, width: float, height: float,
    colors: list[str], axis_color: str, show_legend: bool, title: str | None,
    vmin: float, vmax: float,
) -> list[str]:
    parts: list[str] = []
    top_margin = 28 if title else 8
    legend_h = 20 if show_legend and len(series) > 1 else 0
    label_margin = 44 if horizontal else 28
    axis_margin = 28 if horizontal else 44

    plot_x = x + (label_margin if horizontal else axis_margin)
    plot_y = y + top_margin
    plot_w = width - plot_x + x - 4
    plot_h = height - top_margin - legend_h - (axis_margin if horizontal else label_margin)

    if title:
        parts.append(f'<text x="{x + width / 2:g}" y="{y + 18:g}" text-anchor="middle" font-size="16" font-weight="bold" fill="{_esc(axis_color)}">{_esc(title)}</text>')

    n_categories = len(categories)
    n_series = len(series)
    slot = (plot_h if horizontal else plot_w) / max(n_categories, 1)

    if horizontal:
        baseline_x = plot_x + plot_w * (0 - vmin) / (vmax - vmin if vmax != vmin else 1)
        parts.append(f'<line x1="{baseline_x:g}" y1="{plot_y:g}" x2="{baseline_x:g}" y2="{plot_y + plot_h:g}" stroke="{_esc(axis_color)}" stroke-width="1"/>')
    else:
        baseline_y = _value_to_y(0, vmin, vmax, plot_y, plot_h)
        parts.append(f'<line x1="{plot_x:g}" y1="{baseline_y:g}" x2="{plot_x + plot_w:g}" y2="{baseline_y:g}" stroke="{_esc(axis_color)}" stroke-width="1"/>')

    for i, category in enumerate(categories):
        group_extent = slot * 0.7
        bar_extent = group_extent / n_series
        for j, one_series in enumerate(series):
            value = float(one_series["values"][i])
            color = colors[j % len(colors)]
            if horizontal:
                slot_start = plot_y + i * slot + slot * 0.15
                bar_y = slot_start + j * bar_extent
                bar_x_end = plot_x + plot_w * (value - vmin) / (vmax - vmin if vmax != vmin else 1)
                bar_x = min(bar_x_end, baseline_x)
                bar_len = abs(bar_x_end - baseline_x)
                parts.append(f'<rect x="{bar_x:g}" y="{bar_y:g}" width="{bar_len:g}" height="{bar_extent * 0.85:g}" fill="{_esc(color)}"/>')
            else:
                slot_start = plot_x + i * slot + slot * 0.15
                bar_x = slot_start + j * bar_extent
                bar_y_end = _value_to_y(value, vmin, vmax, plot_y, plot_h)
                bar_y = min(bar_y_end, baseline_y)
                bar_len = abs(baseline_y - bar_y_end)
                parts.append(f'<rect x="{bar_x:g}" y="{bar_y:g}" width="{bar_extent * 0.85:g}" height="{bar_len:g}" fill="{_esc(color)}"/>')
        if horizontal:
            label_y = plot_y + i * slot + slot / 2 + 4
            parts.append(f'<text x="{plot_x - 8:g}" y="{label_y:g}" text-anchor="end" font-size="12" fill="{_esc(axis_color)}">{_esc(category)}</text>')
        else:
            label_x = plot_x + i * slot + slot / 2
            parts.append(f'<text x="{label_x:g}" y="{plot_y + plot_h + 16:g}" text-anchor="middle" font-size="12" fill="{_esc(axis_color)}">{_esc(category)}</text>')

    if show_legend and n_series > 1:
        legend_y = y + height - 6
        legend_x = x
        for j, one_series in enumerate(series):
            name = str(one_series.get("name", f"series {j + 1}"))
            chip_x = legend_x + j * 110
            parts.append(f'<rect x="{chip_x:g}" y="{legend_y - 10:g}" width="10" height="10" fill="{_esc(colors[j % len(colors)])}"/>')
            parts.append(f'<text x="{chip_x + 14:g}" y="{legend_y - 1:g}" font-size="12" fill="{_esc(axis_color)}">{_esc(name)}</text>')

    return parts


def _render_line(
    *, categories: list[str], series: list[dict[str, object]],
    x: float, y: float, width: float, height: float,
    colors: list[str], axis_color: str, show_legend: bool, title: str | None,
    vmin: float, vmax: float,
) -> list[str]:
    parts: list[str] = []
    top_margin = 28 if title else 8
    legend_h = 20 if show_legend and len(series) > 1 else 0
    plot_x = x + 44
    plot_y = y + top_margin
    plot_w = width - 44 - 4
    plot_h = height - top_margin - legend_h - 28

    if title:
        parts.append(f'<text x="{x + width / 2:g}" y="{y + 18:g}" text-anchor="middle" font-size="16" font-weight="bold" fill="{_esc(axis_color)}">{_esc(title)}</text>')

    baseline_y = _value_to_y(0, vmin, vmax, plot_y, plot_h)
    parts.append(f'<line x1="{plot_x:g}" y1="{baseline_y:g}" x2="{plot_x + plot_w:g}" y2="{baseline_y:g}" stroke="{_esc(axis_color)}" stroke-width="1"/>')

    n = len(categories)
    step = plot_w / max(n - 1, 1) if n > 1 else 0
    for i, category in enumerate(categories):
        px = plot_x + (i * step if n > 1 else plot_w / 2)
        parts.append(f'<text x="{px:g}" y="{plot_y + plot_h + 16:g}" text-anchor="middle" font-size="12" fill="{_esc(axis_color)}">{_esc(category)}</text>')

    for j, one_series in enumerate(series):
        color = colors[j % len(colors)]
        points = []
        for i in range(n):
            px = plot_x + (i * step if n > 1 else plot_w / 2)
            py = _value_to_y(float(one_series["values"][i]), vmin, vmax, plot_y, plot_h)
            points.append((px, py))
        polyline = " ".join(f"{px:g},{py:g}" for px, py in points)
        parts.append(f'<polyline points="{polyline}" fill="none" stroke="{_esc(color)}" stroke-width="2.5"/>')
        for px, py in points:
            parts.append(f'<circle cx="{px:g}" cy="{py:g}" r="3.5" fill="{_esc(color)}"/>')

    if show_legend and len(series) > 1:
        legend_y = y + height - 6
        for j, one_series in enumerate(series):
            name = str(one_series.get("name", f"series {j + 1}"))
            chip_x = x + j * 110
            parts.append(f'<rect x="{chip_x:g}" y="{legend_y - 10:g}" width="10" height="10" fill="{_esc(colors[j % len(colors)])}"/>')
            parts.append(f'<text x="{chip_x + 14:g}" y="{legend_y - 1:g}" font-size="12" fill="{_esc(axis_color)}">{_esc(name)}</text>')

    return parts


def _render_pie_or_donut(
    *, donut: bool, categories: list[str], values: list[float],
    x: float, y: float, width: float, height: float,
    colors: list[str], axis_color: str, title: str | None,
) -> list[str]:
    parts: list[str] = []
    top_margin = 28 if title else 8
    if title:
        parts.append(f'<text x="{x + width / 2:g}" y="{y + 18:g}" text-anchor="middle" font-size="16" font-weight="bold" fill="{_esc(axis_color)}">{_esc(title)}</text>')

    legend_w = width * 0.4
    circle_area_w = width - legend_w
    circle_area_h = height - top_margin
    r_outer = min(circle_area_w, circle_area_h) / 2 - 8
    cx = x + circle_area_w / 2
    cy = y + top_margin + circle_area_h / 2
    r_inner = r_outer * 0.55 if donut else 0

    total = sum(values) or 1.0
    angle = -180.0
    for i, value in enumerate(values):
        span = value / total * 360.0
        end_angle = angle + span
        color = colors[i % len(colors)]
        large_arc = 1 if span > 180 else 0
        x1o, y1o = _polar(cx, cy, r_outer, angle)
        x2o, y2o = _polar(cx, cy, r_outer, end_angle)
        if donut:
            x1i, y1i = _polar(cx, cy, r_inner, angle)
            x2i, y2i = _polar(cx, cy, r_inner, end_angle)
            path = (
                f"M {x1o:g},{y1o:g} A {r_outer:g},{r_outer:g} 0 {large_arc} 1 {x2o:g},{y2o:g} "
                f"L {x2i:g},{y2i:g} A {r_inner:g},{r_inner:g} 0 {large_arc} 0 {x1i:g},{y1i:g} Z"
            )
        else:
            path = f"M {cx:g},{cy:g} L {x1o:g},{y1o:g} A {r_outer:g},{r_outer:g} 0 {large_arc} 1 {x2o:g},{y2o:g} Z"
        parts.append(f'<path d="{path}" fill="{_esc(color)}"/>')
        angle = end_angle

    legend_x = x + circle_area_w + 8
    legend_y = y + top_margin + 8
    for i, (category, value) in enumerate(zip(categories, values)):
        pct = round(value / total * 100)
        row_y = legend_y + i * 20
        parts.append(f'<rect x="{legend_x:g}" y="{row_y - 10:g}" width="10" height="10" fill="{_esc(colors[i % len(colors)])}"/>')
        parts.append(f'<text x="{legend_x + 14:g}" y="{row_y - 1:g}" font-size="12" fill="{_esc(axis_color)}">{_esc(category)} ({pct}%)</text>')

    return parts


def chart_create(payload: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """Render one native-ready chart marker (visible fallback + JSON metadata).

    Never writes any file; the returned fragment is self-contained and its
    data-pptx-fallback-sha256 is already stamped, so no separate
    stamp_native_fallbacks.py run is needed after the agent inserts it
    verbatim into a page.
    """
    chart_type = payload.get("type")
    if chart_type not in SUPPORTED_CHART_TYPES:
        raise ToolError(
            "UNSUPPORTED_CHART_TYPE",
            f"type must be one of {SUPPORTED_CHART_TYPES}, got {chart_type!r}",
            supported_types=list(SUPPORTED_CHART_TYPES),
        )
    element_id = payload.get("id")
    if not isinstance(element_id, str) or not element_id:
        raise ToolError("INVALID_ID", "id must be a non-empty string")

    x, y, width, height = _require_frame(payload.get("frame"))
    categories = payload.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ToolError("INVALID_DATA", "categories must be a non-empty array")
    categories = [str(c) for c in categories]
    series = _require_series(payload.get("series"))

    for entry in series:
        if len(entry["values"]) != len(categories):
            raise ToolError(
                "INVALID_DATA",
                f"series {entry.get('name', '?')!r} has {len(entry['values'])} values "
                f"but there are {len(categories)} categories",
            )

    if chart_type in ("pie", "donut") and len(series) != 1:
        raise ToolError("INVALID_DATA", f"{chart_type} requires exactly one series, got {len(series)}")

    style = payload.get("style") or {}
    if not isinstance(style, dict):
        raise ToolError("INVALID_STYLE", "style must be an object")
    colors = _palette(style)
    axis_color = str(style.get("axis_color", "#6E6E73"))
    title = payload.get("title")
    show_legend = bool(payload.get("show_legend", True))

    all_values = [float(v) for entry in series for v in entry["values"]]
    axes = payload.get("axes") or {}
    vmin = float(axes.get("minimum")) if isinstance(axes, dict) and axes.get("minimum") is not None else min(0.0, min(all_values))
    vmax = float(axes.get("maximum")) if isinstance(axes, dict) and axes.get("maximum") is not None else max(all_values)
    if vmax <= vmin:
        vmax = vmin + 1.0

    if chart_type == "column":
        fallback_parts = _render_column_or_bar(
            horizontal=False, categories=categories, series=series,
            x=x, y=y, width=width, height=height, colors=colors,
            axis_color=axis_color, show_legend=show_legend, title=title,
            vmin=vmin, vmax=vmax,
        )
    elif chart_type == "bar":
        fallback_parts = _render_column_or_bar(
            horizontal=True, categories=categories, series=series,
            x=x, y=y, width=width, height=height, colors=colors,
            axis_color=axis_color, show_legend=show_legend, title=title,
            vmin=vmin, vmax=vmax,
        )
    elif chart_type == "line":
        fallback_parts = _render_line(
            categories=categories, series=series,
            x=x, y=y, width=width, height=height, colors=colors,
            axis_color=axis_color, show_legend=show_legend, title=title,
            vmin=vmin, vmax=vmax,
        )
    else:
        fallback_parts = _render_pie_or_donut(
            donut=(chart_type == "donut"), categories=categories,
            values=[float(v) for v in series[0]["values"]],
            x=x, y=y, width=width, height=height, colors=colors,
            axis_color=axis_color, title=title,
        )

    metadata = {
        "x": x, "y": y, "width": width, "height": height,
        "name": element_id, "type": chart_type,
        "categories": categories,
        "series": [{"name": s.get("name", ""), "values": [float(v) for v in s["values"]]} for s in series],
    }
    if title:
        metadata["title"] = title
    if show_legend:
        metadata["show_legend"] = True
    metadata_json = json.dumps(metadata, ensure_ascii=False)

    fragment_xml = (
        f'<g id="{_esc(element_id)}" data-pptx-replace-with="chart" '
        f'data-pptx-bounds="{x:g} {y:g} {width:g} {height:g}">'
        f'<metadata type="application/json">{_esc(metadata_json)}</metadata>'
        + "".join(fallback_parts)
        + "</g>"
    )

    element = ET.fromstring(fragment_xml)
    stamp_native_fallback_baseline(element)
    fragment = ET.tostring(element, encoding="unicode")

    return {
        "artifact_id": f"chart:{element_id}",
        "native_type": "chart",
        "chart_type": chart_type,
        "svg_fragment": fragment,
        "fallback_sha256_attr": NATIVE_FALLBACK_SHA256_ATTR,
    }, []
