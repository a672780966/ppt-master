#!/usr/bin/env python3
"""Tests for scripts/workbench/runtime_api.py (P5) -- real runtime/tool/hook calls, no mocking."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from flask import Flask  # noqa: E402

from runtime import controller  # noqa: E402
from runtime.build_state import load  # noqa: E402
from runtime.revisions import submit_slide  # noqa: E402
from workbench.runtime_api import register  # noqa: E402


class RuntimeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        app = Flask(__name__)
        register(app, self.project_path)
        self.client = app.test_client()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_project_reports_legacy_when_no_build_state(self) -> None:
        resp = self.client.get("/api/runtime/project")
        self.assertEqual(resp.get_json()["mode"], "legacy")

    def test_project_reports_real_fields_once_adopted(self) -> None:
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        data = self.client.get("/api/runtime/project").get_json()
        self.assertEqual(data["mode"], "build-state")
        self.assertEqual(data["route"], "quick")
        self.assertEqual(data["hooks"]["label"], "Advisory")

    def test_slides_reflects_real_status_mix(self) -> None:
        controller.init_state(self.project_path, route="quick", pages=["P01", "P02", "P03"])
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready")
        submit_slide(self.project_path, "P02", expected_revision=0, status="building")
        data = self.client.get("/api/runtime/slides").get_json()
        labels = {s["id"]: s["display_label"] for s in data["slides"]}
        self.assertEqual(labels["P01"], "Needs validation")  # ready but never validated
        self.assertEqual(labels["P02"], "Building")
        self.assertEqual(labels["P03"], "Planned")

    def test_slide_detail_404_for_unknown_slide(self) -> None:
        controller.init_state(self.project_path, route="quick")
        resp = self.client.get("/api/runtime/slides/P99")
        self.assertFalse(resp.get_json()["ok"])
        self.assertEqual(resp.get_json()["errors"][0]["code"], "SLIDE_NOT_FOUND")

    def test_validate_slide_route_calls_the_real_checker_and_binds_revision(self) -> None:
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        svg_dir = self.project_path / "svg_output"
        svg_dir.mkdir()
        svg_file = svg_dir / "01_cover.svg"
        svg_file.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"></svg>', encoding="utf-8")
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready", from_file=svg_file)

        resp = self.client.post("/api/runtime/slides/P01/validate")
        envelope = resp.get_json()
        self.assertIn("ok", envelope)  # real checker ran (pass or fail, either is fine here)
        state = load(self.project_path)
        # Whatever the checker said, PostToolUse should have bound validated_revision.
        self.assertEqual(state.slides["P01"].validated_revision, state.slides["P01"].revision)

    def test_export_route_blocked_in_enforce_mode_names_the_real_reason(self) -> None:
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready")
        controller.set_hooks_mode(self.project_path, "enforce")

        resp = self.client.post("/api/runtime/deck/export", json={"no_notes": True})
        envelope = resp.get_json()
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["errors"][0]["code"], "DECK_NOT_EXPORTABLE")
        reasons = {r["type"] for r in envelope["errors"][0]["reasons"]}
        self.assertIn("stale_validation", reasons)

    def test_resume_is_read_only_by_default(self) -> None:
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        svg_dir = self.project_path / "svg_output"
        svg_dir.mkdir()
        svg_file = svg_dir / "01_cover.svg"
        svg_file.write_text("<svg></svg>", encoding="utf-8")
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready", from_file=svg_file)
        svg_file.write_text("<svg>changed on disk</svg>", encoding="utf-8")

        data = self.client.get("/api/runtime/resume").get_json()
        self.assertEqual(len(data["reconciliation"]), 1)
        self.assertEqual(data["reconciliation"][0]["code"], "EXTERNAL_ARTIFACT_CHANGE")
        self.assertEqual(load(self.project_path).slides["P01"].status, "ready")  # untouched

        apply_data = self.client.post("/api/runtime/resume/apply").get_json()
        self.assertEqual(len(apply_data["reconciliation"]), 1)
        self.assertEqual(load(self.project_path).slides["P01"].status, "dirty")  # now applied

    def test_stop_check_route_reflects_real_incompleteness(self) -> None:
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        data = self.client.get("/api/runtime/stop-check").get_json()
        self.assertEqual(data["decision"], "block")
        self.assertEqual(data["code"], "BUILD_INCOMPLETE")

    def test_active_slide_round_trips(self) -> None:
        resp = self.client.post("/api/runtime/active-slide", json={"slide_id": "P03"})
        self.assertEqual(resp.get_json()["active_slide"], "P03")


if __name__ == "__main__":
    unittest.main()
