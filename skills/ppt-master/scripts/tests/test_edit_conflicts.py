#!/usr/bin/env python3
"""Tests for scripts/runtime/edit_conflicts.py (P6) -- the revision-guard state machine."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime.edit_conflicts import (  # noqa: E402
    EditConflictError,
    check_undo_conflict,
    decide_plan_attempt,
    decide_revision_conflict,
)


class DecideRevisionConflictTests(unittest.TestCase):
    def test_unchanged_revision_proceeds(self) -> None:
        decision = decide_revision_conflict(current_revision=8, base_revision=8, rebase_count=0)
        self.assertEqual(decision.outcome, "proceed")

    def test_first_conflict_rebases_once(self) -> None:
        decision = decide_revision_conflict(current_revision=9, base_revision=8, rebase_count=0)
        self.assertEqual(decision.outcome, "rebase")
        self.assertEqual(decision.rebase_count, 1)

    def test_second_conflict_after_a_rebase_stops(self) -> None:
        decision = decide_revision_conflict(current_revision=10, base_revision=9, rebase_count=1)
        self.assertEqual(decision.outcome, "stop")

    def test_never_silently_rewrites_and_proceeds_on_conflict(self) -> None:
        # A changed revision with rebase_count still 0 must never be "proceed" --
        # SS18's hard ban on replaying the same plan against a bumped revision.
        decision = decide_revision_conflict(current_revision=9, base_revision=8, rebase_count=0)
        self.assertNotEqual(decision.outcome, "proceed")


class DecidePlanAttemptTests(unittest.TestCase):
    def test_within_budget_does_not_raise(self) -> None:
        decide_plan_attempt(1)
        decide_plan_attempt(2)

    def test_exceeding_budget_raises_invalid_edit_plan(self) -> None:
        with self.assertRaises(EditConflictError) as ctx:
            decide_plan_attempt(3)
        self.assertEqual(ctx.exception.code, "INVALID_EDIT_PLAN")


class CheckUndoConflictTests(unittest.TestCase):
    def test_matching_revision_is_silent(self) -> None:
        check_undo_conflict(current_revision=9, ai_edit_result_revision=9)

    def test_moved_on_revision_raises_undo_conflict(self) -> None:
        with self.assertRaises(EditConflictError) as ctx:
            check_undo_conflict(current_revision=10, ai_edit_result_revision=9)
        self.assertEqual(ctx.exception.code, "UNDO_CONFLICT")


if __name__ == "__main__":
    unittest.main()
