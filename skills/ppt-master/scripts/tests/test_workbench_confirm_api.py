#!/usr/bin/env python3
"""Tests for scripts/workbench/confirm_api.py (P5) -- real confirm_ui reuse, no subprocess."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from flask import Flask  # noqa: E402

from workbench.confirm_api import register  # noqa: E402

_STAGE1_OPTIONS = {
    "schema_version": 1, "phase": "template",
    "default_mode": "free_design", "explicit_workspace_roots": [],
}
_STAGE1_RECOMMENDATION = {
    "stage": "stage1",
    "primary_language": "en-US",
    "recommend": {"canvas": "ppt169"},
    "audience": {"value": "test audience"},
    "communication_intent": {"value": "test intent"},
    "audience_outcome": {"value": ""},
    "core_message": {"value": ""},
    "delivery_context": {"value": ""},
    "artifact_afterlife": {"value": ""},
    "content_divergence": {"value": ""},
}
_VALID_STAGE1_SUBMISSION = {
    "stage": "stage1",
    "primary_language": "en-US",
    "canvas": "ppt169",
    "audience": "test audience",
    "communication_intent": "test intent",
    "audience_outcome": "", "core_message": "", "delivery_context": "",
    "artifact_afterlife": "", "content_divergence": "",
    "template_selection": {"mode": "free_design", "selection_keys": []},
}


class ConfirmApiProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        self.confirm_dir = self.project_path / "confirm_ui"
        self.confirm_dir.mkdir()
        app = Flask(__name__)
        register(app, self.project_path)
        self.client = app.test_client()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_state_reports_real_error_when_no_files_authored_yet(self) -> None:
        # No template_options.json / recommendations.stage1.json yet -- this
        # must come back as confirm_ui's own real "not found" response, not a
        # stub -- proving the proxy actually reaches confirm_ui's own logic.
        resp = self.client.get("/api/confirm/state")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("recommendations.stage1.json", resp.get_json()["error"])

    def test_state_reflects_a_real_authored_stage1_recommendation(self) -> None:
        (self.confirm_dir / "template_options.json").write_text(json.dumps(_STAGE1_OPTIONS), encoding="utf-8")
        (self.confirm_dir / "recommendations.stage1.json").write_text(json.dumps(_STAGE1_RECOMMENDATION), encoding="utf-8")

        resp = self.client.get("/api/confirm/state")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["stage"], "stage1")
        self.assertEqual(data["audience"]["value"], "test audience")
        self.assertIn("template_options", data)  # real library catalog folded in

    def test_submit_rejects_an_incomplete_payload_with_confirm_uis_real_validation(self) -> None:
        (self.confirm_dir / "template_options.json").write_text(json.dumps(_STAGE1_OPTIONS), encoding="utf-8")
        (self.confirm_dir / "recommendations.stage1.json").write_text(json.dumps(_STAGE1_RECOMMENDATION), encoding="utf-8")

        resp = self.client.post("/api/confirm/submit", json={"stage": "stage1", "audience": "x"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("template_selection", resp.get_json()["error"])
        self.assertFalse((self.confirm_dir / "result.json").is_file())

    def test_submit_writes_real_result_and_template_selection_on_success(self) -> None:
        (self.confirm_dir / "template_options.json").write_text(json.dumps(_STAGE1_OPTIONS), encoding="utf-8")
        (self.confirm_dir / "recommendations.stage1.json").write_text(json.dumps(_STAGE1_RECOMMENDATION), encoding="utf-8")

        resp = self.client.post("/api/confirm/submit", json=_VALID_STAGE1_SUBMISSION)
        self.assertEqual(resp.status_code, 200)

        result = json.loads((self.confirm_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "stage1-confirmed")
        self.assertEqual(result["audience"], "test audience")

        selection = json.loads((self.confirm_dir / "template_selection.json").read_text(encoding="utf-8"))
        self.assertEqual(selection["mode"], "free_design")
        self.assertEqual(selection["status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
