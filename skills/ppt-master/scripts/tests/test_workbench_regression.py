#!/usr/bin/env python3
"""
Real-project + real-server regression tests for P5 Unified Workbench.

Mirrors the P4 convention (test_hooks_regression.py): reuse real projects
already on disk from prior phases rather than fabricating a new fixture,
and exercise the actual Flask apps (svg_editor.server.create_app() for
Case D, workbench.runtime_api/confirm_api for the rest) through their test
clients -- no mocking of business logic anywhere in this file.

Case coverage (P5 plan SS48, letters match):
  A - load a pre-P4 real project through the Workbench cleanly
  C - real edit-then-export-blocked-then-validated-then-export-succeeds,
      through the HTTP-shaped API this time (P4's own regression proved the
      same flow at the CLI layer)
  D - a real direct edit through svg_editor's actual /api/slide/.../edit +
      /api/save-all closes the loop into build_state.json (the one new
      piece of runtime logic P5 adds)
  F - shadow mode via the real P3 fixture project (already hooks.mode:
      shadow from the P4 regression)
  G - enforce mode via the real P2 project (already hooks.mode: enforce)
Cases B and E are already covered as real (if synthetic-project) API
integration tests in test_workbench_runtime_api.py and are not repeated
here.
"""

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
from svg_editor.server import create_app as create_editor_app  # noqa: E402
from tools.dispatch import invoke  # noqa: E402
from workbench.runtime_api import register as register_runtime_api  # noqa: E402

_REPO_ROOT = SCRIPTS_DIR.parents[2]
_P2_PROJECT = _REPO_ROOT / "projects" / "text-teaching-p2-default_ppt169_20260912"
_P3_PROJECT = _REPO_ROOT / "projects" / "semantic-tools-p3-fixture_20260912"


def _runtime_client(project_path: Path):
    app = Flask(__name__)
    register_runtime_api(app, project_path)
    return app.test_client()


@unittest.skipUnless(_P2_PROJECT.is_dir(), "real P2 text-teaching project not present on this machine")
class CaseA_LoadPreP4ProjectTests(unittest.TestCase):
    def test_workbench_loads_the_real_default_project_cleanly(self) -> None:
        client = _runtime_client(_P2_PROJECT)
        project = client.get("/api/runtime/project").get_json()
        self.assertTrue(project["ok"])
        self.assertEqual(project["mode"], "build-state")
        self.assertEqual(project["route"], "default")

        slides = client.get("/api/runtime/slides").get_json()
        self.assertTrue(slides["ok"])
        self.assertEqual(len(slides["slides"]), 9)


@unittest.skipUnless(_P2_PROJECT.is_dir(), "real P2 text-teaching project not present on this machine")
class CaseC_G_EditBlockValidateExportTests(unittest.TestCase):
    """Reuses the real, already fully-adopted (hooks.mode: enforce) P2
    project -- same slide (P05) the P4 CLI regression exercised, this time
    driven through the HTTP API layer instead of the CLI."""

    def test_stale_validation_blocks_then_clears_through_the_api(self) -> None:
        client = _runtime_client(_P2_PROJECT)

        state = load(_P2_PROJECT)
        self.assertEqual(state.hooks_mode, "enforce")  # left this way by the P4 regression
        before = state.slides["P05"].revision

        submit_slide(_P2_PROJECT, "P05", expected_revision=before, status="ready")

        slides = client.get("/api/runtime/slides").get_json()["slides"]
        p05 = next(s for s in slides if s["id"] == "P05")
        self.assertEqual(p05["display_label"], "Needs validation")

        export_resp = client.post("/api/runtime/deck/export", json={"no_notes": True}).get_json()
        self.assertFalse(export_resp["ok"])
        self.assertEqual(export_resp["errors"][0]["code"], "DECK_NOT_EXPORTABLE")
        reasons = export_resp["errors"][0]["reasons"]
        self.assertTrue(any(r["slide"] == "P05" for r in reasons if r["type"] == "stale_validation"))

        validate_resp = client.post("/api/runtime/slides/P05/validate").get_json()
        self.assertTrue(validate_resp["ok"])

        export_resp2 = client.post("/api/runtime/deck/export", json={"no_notes": True}).get_json()
        self.assertTrue(export_resp2["ok"])


