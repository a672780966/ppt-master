#!/usr/bin/env python3
"""Tests for scripts/tools/dispatch.py (P5) -- the shared CLI/Workbench invoke path."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime import controller  # noqa: E402
from tools.dispatch import TOOLS, invoke  # noqa: E402


class DispatchInvokeTests(unittest.TestCase):
    def test_unknown_tool_returns_exit_code_2(self) -> None:
        envelope, exit_code = invoke("not.a.real.tool", {})
        self.assertEqual(exit_code, 2)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["errors"][0]["code"], "UNKNOWN_TOOL")

    def test_all_six_tools_registered(self) -> None:
        self.assertEqual(
            set(TOOLS),
            {"shape.create", "formula.create", "chart.create", "table.create", "slide.validate", "deck.export"},
        )

    def test_shape_create_success_matches_cli_envelope_shape(self) -> None:
        envelope, exit_code = invoke("shape.create", {
            "id": "p03-arrow", "preset": "rightArrow",
            "frame": {"x": 100, "y": 100, "width": 200, "height": 80},
            "fill": "#2563EB", "stroke": "none",
        })
        self.assertEqual(exit_code, 0)
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["artifact_id"], "shape:p03-arrow")

    def test_tool_error_maps_to_exit_code_1(self) -> None:
        envelope, exit_code = invoke("shape.create", {
            "id": "x", "preset": "notreal", "frame": {"x": 0, "y": 0, "width": 10, "height": 10},
        })
        self.assertEqual(exit_code, 1)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["errors"][0]["code"], "UNKNOWN_PRESET")

    def test_deck_export_blocked_by_enforce_mode_hook_maps_to_exit_code_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            controller.init_state(project_path, route="quick", pages=["P01"])
            controller.set_hooks_mode(project_path, "enforce")
            envelope, exit_code = invoke("deck.export", {"project": str(project_path)})
            self.assertEqual(exit_code, 1)
            self.assertFalse(envelope["ok"])
            self.assertEqual(envelope["errors"][0]["code"], "DECK_NOT_EXPORTABLE")


if __name__ == "__main__":
    unittest.main()
