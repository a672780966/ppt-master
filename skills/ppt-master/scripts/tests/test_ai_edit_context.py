#!/usr/bin/env python3
"""Tests for scripts/runtime/ai_edit_context.py (P6) -- Selection Context Compiler."""

from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime.ai_edit_context import (  # noqa: E402
    SelectionContextError,
    build_selection_context,
    object_kind,
    selected_object_facts,
)

_REPO_ROOT = SCRIPTS_DIR.parents[2]
_P3_PROJECT = _REPO_ROOT / "projects" / "semantic-tools-p3-fixture_20260912"

_FIXTURE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">
  <text id="title-01">Hello World</text>
  <g id="group-01"><text>only</text><tspan>text</tspan></g>
  <g id="chart-01" data-pptx-replace-with="chart" data-pptx-bounds="10 20 300 200"></g>
  <g id="shape-01" data-pptx-authoring="preset" data-pptx-object="shape" data-pptx-prst="rightArrow"></g>
  <g id="chrome-01" data-pptx-role="footer"></g>
  <image id="pic-01" href="x.png"/>
  <rect id="rect-01" x="1" y="2" width="30" height="40"/>
</svg>"""


class ObjectKindTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ET.fromstring(_FIXTURE_SVG)

    def _kind(self, element_id: str) -> str:
        from runtime.svg_tree import find_by_id
        return object_kind(find_by_id(self.root, element_id))

    def test_plain_text_element(self) -> None:
        self.assertEqual(self._kind("title-01"), "text")

    def test_group_of_only_text_children_classified_as_text(self) -> None:
        self.assertEqual(self._kind("group-01"), "text")

    def test_native_chart_marker(self) -> None:
        self.assertEqual(self._kind("chart-01"), "chart")

    def test_native_shape_marker(self) -> None:
        self.assertEqual(self._kind("shape-01"), "shape")

    def test_structural_role_marker(self) -> None:
        self.assertEqual(self._kind("chrome-01"), "footer")

    def test_image_tag(self) -> None:
        self.assertEqual(self._kind("pic-01"), "image")

    def test_bare_non_text_element_is_unknown_kind_at_tag_level(self) -> None:
        # rect isn't text/image/g -- falls through to "unknown"
        from runtime.svg_tree import find_by_id
        self.assertEqual(object_kind(find_by_id(self.root, "rect-01")), "unknown")


class SelectedObjectFactsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ET.fromstring(_FIXTURE_SVG)

    def test_bounds_from_native_marker(self) -> None:
        facts = selected_object_facts(self.root, "chart-01")
        self.assertEqual(facts["kind"], "chart")
        self.assertEqual(facts["native_object_type"], "chart")
        self.assertEqual(facts["bounds"], {"x": 10.0, "y": 20.0, "width": 300.0, "height": 200.0})
        self.assertEqual(facts["parent_id"], None)  # top-level, parent is root

    def test_bounds_from_plain_geometry_attrs(self) -> None:
        facts = selected_object_facts(self.root, "rect-01")
        self.assertEqual(facts["bounds"], {"x": 1.0, "y": 2.0, "width": 30.0, "height": 40.0})

    def test_text_extraction(self) -> None:
        facts = selected_object_facts(self.root, "title-01")
        self.assertEqual(facts["text"], "Hello World")

    def test_parent_id_for_nested_element(self) -> None:
        # give the text child inside group-01 a real id to test parent linkage
        root = ET.fromstring(_FIXTURE_SVG)
        group = root.find(".//*[@id='group-01']")
        group[0].set("id", "nested-text")
        facts = selected_object_facts(root, "nested-text")
        self.assertEqual(facts["parent_id"], "group-01")

    def test_missing_id_raises_selection_no_longer_exists(self) -> None:
        with self.assertRaises(SelectionContextError) as ctx:
            selected_object_facts(self.root, "does-not-exist")
        self.assertEqual(ctx.exception.code, "SELECTION_NO_LONGER_EXISTS")


class BuildSelectionContextRealProjectTests(unittest.TestCase):
    """Against the real, on-disk, Quick-route P3 fixture project -- proves
    the graceful page_context fallback (no design_spec.md/spec_lock.md on
    Quick) and real native-object fact extraction, not a synthetic stub."""

    @unittest.skipUnless(_P3_PROJECT.is_dir(), "real P3 fixture project not present on this machine")
    def test_quick_route_project_falls_back_gracefully_with_real_object_facts(self) -> None:
        ctx = build_selection_context(
            _P3_PROJECT,
            slide_id="P01",
            expected_revision=0,
            plan_revision=0,
            scope="selection",
            selection_ids=["p-tools-hours-chart", "p-tools-average", "p-tools-arrow"],
            instruction="test instruction",
        )
        self.assertIsNone(ctx.page_context)
        self.assertTrue(any("page_context_unavailable" in w for w in ctx.warnings))
        kinds = {f["id"]: f["kind"] for f in ctx.object_facts}
        self.assertEqual(kinds["p-tools-hours-chart"], "chart")
        self.assertEqual(kinds["p-tools-average"], "formula")
        self.assertEqual(kinds["p-tools-arrow"], "shape")

    @unittest.skipUnless(_P3_PROJECT.is_dir(), "real P3 fixture project not present on this machine")
    def test_missing_slide_svg_raises_target_not_found(self) -> None:
        with self.assertRaises(SelectionContextError) as ctx:
            build_selection_context(
                _P3_PROJECT, slide_id="P99", expected_revision=0, plan_revision=0,
                scope="element", selection_ids=[], instruction="x",
            )
        self.assertEqual(ctx.exception.code, "TARGET_NOT_FOUND")

    @unittest.skipUnless(_P3_PROJECT.is_dir(), "real P3 fixture project not present on this machine")
    def test_slide_scope_skips_object_facts_entirely(self) -> None:
        ctx = build_selection_context(
            _P3_PROJECT, slide_id="P01", expected_revision=0, plan_revision=0,
            scope="slide", selection_ids=[], instruction="redo the whole page",
        )
        self.assertEqual(ctx.object_facts, [])


if __name__ == "__main__":
    unittest.main()
