#!/usr/bin/env python3
"""Tests for scripts/tools/{validate,export}.py (P3 slide.validate / deck.export)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from tools.errors import ToolError  # noqa: E402
from tools.export import deck_export  # noqa: E402
from tools.validate import slide_validate  # noqa: E402

# Reuses the real P0/P2 benchmark project on disk rather than re-authoring a
# throwaway fixture -- it already has a passing final quality report and a
# real export, so this exercises the wrappers against real checker/exporter
# output instead of a synthetic stand-in.
_REAL_PROJECT = (
    SCRIPTS_DIR.parents[2]
    / "projects"
    / "text-teaching-p2-default_ppt169_20260912"
)


class SlideValidateTests(unittest.TestCase):
    def test_invalid_project_raises_stable_code(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            slide_validate({"project": str(Path(tempfile.mkdtemp()) / "does-not-exist")})
        self.assertEqual(ctx.exception.code, "INVALID_PROJECT")

    def test_page_stage_requires_a_page(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            slide_validate({"project": ".", "stage": "page"})
        self.assertEqual(ctx.exception.code, "INVALID_STAGE")

    @unittest.skipUnless(_REAL_PROJECT.is_dir(), "real P0/P2 benchmark project not present on this machine")
    def test_real_project_clean_page_passes(self) -> None:
        fields, warnings = slide_validate({"project": str(_REAL_PROJECT), "page": "02_objectives.svg"})
        self.assertTrue(fields["ok"])
        self.assertEqual(fields["errors"], [])

    @unittest.skipUnless(_REAL_PROJECT.is_dir(), "real P0/P2 benchmark project not present on this machine")
    def test_real_project_page_with_known_warning_is_classified(self) -> None:
        fields, warnings = slide_validate({"project": str(_REAL_PROJECT), "page": "09_summary.svg"})
        self.assertTrue(fields["ok"])  # warning only, not blocking
        self.assertTrue(any(w.startswith("TEXT_OVERFLOW:") for w in warnings))


class DeckExportTests(unittest.TestCase):
    def test_invalid_project_raises_stable_code(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            deck_export({"project": str(Path(tempfile.mkdtemp()) / "does-not-exist")})
        self.assertEqual(ctx.exception.code, "INVALID_PROJECT")

    @unittest.skipUnless(_REAL_PROJECT.is_dir(), "real P0/P2 benchmark project not present on this machine")
    def test_real_project_exports_and_reports_expected_state_mismatch(self) -> None:
        fields, warnings = deck_export({
            "project": str(_REAL_PROJECT),
            "no_notes": True,
            "expected_state": {"slide_count": 99},
        })
        self.assertTrue(fields["ok"])
        self.assertEqual(fields["slide_count"], 9)
        self.assertTrue(any("expected_state.slide_count" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
