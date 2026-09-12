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

    def test_base_revision_must_match_job_revision_when_supplied(self) -> None:
        with self.assertRaises(EditPlanError) as ctx:
            validate_edit_plan(
                _plan(base_revision=7),
                scope="selection",
                selection_ids=["title-01"],
                current_ids=_CURRENT_IDS,
                expected_base_revision=8,
            )
        self.assertEqual(ctx.exception.code, "INVALID_EDIT_PLAN")
        self.assertEqual(ctx.exception.details["plan_base_revision"], 7)
        self.assertEqual(ctx.exception.details["expected_base_revision"], 8)

    def test_bool_is_not_accepted_as_base_revision(self) -> None:
        with self.assertRaises(EditPlanError):
            validate_edit_plan(_plan(base_revision=True), scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)

    def test_summary_must_be_string_when_present(self) -> None:
        with self.assertRaises(EditPlanError):
            validate_edit_plan(_plan(summary={"not": "text"}), scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)


class OperationTypeValidationTests(unittest.TestCase):
    def test_set_text_requires_string_value(self) -> None:
        plan = _plan(operations=[{"type": "set_text", "target": "title-01", "value": 123}])
        with self.assertRaises(EditPlanError):
            validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)

    def test_set_style_requires_object(self) -> None:
        plan = _plan(operations=[{"type": "set_style", "target": "title-01", "style": "fill:red"}])
        with self.assertRaises(EditPlanError):
            validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)

    def test_set_style_rejects_nested_values(self) -> None:
        plan = _plan(operations=[{"type": "set_style", "target": "title-01", "style": {"fill": {"bad": True}}}])
        with self.assertRaises(EditPlanError):
            validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)

    def test_set_geometry_rejects_non_numeric_value(self) -> None:
        plan = _plan(operations=[{"type": "set_geometry", "target": "title-01", "geometry": {"x": "ten"}}])
        with self.assertRaises(EditPlanError):
            validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)

    def test_set_geometry_rejects_non_geometry_field(self) -> None:
        plan = _plan(operations=[{"type": "set_geometry", "target": "title-01", "geometry": {"fill": 12}}])
        with self.assertRaises(EditPlanError):
            validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)

    def test_translate_requires_numeric_delta(self) -> None:
        plan = _plan(operations=[{"type": "translate", "target": "title-01", "dx": "5", "dy": 1}])
        with self.assertRaises(EditPlanError):
            validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)

    def test_resize_requires_at_least_one_positive_dimension(self) -> None:
        for op in (
            {"type": "resize", "target": "title-01"},
            {"type": "resize", "target": "title-01", "width": 0},
            {"type": "resize", "target": "title-01", "height": -5},
        ):
            with self.subTest(op=op), self.assertRaises(EditPlanError):
                validate_edit_plan(_plan(operations=[op]), scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)

    def test_fragment_requires_string_markup(self) -> None:
        plan = _plan(operations=[{"type": "replace_fragment", "target": "title-01", "fragment": {}}])
        with self.assertRaises(EditPlanError):
            validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)

    def test_insert_index_must_be_non_negative_integer(self) -> None:
        plan = _plan(operations=[{"type": "insert_fragment", "parent": "title-01", "fragment": "<g/>", "index": -1}])
        with self.assertRaises(EditPlanError):
            validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)

    def test_semantic_tool_arguments_must_be_object(self) -> None:
        plan = _plan(operations=[{"type": "semantic_tool", "target": "title-01", "tool": "chart.create", "arguments": []}])
        with self.assertRaises(EditPlanError):
            validate_edit_plan(plan, scope="selection", selection_ids=["title-01"], current_ids=_CURRENT_IDS)


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
