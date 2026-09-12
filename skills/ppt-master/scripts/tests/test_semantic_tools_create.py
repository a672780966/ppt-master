#!/usr/bin/env python3
"""Tests for scripts/tools/{shape,formula,chart,table}.py (P3 create tools)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from tools.chart import chart_create  # noqa: E402
from tools.errors import ToolError  # noqa: E402
from tools.formula import formula_create, linearize_preview  # noqa: E402
from tools.shape import shape_create  # noqa: E402
from tools.table import table_create  # noqa: E402


class ShapeCreateTests(unittest.TestCase):
    def test_renders_a_real_preset_fragment(self) -> None:
        fields, warnings = shape_create({
            "id": "p03-arrow", "preset": "rightArrow",
            "frame": {"x": 100, "y": 100, "width": 200, "height": 80},
            "fill": "#2563EB", "stroke": "none",
        })
        self.assertEqual(warnings, [])
        self.assertEqual(fields["artifact_id"], "shape:p03-arrow")
        elem = ET.fromstring(fields["svg_fragment"])
        self.assertEqual(elem.get("data-pptx-prst"), "rightArrow")
        self.assertIsNotNone(elem.find("path"))

    def test_unknown_preset_raises_stable_code(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            shape_create({"id": "x", "preset": "notreal", "frame": {"x": 0, "y": 0, "width": 10, "height": 10}})
        self.assertEqual(ctx.exception.code, "UNKNOWN_PRESET")

    def test_invalid_frame_raises_stable_code(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            shape_create({"id": "x", "preset": "rightArrow", "frame": {"x": 0, "y": 0}})
        self.assertEqual(ctx.exception.code, "INVALID_FRAME")


class FormulaCreateTests(unittest.TestCase):
    def test_linearize_preview_handles_frac_sqrt_pm_and_scripts(self) -> None:
        preview, approximate = linearize_preview(r"\frac{-b \pm \sqrt{b^2-4ac}}{2a}")
        self.assertFalse(approximate)
        self.assertIn("±", preview)
        self.assertIn("√", preview)

    def test_block_formula_is_a_complete_replace_with_group(self) -> None:
        fields, warnings = formula_create({
            "id": "quad", "latex": r"\frac{-b}{2a}", "display": "block",
            "frame": {"x": 100, "y": 100, "width": 400, "height": 100},
            "font_size": 36, "color": "#173B57", "align": "center",
        })
        elem = ET.fromstring(fields["svg_fragment"])
        self.assertEqual(elem.get("data-pptx-replace-with"), "formula")
        metadata = elem.find("metadata")
        payload = json.loads(metadata.text)
        self.assertEqual(payload["latex"], r"\frac{-b}{2a}")
        self.assertEqual(payload["display"], "block")

    def test_inline_formula_is_a_bare_tspan(self) -> None:
        fields, warnings = formula_create({"latex": r"\frac{a}{b}", "display": "inline"})
        self.assertTrue(fields["svg_fragment"].startswith("<tspan"))
        self.assertNotIn("data-pptx-replace-with", fields["svg_fragment"])

    def test_unsupported_latex_raises_stable_code(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            formula_create({"id": "x", "latex": r"\notarealcommand{x}", "display": "block",
                             "frame": {"x": 0, "y": 0, "width": 10, "height": 10}})
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_LATEX")


class ChartCreateTests(unittest.TestCase):
    _BASE = {
        "id": "p05-chart",
        "frame": {"x": 0, "y": 0, "width": 400, "height": 300},
        "categories": ["Q1", "Q2", "Q3"],
        "series": [{"name": "Cloud", "values": [12, 15, 19]}],
    }

    def test_all_five_supported_types_render_a_self_contained_marker(self) -> None:
        for chart_type in ("column", "bar", "line"):
            payload = dict(self._BASE, type=chart_type)
            fields, warnings = chart_create(payload)
            elem = ET.fromstring(fields["svg_fragment"])
            self.assertEqual(elem.get("data-pptx-replace-with"), "chart")
            self.assertIn("data-pptx-fallback-sha256", elem.attrib)
            self.assertIsNotNone(json.loads(elem.find("metadata").text))

        for chart_type in ("pie", "donut"):
            payload = dict(self._BASE, type=chart_type, series=[{"name": "share", "values": [30, 45, 25]}])
            fields, warnings = chart_create(payload)
            elem = ET.fromstring(fields["svg_fragment"])
            self.assertEqual(elem.get("data-pptx-replace-with"), "chart")
            self.assertIn("data-pptx-fallback-sha256", elem.attrib)

    def test_unsupported_type_raises_with_supported_list(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            chart_create(dict(self._BASE, type="radar"))
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_CHART_TYPE")
        self.assertIn("supported_types", ctx.exception.details)

    def test_pie_requires_exactly_one_series(self) -> None:
        payload = dict(self._BASE, type="pie", series=[{"values": [1, 2, 3]}, {"values": [4, 5, 6]}])
        with self.assertRaises(ToolError) as ctx:
            chart_create(payload)
        self.assertEqual(ctx.exception.code, "INVALID_DATA")

    def test_series_length_mismatch_raises_invalid_data(self) -> None:
        payload = dict(self._BASE, type="column", series=[{"name": "x", "values": [1, 2]}])
        with self.assertRaises(ToolError) as ctx:
            chart_create(payload)
        self.assertEqual(ctx.exception.code, "INVALID_DATA")

    def test_fallback_fingerprint_is_stable_across_identical_input(self) -> None:
        fields1, _ = chart_create(dict(self._BASE, type="column"))
        fields2, _ = chart_create(dict(self._BASE, type="column"))
        hash1 = ET.fromstring(fields1["svg_fragment"]).get("data-pptx-fallback-sha256")
        hash2 = ET.fromstring(fields2["svg_fragment"]).get("data-pptx-fallback-sha256")
        self.assertEqual(hash1, hash2)


class TableCreateTests(unittest.TestCase):
    def test_renders_grid_with_header_and_matching_metadata(self) -> None:
        fields, warnings = table_create({
            "id": "p05-tbl",
            "frame": {"x": 0, "y": 0, "width": 400, "height": 200},
            "columns": ["A", "B"],
            "rows": [["1", "2"], ["3", "4"]],
        })
        elem = ET.fromstring(fields["svg_fragment"])
        self.assertEqual(elem.get("data-pptx-replace-with"), "table")
        self.assertIn("data-pptx-fallback-sha256", elem.attrib)
        payload = json.loads(elem.find("metadata").text)
        self.assertEqual(payload["schema"], "ppt-master.semantic-table.v2")
        self.assertEqual([[c["text"] for c in row] for row in payload["rows"]], [["1", "2"], ["3", "4"]])
        self.assertEqual([c["text"] for c in payload["columns"]], ["A", "B"])
        self.assertTrue(all(c["bold"] for c in payload["columns"]))
        self.assertEqual(payload["style"]["header_fill"], "#1E5FFF")
        self.assertEqual(len(payload["row_heights"]), 3)  # header + 2 body rows

    def test_mismatched_row_length_raises_invalid_data(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            table_create({
                "id": "x", "frame": {"x": 0, "y": 0, "width": 100, "height": 100},
                "rows": [["a", "b"], ["c"]],
            })
        self.assertEqual(ctx.exception.code, "INVALID_DATA")

    def test_table_without_header_row(self) -> None:
        fields, warnings = table_create({
            "id": "x", "frame": {"x": 0, "y": 0, "width": 100, "height": 100},
            "rows": [["a", "b"]],
        })
        payload = json.loads(ET.fromstring(fields["svg_fragment"]).find("metadata").text)
        self.assertNotIn("columns", payload)


if __name__ == "__main__":
    unittest.main()
