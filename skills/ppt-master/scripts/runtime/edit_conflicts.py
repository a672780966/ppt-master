#!/usr/bin/env python3
"""
PPT Master - Edit Conflict / Revision Guard Decisions (P6)

Encodes SS17-21 and SS33 as small, pure decision functions -- no I/O, no
persistence. `ai_edit_jobs.py` calls these and then persists whatever they
decide; kept separate so the actual conflict *rules* are testable in
complete isolation from job storage.

Hard rule (SS18): a revision conflict is never resolved by rewriting
`expected_revision` to the new value and replaying the *same* plan. The
only two outcomes are "rebuild context, ask for one fresh plan" (rebase)
or "stop and tell the human" (EDIT_CONFLICT_REQUIRES_RETRY) -- there is no
third option.

Usage:
    from runtime.edit_conflicts import decide_revision_conflict, check_undo_conflict

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_REBASE_ATTEMPTS = 1  # exactly one automatic replan (SS19), never more (SS20)
MAX_PLAN_ATTEMPTS = 2  # the submission itself, plus exactly one schema-repair retry (SS36)


class EditConflictError(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass
class ConflictDecision:
    outcome: str  # "proceed" | "rebase" | "stop"
    rebase_count: int


def decide_revision_conflict(current_revision: int, base_revision: int, rebase_count: int) -> ConflictDecision:
    """Called right before applying a submitted plan.

    "proceed": current_revision == base_revision, nothing changed -- apply.
    "rebase": a change happened, but this job hasn't used its one
       automatic replan yet -- discard the plan, rebuild context, ask for
       exactly one more plan against the fresh revision.
    "stop": a change happened *again* after a rebase already happened once
       -- EDIT_CONFLICT_REQUIRES_RETRY, hand it back to the human.
    """
    if current_revision == base_revision:
        return ConflictDecision(outcome="proceed", rebase_count=rebase_count)
    if rebase_count < MAX_REBASE_ATTEMPTS:
        return ConflictDecision(outcome="rebase", rebase_count=rebase_count + 1)
    return ConflictDecision(outcome="stop", rebase_count=rebase_count)


def decide_plan_attempt(plan_attempts: int) -> None:
    """Raise INVALID_EDIT_PLAN's terminal form once the one schema-repair
    retry (SS36) is exhausted. Callers increment plan_attempts *before*
    calling this on each submit-plan call that fails validation."""
    if plan_attempts > MAX_PLAN_ATTEMPTS:
        raise EditConflictError(
            "INVALID_EDIT_PLAN",
            f"EditPlan still invalid after {MAX_PLAN_ATTEMPTS} attempts; stopping (no further retry)",
        )


def check_undo_conflict(current_revision: int, ai_edit_result_revision: int) -> None:
    """Raise UNDO_CONFLICT if the slide moved on since the AI edit that's
    being undone (SS33) -- undo may only restore from the exact revision
    the AI edit itself produced."""
    if current_revision != ai_edit_result_revision:
        raise EditConflictError(
            "UNDO_CONFLICT",
            f"slide has moved to revision {current_revision} since the AI edit produced revision "
            f"{ai_edit_result_revision}; undo refused",
            current_revision=current_revision, ai_edit_result_revision=ai_edit_result_revision,
        )
