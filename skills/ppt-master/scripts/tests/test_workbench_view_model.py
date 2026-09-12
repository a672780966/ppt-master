#!/usr/bin/env python3
"""Tests for scripts/workbench/view_model.py (P5) -- pure state-to-UI mapping."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime.build_state import BuildState, SlideState  # noqa: E402
from workbench.view_model import (  # noqa: E402
    build_progress,
    export_status,
    hooks_mode_view,
    slide_view,
    slides_view,
    validation_state,
)


class ValidationStateTests(unittest.TestCase):
    def test_never_validated(self) -> None:
        self.assertEqual(validation_state(SlideState(revision=1)), "not_validated")

    def test_valid_when_revision_matches(self) -> None:
        slide = SlideState(revision=3, validated_revision=3, validation_status="passed")
        self.assertEqual(validation_state(slide), "valid")

    def test_valid_with_warnings_status_still_counts_as_valid(self) -> None:
        slide = SlideState(revision=3, validated_revision=3, validation_status="passed-with-warnings")
        self.assertEqual(validation_state(slide), "valid")

    def test_stale_when_revision_moved_past_validated(self) -> None:
        slide = SlideState(revision=4, validated_revision=3, validation_status="passed")
        self.assertEqual(validation_state(slide), "stale_validation")

    def test_failed_when_validated_at_current_revision_but_failed(self) -> None:
        slide = SlideState(revision=2, validated_revision=2, validation_status="failed")
        self.assertEqual(validation_state(slide), "failed")


class SlideViewTests(unittest.TestCase):
    def test_ready_and_valid_shows_plain_ready(self) -> None:
        slide = SlideState(status="ready", revision=4, validated_revision=4, validation_status="passed")
        view = slide_view("P01", slide)
        self.assertEqual(view["display_label"], "Ready")
        self.assertEqual(view["validation_label"], "Validated")

    def test_ready_but_never_validated_shows_needs_validation(self) -> None:
        slide = SlideState(status="ready", revision=1)
        view = slide_view("P02", slide)
        self.assertEqual(view["display_label"], "Needs validation")

    def test_ready_but_stale_validation_shows_needs_validation(self) -> None:
        slide = SlideState(status="ready", revision=6, validated_revision=5, validation_status="passed")
        view = slide_view("P03", slide)
        self.assertEqual(view["display_label"], "Needs validation")
        self.assertEqual(view["validation_label"], "Outdated")

    def test_ready_but_failed_validation_shows_validation_failed(self) -> None:
        slide = SlideState(status="ready", revision=8, validated_revision=8, validation_status="failed")
        view = slide_view("P11", slide)
        self.assertEqual(view["display_label"], "Validation failed")

    def test_building_never_overridden_by_validation(self) -> None:
        slide = SlideState(status="building", revision=2)
        view = slide_view("P02", slide)
        self.assertEqual(view["display_label"], "Building")

    def test_planned_and_stale_pass_through_unmodified(self) -> None:
        self.assertEqual(slide_view("P04", SlideState(status="planned"))["display_label"], "Planned")
        self.assertEqual(slide_view("P05", SlideState(status="stale", revision=1))["display_label"], "Stale")

    def test_slides_view_sorts_in_page_order(self) -> None:
        state = BuildState(slides={"P10": SlideState(), "P02": SlideState(), "P01": SlideState()})
        ids = [v["id"] for v in slides_view(state)]
        self.assertEqual(ids, ["P01", "P02", "P10"])


class BuildProgressTests(unittest.TestCase):
    def test_counts_and_needs_validation_never_from_file_counting(self) -> None:
        state = BuildState(slides={
            "P01": SlideState(status="ready", revision=1, validated_revision=1, validation_status="passed"),
            "P02": SlideState(status="building", revision=1),
            "P03": SlideState(status="ready", revision=2, validated_revision=1, validation_status="passed"),
            "P04": SlideState(status="planned"),
        })
        progress = build_progress(state)
        self.assertEqual(progress["total"], 4)
        self.assertEqual(progress["ready"], 2)
        self.assertEqual(progress["building"], 1)
        self.assertEqual(progress["planned"], 1)
        self.assertEqual(progress["needs_validation"], 1)


class ExportStatusTests(unittest.TestCase):
    def test_never_exported(self) -> None:
        self.assertEqual(export_status(BuildState())["code"], "never")

    def test_outdated_when_dirty(self) -> None:
        state = BuildState(export_dirty=True, last_export="exports/x.pptx")
        self.assertEqual(export_status(state)["code"], "outdated")

    def test_ready_when_clean(self) -> None:
        state = BuildState(export_dirty=False, last_export="exports/x.pptx")
        self.assertEqual(export_status(state)["code"], "ready")

    def test_never_relies_on_pptx_file_existing_on_disk(self) -> None:
        # export_status is purely a function of build_state fields; it never
        # touches the filesystem to check whether last_export actually exists.
        state = BuildState(export_dirty=False, last_export="exports/does-not-exist.pptx")
        self.assertEqual(export_status(state)["code"], "ready")


class HooksModeViewTests(unittest.TestCase):
    def test_shadow_maps_to_advisory(self) -> None:
        self.assertEqual(hooks_mode_view(BuildState(hooks_mode="shadow"))["label"], "Advisory")

    def test_enforce_maps_to_enforced(self) -> None:
        self.assertEqual(hooks_mode_view(BuildState(hooks_mode="enforce"))["label"], "Enforced")


if __name__ == "__main__":
    unittest.main()
