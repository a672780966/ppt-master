#!/usr/bin/env python3
"""Tests for scripts/runtime/edit_plan.py (P6) -- EditPlan schema/scope/allowlist validation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime.edit_plan import EditPlanError, validate_edit_plan  # noqa: E402

_CURRENT_IDS = {"title-01", "chart-02", "other-99"}


def _plan(**overrides):
    base = {
        "scope": "selection",
        "base_revision": 8,
        "summary": "test",
        "operations": [{"type": "set_text", "target": "title-01", "value": "hi"}],
    }
    base.update(overrides)
    return base


class ShapeValidationTests(unittest.TestCase):
    def test_valid_plan_returns_operations(self) -> None:
        ops = validate_edit_plan(_plan(), scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)
        self.assertEqual(len(ops), 1)

    def test_non_dict_plan_rejected(self) -> None:
        with self.assertRaises(EditPlanError) as ctx:
            validate_edit_plan("not a dict", scope="selection", selection_ids=[], current_ids=_CURRENT_IDS)
        self.assertEqual(ctx.exception.code, "INVALID_EDIT_PLAN")

    def test_missing_top_level_field_rejected(self) -> None:
        plan = _plan()
        del plan["base_revision"]
        with self.assertRaises(EditPlanError) as ctx:
            validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)
        self.assertEqual(ctx.exception.code, "INVALID_EDIT_PLAN")

    def test_scope_mismatch_between_plan_and_job_rejected(self) -> None:
        with self.assertRaises(EditPlanError) as ctx:
            validate_edit_plan(_plan(scope="slide"), scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)
        self.assertEqual(ctx.exception.code, "INVALID_EDIT_PLAN")

    def test_empty_operations_rejected(self) -> None:
        with self.assertRaises(EditPlanError) as ctx:
            validate_edit_plan(_plan(operations=[]), scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)
        self.assertEqual(ctx.exception.code, "INVALID_EDIT_PLAN")

    def test_unknown_operation_type_rejected(self) -> None:
        plan = _plan(operations=[{"type": "delete_everything", "target": "title-01"}])
        with self.assertRaises(EditPlanError) as ctx:
            validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)
        self.assertEqual(ctx.exception.code, "INVALID_EDIT_PLAN")

    def test_operation_missing_required_field_rejected(self) -> None:
        plan = _plan(operations=[{"type": "translate", "target": "title-01", "dx": 1}])  # missing dy
        with self.assertRaises(EditPlanError) as ctx:
            validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)
        self.assertEqual(ctx.exception.code, "INVALID_EDIT_PLAN")


class ScopeValidationTests(unittest.TestCase):
    def test_target_outside_selection_rejected_as_out_of_scope(self) -> None:
        plan = _plan(operations=[{"type": "delete_element", "target": "chart-02"}])
        with self.assertRaises(EditPlanError) as ctx:
            validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)
        self.assertEqual(ctx.exception.code, "OUT_OF_SCOPE_EDIT")
        self.assertEqual(ctx.exception.details["target"], "chart-02")

    def test_slide_scope_allows_any_existing_target(self) -> None:
        plan = _plan(scope="slide", operations=[{"type": "delete_element", "target": "chart-02"}])
        ops = validate_edit_plan(plan, scope="slide", selection_ids=[], current_ids=_CURRENT_IDS)
        self.assertEqual(len(ops), 1)

    def test_insert_fragment_scope_checked_against_parent_not_target(self) -> None:
        plan = _plan(operations=[{"type": "insert_fragment", "parent": "title-01", "fragment": "<g/>"}])
        ops = validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)
        self.assertEqual(len(ops), 1)

    def test_target_never_existed_is_target_not_found(self) -> None:
        plan = _plan(scope="element", operations=[{"type": "set_text", "target": "ghost-id", "value": "x"}])
        with self.assertRaises(EditPlanError) as ctx:
            validate_edit_plan(plan, scope="element", selection_ids=["ghost-id"], current_ids=_CURRENT_IDS)
        self.assertEqual(ctx.exception.code, "SELECTION_NO_LONGER_EXISTS")


class SemanticToolAllowlistTests(unittest.TestCase):
    def test_allowed_tool_passes(self) -> None:
        plan = _plan(operations=[{"type": "semantic_tool", "target": "title-01", "tool": "chart.create", "arguments": {}}])
        ops = validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)
        self.assertEqual(len(ops), 1)

    def test_disallowed_tool_rejected(self) -> None:
        plan = _plan(operations=[{"type": "semantic_tool", "target": "title-01", "tool": "deck.export", "arguments": {}}])
        with self.assertRaises(EditPlanError) as ctx:
            validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)
        self.assertEqual(ctx.exception.code, "INVALID_EDIT_PLAN")

    def test_arbitrary_shell_style_tool_name_rejected(self) -> None:
        plan = _plan(operations=[{"type": "semantic_tool", "target": "title-01", "tool": "arbitrary_shell", "arguments": {}}])
        with self.assertRaises(EditPlanError):
            validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)


if __name__ == "__main__":
    unittest.main()
