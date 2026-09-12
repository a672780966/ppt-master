#!/usr/bin/env python3
"""Tests for scripts/runtime/revisions.py, controller.py, jobs.py, resume.py (P1)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime import controller, jobs as job_ops  # noqa: E402
from runtime.build_state import BuildStateError, load, save  # noqa: E402
from runtime.resume import resume_report  # noqa: E402
from runtime.revisions import bump_plan_revision, submit_slide  # noqa: E402
from runtime.build_state import StaleEditError  # noqa: E402

BUILD_STATE_CLI = SCRIPTS_DIR / "build_state.py"


class RevisionControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        controller.init_state(self.project_path, route="quick")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_submit_slide_success_bumps_revision_content_hash_and_export_dirty(self) -> None:
        controller.set_export(self.project_path, dirty=False)
        slide = submit_slide(
            self.project_path,
            "P01",
            expected_revision=0,
            status="ready",
            content_hash="sha256:" + "a" * 64,
        )
        self.assertEqual(slide.revision, 1)
        self.assertEqual(slide.status, "ready")
        self.assertFalse(slide.dirty)
        state = load(self.project_path)
        self.assertTrue(state.export_dirty)

    def test_submit_slide_stale_edit_rejected_and_file_unmodified(self) -> None:
        submit_slide(self.project_path, "P01", expected_revision=0, status="building")
        before = (self.project_path / "build_state.json").read_bytes()
        with self.assertRaises(StaleEditError) as ctx:
            submit_slide(self.project_path, "P01", expected_revision=0, status="ready")
        self.assertEqual(ctx.exception.expected, 0)
        self.assertEqual(ctx.exception.actual, 1)
        after = (self.project_path / "build_state.json").read_bytes()
        self.assertEqual(before, after)

    def test_submit_slide_cli_exits_3_on_stale_edit(self) -> None:
        submit_slide(self.project_path, "P01", expected_revision=0, status="building")
        result = subprocess.run(
            [
                sys.executable, str(BUILD_STATE_CLI), "submit-slide",
                str(self.project_path), "P01", "--expected-revision", "0",
            ],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("STALE_EDIT", result.stdout)

    def test_bump_plan_revision_cascades_ready_dirty_building_but_not_planned(self) -> None:
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready")
        submit_slide(self.project_path, "P02", expected_revision=0, status="dirty")
        submit_slide(self.project_path, "P03", expected_revision=0, status="building")

        from runtime.build_state import SlideState

        state = load(self.project_path)
        state.slides["P04"] = SlideState(status="planned")
        save(self.project_path, state)

        affected = bump_plan_revision(self.project_path)
        self.assertEqual(set(affected), {"P01", "P02", "P03"})
        state = load(self.project_path)
        self.assertEqual(state.slides["P01"].status, "stale")
        self.assertEqual(state.slides["P02"].status, "stale")
        self.assertEqual(state.slides["P03"].status, "stale")
        self.assertEqual(state.slides["P04"].status, "planned")

    def test_bump_plan_revision_is_idempotent_on_already_stale_slides(self) -> None:
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready")
        bump_plan_revision(self.project_path)
        affected_again = bump_plan_revision(self.project_path)
        self.assertEqual(affected_again, [])
        state = load(self.project_path)
        self.assertEqual(state.slides["P01"].status, "stale")


class ResumeReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_resume_missing_build_state_reports_legacy(self) -> None:
        report = resume_report(self.project_path)
        self.assertEqual(report, {"mode": "legacy", "reason": "no build_state.json"})

    def test_resume_reports_next_planned_or_stale_slide_in_page_order(self) -> None:
        controller.init_state(self.project_path, route="default", pages=["P02", "P01"])
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready")
        report = resume_report(self.project_path)
        self.assertEqual(report["next_action"], "author_slide")
        self.assertEqual(report["slide"], "P02")

    def test_resume_reports_run_final_gate_when_all_ready_but_gate_pending(self) -> None:
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready")
        report = resume_report(self.project_path)
        self.assertEqual(report["next_action"], "run_final_gate")

    def test_resume_reports_export_when_gates_passed_and_export_dirty(self) -> None:
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready")
        controller.set_gate(self.project_path, "final", "passed")
        report = resume_report(self.project_path)
        self.assertEqual(report["next_action"], "export")

    def test_resume_reports_nothing_pending_when_fully_exported(self) -> None:
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready")
        controller.set_gate(self.project_path, "final", "passed")
        controller.set_export(self.project_path, dirty=False, last_export="exports/x.pptx")
        report = resume_report(self.project_path)
        self.assertEqual(report["next_action"], "nothing_pending")

    def test_resume_reports_queued_job_when_slides_and_gates_and_export_are_clear(self) -> None:
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready")
        controller.set_gate(self.project_path, "final", "passed")
        controller.set_export(self.project_path, dirty=False)
        job_ops.enqueue(self.project_path, job_type="ai-edit", slide="P01", note="tweak title")
        report = resume_report(self.project_path)
        self.assertEqual(report["next_action"], "process_job")
        self.assertEqual(report["job"]["slide"], "P01")


class JobQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        controller.init_state(self.project_path, route="quick")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_enqueue_dequeue_is_fifo(self) -> None:
        first = job_ops.enqueue(self.project_path, job_type="ai-edit", slide="P01")
        job_ops.enqueue(self.project_path, job_type="direct-edit", slide="P02")
        oldest = job_ops.dequeue(self.project_path)
        self.assertEqual(oldest.id, first.id)

    def test_done_and_reject_transition_status_correctly(self) -> None:
        job = job_ops.enqueue(self.project_path, job_type="regen", slide="P01")
        job_ops.mark_done(self.project_path, job.id)
        self.assertEqual(job_ops.list_jobs(self.project_path, status="done")[0].id, job.id)

        job2 = job_ops.enqueue(self.project_path, job_type="regen", slide="P02")
        job_ops.mark_rejected(self.project_path, job2.id, reason="conflict")
        rejected = job_ops.list_jobs(self.project_path, status="rejected")[0]
        self.assertEqual(rejected.id, job2.id)
        self.assertEqual(rejected.note, "conflict")

    def test_enqueue_invalid_type_raises(self) -> None:
        with self.assertRaises(BuildStateError):
            job_ops.enqueue(self.project_path, job_type="not-a-real-type")


if __name__ == "__main__":
    unittest.main()
