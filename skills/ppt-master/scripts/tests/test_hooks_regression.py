#!/usr/bin/env python3
"""
Real-project regression tests for P4 Lifecycle Hooks.

Read-only smoke assertions against the state left behind by this phase's
actual regression run (recorded narratively in
references/benchmarking/corpus.md `hooks-p4`, with full command transcripts)
-- not a re-mutation of shared real projects on every test run, matching the
existing convention in test_semantic_tools_validate_export.py (real project,
@unittest.skipUnless, assert against what is actually on disk).

The full fault-injection *logic* (stale_validation blocks export, stale
slide blocks Stop with the give-up cap) is unit-tested against disposable
tempdir projects in test_hooks.py; this file only confirms the real CLI
wiring actually left the two real projects in the state that regression
narrative claims.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime.build_state import load  # noqa: E402

_REPO_ROOT = SCRIPTS_DIR.parents[2]
_P2_PROJECT = _REPO_ROOT / "projects" / "text-teaching-p2-default_ppt169_20260912"
_P3_PROJECT = _REPO_ROOT / "projects" / "semantic-tools-p3-fixture_20260912"
_BUILD_STATE_CLI = SCRIPTS_DIR / "build_state.py"
_SEMANTIC_TOOLS_CLI = SCRIPTS_DIR / "semantic_tools.py"


@unittest.skipUnless(_P2_PROJECT.is_dir(), "real P2 text-teaching project not present on this machine")
class RealP2AdoptionRegressionTests(unittest.TestCase):
    def test_hooks_adopted_in_enforce_mode(self) -> None:
        state = load(_P2_PROJECT)
        self.assertEqual(state.hooks_mode, "enforce")

    def test_every_slide_validated_at_its_current_revision(self) -> None:
        state = load(_P2_PROJECT)
        self.assertEqual(len(state.slides), 9)
        for slide_id, slide in state.slides.items():
            with self.subTest(slide=slide_id):
                self.assertEqual(slide.revision, slide.validated_revision)
                self.assertIn(slide.validation_status, ("passed", "passed-with-warnings"))

    def test_deck_is_exported_and_not_dirty(self) -> None:
        state = load(_P2_PROJECT)
        self.assertEqual(state.phase, "exported")
        self.assertFalse(state.export_dirty)
        self.assertTrue(Path(state.last_export).name.endswith(".pptx"))
        self.assertTrue((_REPO_ROOT / state.last_export).is_file())

    def test_stop_check_allows_and_block_count_is_reset(self) -> None:
        state = load(_P2_PROJECT)
        self.assertEqual(state.stop_block_count, 0)
        result = subprocess.run(
            [sys.executable, str(_BUILD_STATE_CLI), "stop-check", str(_P2_PROJECT), "--json"],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn('"decision": "allow"', result.stdout)

    def test_deck_export_pretooluse_allows_a_clean_re_export(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SEMANTIC_TOOLS_CLI), "deck.export",
             "--input", f'{{"project":"{_P2_PROJECT.as_posix()}","no_notes":true}}'],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('"ok": true', result.stdout)
        self.assertNotIn("DECK_NOT_EXPORTABLE", result.stdout)

    def test_workflow_log_recorded_the_fault_injection_hook_events(self) -> None:
        log_text = (_P2_PROJECT / "validation" / "workflow.log").read_text(encoding="utf-8", errors="replace")
        self.assertIn("HOOK_PRE_TOOL tool=deck.export decision=block mode=enforce", log_text)
        self.assertIn("HOOK_STOP decision=block code=BUILD_INCOMPLETE", log_text)
        self.assertIn("HOOK_STOP status=failed reason=QUALITY_GATE_UNRESOLVED", log_text)


@unittest.skipUnless(_P3_PROJECT.is_dir(), "real P3 semantic-tools fixture project not present on this machine")
class RealP3ShadowModeRegressionTests(unittest.TestCase):
    def test_hooks_left_in_shadow_mode(self) -> None:
        state = load(_P3_PROJECT)
        self.assertIsNotNone(state)
        self.assertEqual(state.hooks_mode, "shadow")

    def test_svg_path_was_recorded_by_submit_slide(self) -> None:
        state = load(_P3_PROJECT)
        self.assertEqual(state.slides["P01"].svg_path, "svg_output/01_data_summary.svg")


if __name__ == "__main__":
    unittest.main()