@unittest.skipUnless(_P3_PROJECT.is_dir(), "real P3 semantic-tools fixture project not present on this machine")
class CaseF_ShadowModeTests(unittest.TestCase):
    def test_shadow_mode_export_runs_with_a_warning_via_the_api(self) -> None:
        state = load(_P3_PROJECT)
        self.assertEqual(state.hooks_mode, "shadow")  # left this way by the P4 regression
        # Create a real staleness condition explicitly rather than relying
        # on whatever state other real-project regressions (P6's included)
        # happened to leave behind -- this test must be self-sufficient.
        submit_slide(_P3_PROJECT, "P01", expected_revision=state.slides["P01"].revision, status="ready")
        try:
            client = _runtime_client(_P3_PROJECT)
            resp = client.post("/api/runtime/deck/export", json={
                "quick_generate": True, "no_notes": True, "native_charts_and_tables": True,
            }).get_json()
            self.assertTrue(resp["ok"])
            self.assertTrue(any("would block" in w for w in resp.get("warnings", [])))
        finally:
            # Leave the shared real project validated + exported again so
            # other tests/regressions that reuse it see a clean state.
            invoke("slide.validate", {"project": str(_P3_PROJECT), "stage": "final", "quick_generate": True})
            invoke("deck.export", {
                "project": str(_P3_PROJECT), "quick_generate": True, "no_notes": True, "native_charts_and_tables": True,
            })


class CaseD_DirectEditRevisionWiringTests(unittest.TestCase):
    """The one genuinely new runtime-logic path P5 adds: a real direct edit
    through the real svg_editor Flask app must be visible in build_state.json
    afterward -- SS19/SS20's exact requirement."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        (self.project_path / "svg_output").mkdir()
        self.svg_file = self.project_path / "svg_output" / "01_cover.svg"
        self.svg_file.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
            '<rect id="box1" x="10" y="10" width="100" height="50" fill="#000000"/>'
            "</svg>",
            encoding="utf-8",
        )
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready", from_file=self.svg_file)
        with_locked = load(self.project_path)
        with_locked.slides["P01"].validated_revision = 1
        from runtime.build_state import save
        save(self.project_path, with_locked)

        app = create_editor_app(str(self.project_path), idle_timeout=0, live=True, lock_file=None)
        self.client = app.test_client()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_real_direct_edit_and_save_all_bumps_revision_and_stales_validation(self) -> None:
        before = load(self.project_path)
        self.assertEqual(before.slides["P01"].revision, 1)
        self.assertEqual(before.slides["P01"].validated_revision, 1)

        edit_resp = self.client.post(
            "/api/slide/01_cover.svg/edit",
            json={"element_id": "box1", "attrs": {"fill": "#ff0000"}},
        )
        self.assertEqual(edit_resp.status_code, 200)

        save_resp = self.client.post("/api/save-all")
        self.assertEqual(save_resp.status_code, 200)
        save_data = save_resp.get_json()
        self.assertIn("01_cover.svg", save_data.get("files_modified", []))

        after = load(self.project_path)
        self.assertEqual(after.slides["P01"].revision, 2)
        self.assertEqual(after.slides["P01"].status, "dirty")
        self.assertTrue(after.slides["P01"].dirty)
        self.assertNotEqual(after.slides["P01"].revision, after.slides["P01"].validated_revision)
        self.assertTrue(after.export_dirty)

        # And the actual SVG content really changed on disk.
        self.assertIn("#ff0000", self.svg_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
