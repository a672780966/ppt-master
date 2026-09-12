#!/usr/bin/env python3
"""Tests for scripts/runtime/svg_tree.py (P6) -- shared find_by_id/find_with_parent."""

from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime.svg_tree import find_by_id, find_with_parent  # noqa: E402

_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <g id="title-01"><text id="title-text">Hello</text></g>
  <g id="chart-02"></g>
</svg>"""


class FindByIdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ET.fromstring(_SVG)

    def test_finds_top_level_element(self) -> None:
        elem = find_by_id(self.root, "chart-02")
        self.assertIsNotNone(elem)

    def test_finds_nested_element(self) -> None:
        elem = find_by_id(self.root, "title-text")
        self.assertIsNotNone(elem)
        self.assertEqual(elem.text, "Hello")

    def test_missing_id_returns_none(self) -> None:
        self.assertIsNone(find_by_id(self.root, "does-not-exist"))


class FindWithParentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ET.fromstring(_SVG)

    def test_returns_element_and_direct_parent(self) -> None:
        elem, parent = find_with_parent(self.root, "title-text")
        self.assertIsNotNone(elem)
        self.assertEqual(parent.get("id"), "title-01")

    def test_top_level_elements_parent_is_root(self) -> None:
        elem, parent = find_with_parent(self.root, "chart-02")
        self.assertIs(parent, self.root)

    def test_missing_id_returns_none_none(self) -> None:
        self.assertEqual(find_with_parent(self.root, "nope"), (None, None))


if __name__ == "__main__":
    unittest.main()
