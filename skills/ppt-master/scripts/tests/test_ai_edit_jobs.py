#!/usr/bin/env python3
"""Tests for scripts/runtime/ai_edit_jobs.py (P6) -- the AI Edit job lifecycle orchestrator."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime import ai_edit_jobs as jobs  # noqa: E402
from runtime import controller  # noqa: E402
from runtime.build_state import load  # noqa: E402
from runtime.revisions import submit_slide  # noqa: E402

_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" data-pptx-page-role="content" font-family="Arial">
  <g id="title-group" data-pptx-bounds="50 60 400 80"><text id="title-01" y="100" x="50">Old Title</text></g>
  <g id="shape-01" data-pptx-bounds="0 0 10 10"><rect id="inner-rect" x="0" y="0" width="1" height="1"/></g>
</svg>"""


def _valid_plan(base_revision: int) -> dict:
    return {
        "scope": "selection", "base_revision": base_revision, "summary": "shorten title",
        "operations": [{"type": "set_text", "target": "title-01", "value": "New Title"}],
    }


class AIEditJobsTestCase(unittest.TestCase):
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

    def _create(self, **overrides):
        params = dict(slide_id="P01", scope="selection", selection_ids=["title-01"], instruction="shorten it")
        params.update(overrides)
        return jobs.create_job(self.project_path, **params)


class CreateJobTests(AIEditJobsTestCase):
    def test_creates_queued_job_with_persisted_context(self) -> None:
        job = self._create()
        self.assertEqual(job.status, "queued")
        self.assertEqual(job.base_revision, 1)
        self.assertTrue((job.dir_path() / "context.json").is_file())
        self.assertTrue((job.dir_path() / "request.json").is_file())
        state = load(self.project_path)
        self.assertEqual(state.slides["P01"].ai_edit_active_job, job.job_id)

    def test_second_job_on_busy_slide_is_rejected(self) -> None:
        self._create()
        with self.assertRaises(jobs.AIEditJobError) as ctx:
            self._create()
        self.assertEqual(ctx.exception.code, "SLIDE_AI_EDIT_BUSY")

    def test_get_context_returns_the_persisted_compiled_facts(self) -> None:
        job = self._create()
        context = jobs.get_context(self.project_path, job.job_id)
        self.assertEqual(context["slide_id"], "P01")
        self.assertEqual(context["object_facts"][0]["id"], "title-01")


class SubmitPlanHappyPathTests(AIEditJobsTestCase):
    def test_valid_plan_completes_and_bumps_revision(self) -> None:
        job = self._create()
        result_job = jobs.submit_plan(self.project_path, job.job_id, _valid_plan(1))
        self.assertEqual(result_job.status, "completed")
        self.assertEqual(result_job.result["new_revision"], 2)
        self.assertIn("New Title", self.svg_path.read_text(encoding="utf-8"))
        state = load(self.project_path)
        self.assertIsNone(state.slides["P01"].ai_edit_active_job)
        self.assertEqual(state.slides["P01"].ai_edit_last_job, job.job_id)

    def test_invalid_plan_fails_with_zero_writes(self) -> None:
        job = self._create()
        bad_plan = {"scope": "selection", "base_revision": 1, "operations": []}
        with self.assertRaises(jobs.AIEditJobError) as ctx:
            jobs.submit_plan(self.project_path, job.job_id, bad_plan)
        self.assertEqual(ctx.exception.code, "INVALID_EDIT_PLAN")
        self.assertIn("Old Title", self.svg_path.read_text(encoding="utf-8"))
        self.assertEqual(load(self.project_path).slides["P01"].revision, 1)
        self.assertEqual(jobs.load_job(self.project_path, job.job_id).status, "failed")

    def test_out_of_scope_plan_fails(self) -> None:
        job = self._create()
        plan = _valid_plan(1)
        plan["operations"] = [{"type": "delete_element", "target": "shape-01"}]
        with self.assertRaises(jobs.AIEditJobError) as ctx:
            jobs.submit_plan(self.project_path, job.job_id, plan)
        self.assertEqual(ctx.exception.code, "OUT_OF_SCOPE_EDIT")


