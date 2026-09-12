#!/usr/bin/env python3
"""Tests for scripts/workbench/edit_wiring.py (P5) -- the save_all() splice logic."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime import controller  # noqa: E402
from runtime.build_state import StaleEditError, load  # noqa: E402
from runtime.revisions import submit_slide  # noqa: E402
from workbench.edit_wiring import record_direct_edit  # noqa: E402


class RecordDirectEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        self.svg_dir = self.project_path / "svg_output"
        self.svg_dir.mkdir(parents=True)
        self.svg_file = self.svg_dir / "01_cover.svg"
        self.svg_file.write_text("<svg></svg>", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_op_when_no_build_state(self) -> None:
        self.assertIsNone(record_direct_edit(self.project_path, self.svg_file))

    def test_no_op_when_filename_unmappable(self) -> None:
        controller.init_state(self.project_path, route="quick")
        unmapped = self.svg_dir / "cover.svg"
        unmapped.write_text("<svg></svg>", encoding="utf-8")
        self.assertIsNone(record_direct_edit(self.project_path, unmapped))

    def test_bumps_revision_sets_dirty_and_stales_validation(self) -> None:
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready", from_file=self.svg_file)
        # Simulate the slide having been validated at revision 1.
        from runtime.build_state import locked_state
        with locked_state(self.project_path) as state:
            state.slides["P01"].validated_revision = 1
            state.slides["P01"].validation_status = "passed"

        self.svg_file.write_text("<svg>edited</svg>", encoding="utf-8")
        slide = record_direct_edit(self.project_path, self.svg_file)

        self.assertEqual(slide.revision, 2)
        self.assertTrue(slide.dirty)
        state = load(self.project_path)
        self.assertEqual(state.slides["P01"].validated_revision, 1)  # unchanged -> now stale by comparison
        self.assertTrue(state.export_dirty)

    def test_auto_vivifies_a_slide_never_seen_before(self) -> None:
        controller.init_state(self.project_path, route="quick")
        slide = record_direct_edit(self.project_path, self.svg_file)
        self.assertEqual(slide.revision, 1)
        self.assertEqual(load(self.project_path).slides["P01"].status, "dirty")

    def test_retries_through_one_stale_edit_error(self) -> None:
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready", from_file=self.svg_file)

        real_submit_slide = submit_slide
        call_count = {"n": 0}

        def flaky_submit_slide(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise StaleEditError("P01", kwargs.get("expected_revision", 0), 99)
            return real_submit_slide(*args, **kwargs)

        with mock.patch("workbench.edit_wiring.submit_slide", side_effect=flaky_submit_slide):
            slide = record_direct_edit(self.project_path, self.svg_file)
        self.assertEqual(call_count["n"], 2)
        self.assertEqual(slide.revision, 2)

    def test_raises_after_exhausting_retries(self) -> None:
        controller.init_state(self.project_path, route="quick", pages=["P01"])

        def always_stale(*args, **kwargs):
            raise StaleEditError("P01", kwargs.get("expected_revision", 0), 99)

        with mock.patch("workbench.edit_wiring.submit_slide", side_effect=always_stale):
            with self.assertRaises(StaleEditError):
                record_direct_edit(self.project_path, self.svg_file)


if __name__ == "__main__":
    unittest.main()
