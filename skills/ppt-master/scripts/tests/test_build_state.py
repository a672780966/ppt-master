#!/usr/bin/env python3
"""Tests for scripts/runtime/build_state.py (P1 Persistent Build State)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime.build_state import (  # noqa: E402
    BuildState,
    BuildStateError,
    BuildStateLockedError,
    SlideState,
    load,
    locked_state,
    save,
    state_path,
)


class BuildStateLoadSaveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_load_missing_file_returns_none(self) -> None:
        self.assertIsNone(load(self.project_path))

    def test_save_then_load_round_trips_exactly(self) -> None:
        state = BuildState(route="quick")
        state.slides["P01"] = SlideState(status="ready", revision=2)
        save(self.project_path, state)

        loaded = load(self.project_path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.route, "quick")
        self.assertEqual(loaded.slides["P01"].revision, 2)
        self.assertEqual(loaded.slides["P01"].status, "ready")

    def test_save_is_atomic_no_surviving_tmp_file(self) -> None:
        state = BuildState()
        save(self.project_path, state)
        leftovers = list(self.project_path.glob("*.tmp-*"))
        self.assertEqual(leftovers, [])
        self.assertTrue(state_path(self.project_path).is_file())

    def test_load_rejects_wrong_schema_id(self) -> None:
        path = state_path(self.project_path)
        path.write_text(json.dumps({"schema": "not-the-right-schema"}), encoding="utf-8")
        with self.assertRaises(BuildStateError):
            load(self.project_path)

    def test_locked_state_prevents_concurrent_claim_and_releases_cleanly(self) -> None:
        lock_path = self.project_path / ".build_state.lock"
        lock_path.write_text("", encoding="utf-8")
        with self.assertRaises(BuildStateLockedError):
            with locked_state(self.project_path, timeout_s=0.2) as _state:
                pass
        lock_path.unlink()
        with locked_state(self.project_path, timeout_s=1.0) as state:
            state.route = "quick"
        loaded = load(self.project_path)
        self.assertEqual(loaded.route, "quick")
        self.assertFalse(lock_path.exists())

    def test_locked_state_does_not_save_when_body_raises(self) -> None:
        save(self.project_path, BuildState(route="default"))
        with self.assertRaises(RuntimeError):
            with locked_state(self.project_path) as state:
                state.route = "quick"
                raise RuntimeError("boom")
        loaded = load(self.project_path)
        self.assertEqual(loaded.route, "default")


if __name__ == "__main__":
    unittest.main()
