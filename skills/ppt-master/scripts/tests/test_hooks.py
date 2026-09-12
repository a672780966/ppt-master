#!/usr/bin/env python3
"""Tests for scripts/runtime/{hook_types,hooks,hook_registry,hook_rules/*}.py (P4)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime import controller, hooks  # noqa: E402
from runtime.build_state import load  # noqa: E402
from runtime.hook_rules.export import post_deck_export, pre_deck_export  # noqa: E402
from runtime.hook_rules.resume import reconcile  # noqa: E402
from runtime.hook_rules.stop import stop_check  # noqa: E402
from runtime.hook_rules.validation import post_create_result, post_slide_validate  # noqa: E402
from runtime.hook_types import HookResult, allow, block, warn  # noqa: E402
from runtime.revisions import submit_slide  # noqa: E402
from tools.errors import ToolError  # noqa: E402


class PostDeckExportTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        controller.init_state(self.project_path, route="quick", pages=["P01"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_successful_export_auto_transitions_dirty_and_phase(self) -> None:
        post_deck_export(
            {"project": str(self.project_path)},
            {"ok": True, "pptx_path": "exports/x.pptx"},
        )
        state = load(self.project_path)
        self.assertFalse(state.export_dirty)
        self.assertEqual(state.last_export, "exports/x.pptx")
        self.assertEqual(state.phase, "exported")

    def test_failed_export_does_not_mutate_state(self) -> None:
        before = (self.project_path / "build_state.json").read_bytes()
        post_deck_export({"project": str(self.project_path)}, {"ok": False})
        after = (self.project_path / "build_state.json").read_bytes()
        self.assertEqual(before, after)


class HooksDispatchWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        controller.init_state(self.project_path, route="quick", pages=["P01"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_run_post_tool_hooks_dispatches_deck_export_with_payload_fields_signature(self) -> None:
        result = hooks.run_post_tool_hooks(
            "deck.export", {"project": str(self.project_path)}, {"ok": True, "pptx_path": "exports/x.pptx"},
        )
        self.assertEqual(result.decision, "allow")
        self.assertEqual(load(self.project_path).phase, "exported")


class HookResultTests(unittest.TestCase):
    def test_to_dict_omits_absent_optional_fields(self) -> None:
        result = allow("PreToolUse", "deck.export")
        self.assertEqual(result.to_dict(), {"hook": "PreToolUse", "tool": "deck.export", "decision": "allow"})

    def test_to_dict_includes_code_message_details_when_present(self) -> None:
        result = block("PreToolUse", "deck.export", "DECK_NOT_EXPORTABLE", "nope", reasons=[{"type": "x"}])
        payload = result.to_dict()
        self.assertEqual(payload["code"], "DECK_NOT_EXPORTABLE")
        self.assertEqual(payload["details"], {"reasons": [{"type": "x"}]})

    def test_non_allow_decision_requires_a_code(self) -> None:
        with self.assertRaises(ValueError):
            HookResult(hook="PreToolUse", tool="deck.export", decision="block")

    def test_invalid_decision_rejected(self) -> None:
        with self.assertRaises(ValueError):
            HookResult(hook="PreToolUse", tool="deck.export", decision="maybe")


class PreDeckExportRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        controller.init_state(self.project_path, route="quick", pages=["P01"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_fully_clean(self) -> None:
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready")
        controller.set_gate(self.project_path, "final", "passed")
        post_slide_validate({"project": str(self.project_path), "page": "01_cover.svg"}, {"ok": True, "errors": []}, [])

    def test_allow_on_a_fully_clean_state(self) -> None:
        self._make_fully_clean()
        result = pre_deck_export({"project": str(self.project_path)})
        self.assertEqual(result.decision, "allow")

    def test_no_build_state_always_allows(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            result = pre_deck_export({"project": other})
            self.assertEqual(result.decision, "allow")

    def test_stale_validation_reason_when_revision_bumped_without_revalidating(self) -> None:
        self._make_fully_clean()
        submit_slide(self.project_path, "P01", expected_revision=1, status="ready")
        result = pre_deck_export({"project": str(self.project_path)})
        self.assertEqual(result.decision, "block")
        self.assertEqual(result.code, "DECK_NOT_EXPORTABLE")
        types = [r["type"] for r in result.details["reasons"]]
        self.assertIn("stale_validation", types)

    def test_stale_slide_reason(self) -> None:
        self._make_fully_clean()
        submit_slide(self.project_path, "P01", expected_revision=1, status="stale")
        result = pre_deck_export({"project": str(self.project_path)})
        self.assertEqual(result.decision, "block")
        types = [r["type"] for r in result.details["reasons"]]
        self.assertIn("stale_slide", types)

    def test_final_gate_not_passed_reason(self) -> None:
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready")
        result = pre_deck_export({"project": str(self.project_path)})
        self.assertEqual(result.decision, "block")
        types = [r["type"] for r in result.details["reasons"]]
        self.assertIn("final_gate_not_passed", types)

    def test_pending_job_reason(self) -> None:
        from runtime import jobs as job_ops
        self._make_fully_clean()
        job_ops.enqueue(self.project_path, job_type="ai-edit", slide="P01")
        result = pre_deck_export({"project": str(self.project_path)})
        self.assertEqual(result.decision, "block")
        types = [r["type"] for r in result.details["reasons"]]
        self.assertIn("pending_job", types)


class ShadowEnforceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready")
        # deliberately never validated -> stale_validation reason -> blockable

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_shadow_mode_downgrades_block_to_warn_and_does_not_raise(self) -> None:
        state = load(self.project_path)
        self.assertEqual(state.hooks_mode, "shadow")
        result = hooks.run_pre_tool_hooks("deck.export", {"project": str(self.project_path)})
        self.assertEqual(result.decision, "warn")
        self.assertIn("shadow", result.message)

    def test_enforce_mode_raises_tool_error(self) -> None:
        controller.set_hooks_mode(self.project_path, "enforce")
        with self.assertRaises(ToolError) as ctx:
            hooks.run_pre_tool_hooks("deck.export", {"project": str(self.project_path)})
        self.assertEqual(ctx.exception.code, "DECK_NOT_EXPORTABLE")

    def test_unregistered_tool_always_allows(self) -> None:
        result = hooks.run_pre_tool_hooks("shape.create", {})
        self.assertEqual(result.decision, "allow")


class PostSlideValidateBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_binds_validated_revision_and_status(self) -> None:
        post_slide_validate(
            {"project": str(self.project_path), "page": "01_cover.svg"},
            {"ok": True, "errors": []}, [],
        )
        state = load(self.project_path)
        self.assertEqual(state.slides["P01"].validated_revision, 1)
        self.assertEqual(state.slides["P01"].validation_status, "passed")

    def test_passed_with_warnings_status(self) -> None:
        post_slide_validate(
            {"project": str(self.project_path), "page": "01_cover.svg"},
            {"ok": True, "errors": []}, ["TEXT_OVERFLOW: ..."],
        )
        state = load(self.project_path)
        self.assertEqual(state.slides["P01"].validation_status, "passed-with-warnings")

    def test_reapplying_identical_result_is_idempotent(self) -> None:
        payload = {"project": str(self.project_path), "page": "01_cover.svg"}
        fields = {"ok": True, "errors": []}
        post_slide_validate(payload, fields, [])
        first = (self.project_path / "build_state.json").read_bytes()
        post_slide_validate(payload, fields, [])
        second = (self.project_path / "build_state.json").read_bytes()
        # updated_at is the only field that could differ between the two
        # applications; strip it before comparing for true idempotency.
        first_json = json.loads(first)
        second_json = json.loads(second)
        for blob in (first_json, second_json):
            blob["slides"]["P01"]["updated_at"] = None
        self.assertEqual(first_json, second_json)

    def test_unparseable_page_name_skips_silently(self) -> None:
        result = post_slide_validate(
            {"project": str(self.project_path), "page": "cover.svg"},
            {"ok": True, "errors": []}, [],
        )
        self.assertEqual(result.decision, "allow")
        state = load(self.project_path)
        self.assertIsNone(state.slides["P01"].validated_revision)


class PostCreateResultContractTests(unittest.TestCase):
    def test_allow_when_all_fields_present(self) -> None:
        result = post_create_result("shape.create", {"artifact_id": "shape:x", "native_type": "shape", "svg_fragment": "<g/>"})
        self.assertEqual(result.decision, "allow")

    def test_warn_when_a_field_is_missing(self) -> None:
        result = post_create_result("chart.create", {"artifact_id": "chart:x", "native_type": "chart"})
        self.assertEqual(result.decision, "warn")
        self.assertEqual(result.code, "RESULT_CONTRACT_INCOMPLETE")

    def test_tool_without_a_registered_contract_always_allows(self) -> None:
        result = post_create_result("slide.validate", {})
        self.assertEqual(result.decision, "allow")


class StopCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        controller.init_state(self.project_path, route="quick", pages=["P01"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_legacy_project_always_allows(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            result = stop_check(Path(other))
            self.assertEqual(result["decision"], "allow")

    def test_blocks_with_build_incomplete_while_under_cap(self) -> None:
        result = stop_check(self.project_path, max_blocks=3)
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["code"], "BUILD_INCOMPLETE")
        self.assertEqual(result["attempts"], 1)

    def test_block_count_resets_on_allow(self) -> None:
        stop_check(self.project_path, max_blocks=3)
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready")
        controller.set_gate(self.project_path, "final", "passed")
        post_slide_validate({"project": str(self.project_path), "page": "01_cover.svg"}, {"ok": True, "errors": []}, [])
        controller.set_export(self.project_path, dirty=False)
        result = stop_check(self.project_path, max_blocks=3)
        self.assertEqual(result["decision"], "allow")
        self.assertEqual(load(self.project_path).stop_block_count, 0)

    def test_gives_up_after_max_blocks_consecutive_blocks(self) -> None:
        for _ in range(3):
            result = stop_check(self.project_path, max_blocks=2)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "QUALITY_GATE_UNRESOLVED")


class ResumeReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        controller.init_state(self.project_path, route="quick", pages=["P01"])
        self.svg_path = self.project_path / "svg_output" / "01_cover.svg"
        self.svg_path.parent.mkdir(parents=True, exist_ok=True)
        self.svg_path.write_text("<svg></svg>", encoding="utf-8")
        submit_slide(self.project_path, "P01", expected_revision=0, status="ready", from_file=self.svg_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_findings_on_a_consistent_project(self) -> None:
        self.assertEqual(reconcile(self.project_path), [])

    def test_external_artifact_change_detected_without_mutating_by_default(self) -> None:
        self.svg_path.write_text("<svg>edited by hand</svg>", encoding="utf-8")
        findings = reconcile(self.project_path)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], "EXTERNAL_ARTIFACT_CHANGE")
        state = load(self.project_path)
        self.assertEqual(state.slides["P01"].status, "ready")  # unmutated

    def test_apply_reconciliation_flips_drifted_slide_to_dirty(self) -> None:
        self.svg_path.write_text("<svg>edited by hand</svg>", encoding="utf-8")
        reconcile(self.project_path, apply=True)
        state = load(self.project_path)
        self.assertEqual(state.slides["P01"].status, "dirty")
        self.assertTrue(state.export_dirty)

    def test_artifact_missing_detected(self) -> None:
        self.svg_path.unlink()
        findings = reconcile(self.project_path)
        self.assertEqual(findings, [{"code": "ARTIFACT_MISSING", "slide": "P01", "path": "svg_output/01_cover.svg"}])


if __name__ == "__main__":
    unittest.main()