class ConflictControlTests(AIEditJobsTestCase):
    def test_single_conflict_rebases_once_and_requires_fresh_plan(self) -> None:
        job = self._create()
        # Someone else edits the slide while the "model" is thinking.
        submit_slide(self.project_path, "P01", expected_revision=1, status="ready")

        with self.assertRaises(jobs.AIEditJobError) as ctx:
            jobs.submit_plan(self.project_path, job.job_id, _valid_plan(1))
        self.assertEqual(ctx.exception.code, "STALE_EDIT")

        rebased = jobs.load_job(self.project_path, job.job_id)
        self.assertEqual(rebased.status, "queued")
        self.assertEqual(rebased.rebase_count, 1)
        self.assertEqual(rebased.base_revision, 2)

        # A fresh plan against the rebased revision succeeds.
        result_job = jobs.submit_plan(self.project_path, job.job_id, _valid_plan(2))
        self.assertEqual(result_job.status, "completed")

    def test_second_conflict_after_rebase_stops_with_zero_writes(self) -> None:
        job = self._create()
        submit_slide(self.project_path, "P01", expected_revision=1, status="ready")
        with self.assertRaises(jobs.AIEditJobError):
            jobs.submit_plan(self.project_path, job.job_id, _valid_plan(1))  # triggers rebase to revision 2

        submit_slide(self.project_path, "P01", expected_revision=2, status="ready")  # conflict again
        with self.assertRaises(jobs.AIEditJobError) as ctx:
            jobs.submit_plan(self.project_path, job.job_id, _valid_plan(2))
        self.assertEqual(ctx.exception.code, "EDIT_CONFLICT_REQUIRES_RETRY")
        self.assertEqual(jobs.load_job(self.project_path, job.job_id).status, "conflicted")
        self.assertIn("Old Title", self.svg_path.read_text(encoding="utf-8"))  # never applied

    def test_selection_disappearing_during_rebase_is_reported(self) -> None:
        job = self._create()  # selection_ids=["title-01"]
        # Someone else deletes the selected element directly (not through
        # the AI Edit job system) while the "model" is thinking.
        import xml.etree.ElementTree as ET
        from runtime.svg_tree import find_with_parent
        root = ET.parse(str(self.svg_path)).getroot()
        elem, parent = find_with_parent(root, "title-01")
        parent.remove(elem)
        self.svg_path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
        submit_slide(self.project_path, "P01", expected_revision=1, status="ready", from_file=self.svg_path)

        with self.assertRaises(jobs.AIEditJobError) as ctx:
            jobs.submit_plan(self.project_path, job.job_id, _valid_plan(1))
        self.assertEqual(ctx.exception.code, "SELECTION_NO_LONGER_EXISTS")


class CancelRetryUndoTests(AIEditJobsTestCase):
    def test_cancel_queued_job_then_reject_submission(self) -> None:
        job = self._create()
        cancelled = jobs.cancel_job(self.project_path, job.job_id)
        self.assertEqual(cancelled.status, "cancelled")
        with self.assertRaises(jobs.AIEditJobError) as ctx:
            jobs.submit_plan(self.project_path, job.job_id, _valid_plan(1))
        self.assertEqual(ctx.exception.code, "AI_EDIT_CANCELLED")

    def test_retry_spawns_a_fresh_job_from_a_failed_one(self) -> None:
        job = self._create()
        with self.assertRaises(jobs.AIEditJobError):
            jobs.submit_plan(self.project_path, job.job_id, {"scope": "selection", "base_revision": 1, "operations": []})
        retried = jobs.retry_job(self.project_path, job.job_id)
        self.assertNotEqual(retried.job_id, job.job_id)
        self.assertEqual(retried.status, "queued")

    def test_undo_restores_content_and_revision_never_decreases(self) -> None:
        job = self._create()
        jobs.submit_plan(self.project_path, job.job_id, _valid_plan(1))
        self.assertEqual(load(self.project_path).slides["P01"].revision, 2)

        undone = jobs.undo_job(self.project_path, job.job_id)
        self.assertIn("Old Title", self.svg_path.read_text(encoding="utf-8"))
        after = load(self.project_path)
        self.assertEqual(after.slides["P01"].revision, 3)  # forward, never back to 1
        self.assertGreater(undone.result["undo_revision"], 2)

    def test_undo_conflict_when_slide_moved_on_since_the_ai_edit(self) -> None:
        job = self._create()
        jobs.submit_plan(self.project_path, job.job_id, _valid_plan(1))
        submit_slide(self.project_path, "P01", expected_revision=2, status="ready")  # user edits again
        with self.assertRaises(jobs.AIEditJobError) as ctx:
            jobs.undo_job(self.project_path, job.job_id)
        self.assertEqual(ctx.exception.code, "UNDO_CONFLICT")


class ReconcileInterruptedJobsTests(AIEditJobsTestCase):
    def test_active_status_job_is_flipped_to_interrupted_on_reconcile(self) -> None:
        job = self._create()
        loaded = jobs.load_job(self.project_path, job.job_id)
        loaded.status = "applying"  # simulate a crash mid-apply
        jobs._save_job(loaded)

        flipped = jobs.reconcile_interrupted_jobs(self.project_path)
        self.assertEqual(flipped, [job.job_id])
        self.assertEqual(jobs.load_job(self.project_path, job.job_id).status, "interrupted")
        self.assertIsNone(load(self.project_path).slides["P01"].ai_edit_active_job)

    def test_terminal_status_job_is_left_alone(self) -> None:
        job = self._create()
        jobs.submit_plan(self.project_path, job.job_id, _valid_plan(1))
        flipped = jobs.reconcile_interrupted_jobs(self.project_path)
        self.assertEqual(flipped, [])


class ListJobsTests(AIEditJobsTestCase):
    def test_filters_by_status(self) -> None:
        job = self._create()
        queued = jobs.list_jobs(self.project_path, status="queued")
        self.assertEqual([j.job_id for j in queued], [job.job_id])
        self.assertEqual(jobs.list_jobs(self.project_path, status="completed"), [])


if __name__ == "__main__":
    unittest.main()
