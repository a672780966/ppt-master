#!/usr/bin/env python3
"""Tests for scripts/workbench/ai_edit_api.py (P6) -- Flask routes over runtime.ai_edit_jobs."""

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
from runtime.revisions import submit_slide  # noqa: E402
from workbench.ai_edit_api import register  # noqa: E402

_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" data-pptx-page-role="content" font-family="Arial">
  <g id="title-group" data-pptx-bounds="50 60 400 80"><text id="title-01" y="100" x="50">Old Title</text></g>
</svg>"""


class AIEditApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        svg_dir = self.project_path / "svg_output"
        svg_dir.mkdir()
        self.svg_path = svg_dir / "01_cover.svg"
        self.svg_path.write_text(_SVG, encoding="utf-8")
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready", from_file=self.svg_path)

        app = Flask(__name__)
        register(app, self.project_path)
        self.client = app.test_client()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_create_list_and_get_job(self) -> None:
        resp = self.client.post("/api/runtime/ai-edits", json={
            "slide_id": "P01", "scope": "selection", "selection_ids": ["title-01"], "instruction": "shorten it",
        })
        data = resp.get_json()
        self.assertTrue(data["ok"])
        job_id = data["job"]["job_id"]
        self.assertEqual(data["job"]["status"], "queued")

        listing = self.client.get("/api/runtime/ai-edits?slide=P01").get_json()
        self.assertEqual(len(listing["jobs"]), 1)

        detail = self.client.get(f"/api/runtime/ai-edits/{job_id}").get_json()
        self.assertEqual(detail["job"]["job_id"], job_id)

    def test_create_on_busy_slide_returns_error_envelope(self) -> None:
        payload = {"slide_id": "P01", "scope": "selection", "selection_ids": ["title-01"], "instruction": "x"}
        self.client.post("/api/runtime/ai-edits", json=payload)
        resp = self.client.post("/api/runtime/ai-edits", json=payload)
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["errors"][0]["code"], "SLIDE_AI_EDIT_BUSY")

    def test_context_endpoint_returns_compiled_facts(self) -> None:
        create = self.client.post("/api/runtime/ai-edits", json={
            "slide_id": "P01", "scope": "selection", "selection_ids": ["title-01"], "instruction": "x",
        }).get_json()
        job_id = create["job"]["job_id"]
        ctx = self.client.get(f"/api/runtime/ai-edits/{job_id}/context").get_json()
        self.assertTrue(ctx["ok"])
        self.assertEqual(ctx["context"]["object_facts"][0]["id"], "title-01")

    def test_full_round_trip_through_the_api(self) -> None:
        create = self.client.post("/api/runtime/ai-edits", json={
            "slide_id": "P01", "scope": "selection", "selection_ids": ["title-01"], "instruction": "shorten it",
        }).get_json()
        job_id = create["job"]["job_id"]

        plan = {
            "scope": "selection", "base_revision": 1, "summary": "shorten",
            "operations": [{"type": "set_text", "target": "title-01", "value": "New"}],
        }
        submit = self.client.post(f"/api/runtime/ai-edits/{job_id}/plan", json=plan).get_json()
        self.assertTrue(submit["ok"])
        self.assertEqual(submit["job"]["status"], "completed")
        self.assertIn("New", self.svg_path.read_text(encoding="utf-8"))

        undo = self.client.post(f"/api/runtime/ai-edits/{job_id}/undo").get_json()
        self.assertTrue(undo["ok"])
        self.assertIn("Old Title", self.svg_path.read_text(encoding="utf-8"))

    def test_cancel_then_retry_spawns_a_new_job(self) -> None:
        create = self.client.post("/api/runtime/ai-edits", json={
            "slide_id": "P01", "scope": "selection", "selection_ids": ["title-01"], "instruction": "x",
        }).get_json()
        job_id = create["job"]["job_id"]
        cancel = self.client.post(f"/api/runtime/ai-edits/{job_id}/cancel").get_json()
        self.assertEqual(cancel["job"]["status"], "cancelled")
        retry = self.client.post(f"/api/runtime/ai-edits/{job_id}/retry").get_json()
        self.assertTrue(retry["ok"])
        self.assertNotEqual(retry["job"]["job_id"], job_id)

    def test_schema_repair_retry_surfaces_through_the_api(self) -> None:
        create = self.client.post("/api/runtime/ai-edits", json={
            "slide_id": "P01", "scope": "selection", "selection_ids": ["title-01"], "instruction": "shorten it",
        }).get_json()
        job_id = create["job"]["job_id"]

        bad_plan = {"scope": "selection", "base_revision": 1, "operations": [
            {"type": "set_style", "target": "title-01", "style": "fill:red"}]}
        first = self.client.post(f"/api/runtime/ai-edits/{job_id}/plan", json=bad_plan).get_json()
        self.assertFalse(first["ok"])
        self.assertEqual(first["errors"][0]["code"], "INVALID_EDIT_PLAN")
        self.assertTrue(first["errors"][0]["retryable"])
        self.assertEqual(self.client.get(f"/api/runtime/ai-edits/{job_id}").get_json()["job"]["status"], "queued")

        good_plan = {"scope": "selection", "base_revision": 1, "summary": "fix", "operations": [
            {"type": "set_text", "target": "title-01", "value": "New"}]}
        second = self.client.post(f"/api/runtime/ai-edits/{job_id}/plan", json=good_plan).get_json()
        self.assertTrue(second["ok"])
        self.assertEqual(second["job"]["status"], "completed")
        self.assertIsNone(second["job"]["error"])

    def test_unknown_job_id_returns_error_envelope_not_a_500(self) -> None:
        resp = self.client.get("/api/runtime/ai-edits/does-not-exist")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["errors"][0]["code"], "TARGET_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
