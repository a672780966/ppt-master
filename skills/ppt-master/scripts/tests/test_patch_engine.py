#!/usr/bin/env python3
"""Tests for scripts/runtime/patch_engine.py (P6) -- deterministic apply, hand-written EditPlans, no AI."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime import controller  # noqa: E402
from runtime.build_state import load  # noqa: E402
from runtime.patch_engine import PatchEngineError, apply_edit_plan  # noqa: E402
from runtime.revisions import submit_slide  # noqa: E402

_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">
  <text id="title-01">Old Title</text>
  <rect id="rect-01" x="10" y="20" width="100" height="50" fill="#000000"/>
  <g id="chart-01" data-pptx-replace-with="chart" data-pptx-bounds="200 200 300 150"><rect id="chart-child" x="210" y="210" width="20" height="20"/></g>
  <g id="shape-01" data-pptx-bounds="0 0 10 10"><rect id="inner-rect"/></g>
</svg>"""


class PatchEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        svg_dir = self.project_path / "svg_output"
        svg_dir.mkdir()
        self.svg_path = svg_dir / "01_cover.svg"
        self.svg_path.write_text(_SVG, encoding="utf-8")
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready", from_file=self.svg_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_set_text_bumps_revision_and_validates(self) -> None:
        result = apply_edit_plan(
            self.project_path, "P01", self.svg_path,
            [{"type": "set_text", "target": "title-01", "value": "New Title"}],
        )
        self.assertEqual(result.new_revision, 2)
        self.assertIn("New Title", self.svg_path.read_text(encoding="utf-8"))
        state = load(self.project_path)
        self.assertEqual(state.slides["P01"].revision, 2)
        self.assertTrue(state.slides["P01"].dirty)

    def test_translate_plain_rect_via_xy_attrs(self) -> None:
        apply_edit_plan(self.project_path, "P01", self.svg_path,
                         [{"type": "translate", "target": "rect-01", "dx": 5, "dy": -3}])
        content = self.svg_path.read_text(encoding="utf-8")
        self.assertIn('x="15"', content)
        self.assertIn('y="17"', content)

    def test_native_object_rejects_non_semantic_tool_operation(self) -> None:
        with self.assertRaises(PatchEngineError) as ctx:
            apply_edit_plan(self.project_path, "P01", self.svg_path,
                             [{"type": "resize", "target": "chart-01", "width": 400}])
        self.assertEqual(ctx.exception.code, "NATIVE_OBJECT_INTEGRITY_ERROR")
        self.assertEqual(load(self.project_path).slides["P01"].revision, 1)

    def test_native_object_rejects_mismatched_semantic_tool(self) -> None:
        with self.assertRaises(PatchEngineError) as ctx:
            apply_edit_plan(
                self.project_path, "P01", self.svg_path,
                [{
                    "type": "semantic_tool",
                    "target": "chart-01",
                    "tool": "formula.create",
                    "arguments": {"latex": "x"},
                }],
            )
        self.assertEqual(ctx.exception.code, "NATIVE_OBJECT_INTEGRITY_ERROR")
        self.assertEqual(ctx.exception.details["expected_tool"], "chart.create")
        self.assertEqual(ctx.exception.details["actual_tool"], "formula.create")
        self.assertEqual(load(self.project_path).slides["P01"].revision, 1)

    def test_native_semantic_tool_must_target_native_root_not_descendant(self) -> None:
        with self.assertRaises(PatchEngineError) as ctx:
            apply_edit_plan(
                self.project_path, "P01", self.svg_path,
                [{
                    "type": "semantic_tool",
                    "target": "chart-child",
                    "tool": "chart.create",
                    "arguments": {"type": "line", "categories": ["a"], "series": [{"name": "s", "values": [1]}]},
                }],
            )
        self.assertEqual(ctx.exception.code, "NATIVE_OBJECT_INTEGRITY_ERROR")
        self.assertEqual(ctx.exception.details["native_root"], "chart-01")
        self.assertEqual(load(self.project_path).slides["P01"].revision, 1)

    def test_native_object_rejects_delete(self) -> None:
        with self.assertRaises(PatchEngineError) as ctx:
            apply_edit_plan(self.project_path, "P01", self.svg_path,
                             [{"type": "delete_element", "target": "chart-01"}])
        self.assertEqual(ctx.exception.code, "NATIVE_OBJECT_INTEGRITY_ERROR")

    def test_delete_element_on_plain_shape_removes_it(self) -> None:
        apply_edit_plan(self.project_path, "P01", self.svg_path,
                         [{"type": "delete_element", "target": "shape-01"}])
        self.assertNotIn('id="shape-01"', self.svg_path.read_text(encoding="utf-8"))

    def test_insert_fragment_rejects_duplicate_id(self) -> None:
        with self.assertRaises(PatchEngineError) as ctx:
            apply_edit_plan(self.project_path, "P01", self.svg_path,
                             [{"type": "insert_fragment", "parent": "shape-01",
                               "fragment": '<rect id="rect-01" x="0" y="0" width="1" height="1"/>'}])
        self.assertEqual(ctx.exception.code, "INVALID_EDIT_PLAN")
        self.assertIn("duplicate ids", str(ctx.exception))
        self.assertEqual(load(self.project_path).slides["P01"].revision, 1)  # zero writes

    def test_set_style_rejects_protected_attribute_instead_of_silently_dropping_it(self) -> None:
        with self.assertRaises(PatchEngineError) as ctx:
            apply_edit_plan(
                self.project_path, "P01", self.svg_path,
                [{"type": "set_style", "target": "title-01", "style": {"id": "rewritten"}}],
            )
        self.assertEqual(ctx.exception.code, "INVALID_EDIT_PLAN")
        self.assertIn('id="title-01"', self.svg_path.read_text(encoding="utf-8"))
        self.assertEqual(load(self.project_path).slides["P01"].revision, 1)

    def test_bounded_set_geometry_rejects_fields_it_would_otherwise_ignore(self) -> None:
        with self.assertRaises(PatchEngineError) as ctx:
            apply_edit_plan(
                self.project_path, "P01", self.svg_path,
                [{"type": "set_geometry", "target": "shape-01", "geometry": {"cx": 5}}],
            )
        self.assertEqual(ctx.exception.code, "INVALID_EDIT_PLAN")
        self.assertEqual(load(self.project_path).slides["P01"].revision, 1)

    def test_before_svg_snapshot_captures_pre_edit_content(self) -> None:
        before_path = self.project_path / "before.svg"
        apply_edit_plan(self.project_path, "P01", self.svg_path,
                         [{"type": "set_text", "target": "title-01", "value": "Changed"}],
                         before_svg_path=before_path)
        self.assertIn("Old Title", before_path.read_text(encoding="utf-8"))
        self.assertIn("Changed", self.svg_path.read_text(encoding="utf-8"))

    def test_clear_annotation_id_strips_edit_target_attrs_in_the_same_write(self) -> None:
        self.svg_path.write_text(_SVG.replace(
            '<text id="title-01">Old Title</text>',
            '<text id="title-01" data-edit-target="true" data-edit-annotation="shorten it">Old Title</text>',
        ), encoding="utf-8")
        apply_edit_plan(self.project_path, "P01", self.svg_path,
                         [{"type": "set_text", "target": "title-01", "value": "New"}],
                         clear_annotation_id="title-01")
        content = self.svg_path.read_text(encoding="utf-8")
        self.assertNotIn("data-edit-target", content)
        self.assertNotIn("data-edit-annotation", content)
        self.assertIn("New", content)

    def test_no_temp_file_left_behind_after_successful_write(self) -> None:
        apply_edit_plan(self.project_path, "P01", self.svg_path,
                         [{"type": "set_text", "target": "title-01", "value": "X"}])
        leftovers = list(self.svg_path.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_semantic_tool_on_native_chart_is_allowed_and_autofills_frame(self) -> None:
        result = apply_edit_plan(
            self.project_path, "P01", self.svg_path,
            [{
                "type": "semantic_tool", "target": "chart-01", "tool": "chart.create",
                "arguments": {"type": "line", "categories": ["a", "b"], "series": [{"name": "s", "values": [1, 2]}]},
            }],
        )
        content = self.svg_path.read_text(encoding="utf-8")
        self.assertIn('id="chart-01"', content)
        self.assertIn('data-pptx-bounds="200 200 300 150"', content)  # frame auto-filled from prior bounds
        self.assertEqual(result.new_revision, 2)


if __name__ == "__main__":
    unittest.main()
