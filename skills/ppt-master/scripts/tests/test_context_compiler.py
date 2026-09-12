#!/usr/bin/env python3
"""Tests for project_management.page_context.compile_page_context (P2 Context Compiler)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from project_management.page_context import (  # noqa: E402
    CONTEXT_COMPILER_STATS_SCHEMA,
    compile_page_context,
)

_SPEC_LOCK_TEXT = """<!-- ppt-master-schema: spec-lock/v1 -->
# Execution Lock

## canvas
- viewBox: 0 0 1280 720
- format: PPT 16:9

## communication
- primary_language: zh-CN
- audience: Engineers
- objective: Explain the compiler contract
- core_message: Compiled context replaces a full re-read
- consumption_mode: balanced

## mode
- mode: instructional

## visual_style
- visual_style: swiss-minimal

## colors
- bg: #FFFFFF

## typography
- font_family: Arial
- body: 24
- title: 36

## icons
- library: none
- inventory: none

## page_rhythm
- P01: anchor
- P02: dense

## pptx_structure
- mode: flat

## forbidden
- `mask`, `<style>`, `class`, external CSS, `<foreignObject>`, `textPath`, `@font-face`, `<animate*>`, `<set>`, `<script>` / event attributes, `<iframe>`
"""

_DESIGN_SPEC_TEXT = """<!-- ppt-master-schema: design-spec/v1 -->
# Fixture Project - Design Spec

## I. Project Information

| Item | Value |
| --- | --- |
| Project Name | Fixture Project |
| Canvas Format | PPT 16:9 (1280x720) |
| Page Count | 2 |
| Primary Language | zh-CN |
| Target Audience | Engineers |
| Communication Intent | Explain the compiler contract |
| Desired Audience Outcome | Understand compiled context |
| Core Message / Ask / Action | Compiled context replaces a full re-read |
| Delivery Context | Test fixture |
| Artifact Afterlife | none |
| Reading Mode | balanced |
| Content Strategy | balanced |
| Design Style | swiss-minimal |
| AI Image Acquisition Path | not applicable |
| Generation Mode | continuous |
| Spec Refinement | disabled |
| Speaker Notes | disabled |
| Custom Animations | disabled |
| Narration Audio | disabled |
| Created Date | 2026-09-12 |

## II. Canvas Specification

| Property | Value |
| --- | --- |
| Format | PPT 16:9 |
| Dimensions | 1280 x 720 |
| viewBox | `0 0 1280 720` |
| Margins | 64px |
| Content Area | fixture |

## III. Visual Theme

### Theme Style

- **Mode**: instructional
- **Visual style**: swiss-minimal
- **Theme**: fixture
- **Tone**: fixture

### Color Scheme

| Role | HEX | Purpose |
| --- | --- | --- |
| Background | #FFFFFF | canvas |

## IV. Typography System

### Font Plan

| Role | Character (Reference) | Primary | English if non-English | Fallback tail |
| --- | --- | --- | --- | --- |
| Title | fixture | Arial | Arial | sans-serif |
| Body | fixture | Arial | Arial | sans-serif |

- **Title stack**: Arial
- **Body stack**: Arial

### Font Size Hierarchy

| Purpose | Anchor Size (px) |
| --- | ---: |
| Body | 24 |
| Title | 36 |

## V. Layout Principles

### Deck-wide Direction

- **Hierarchy direction**: fixture
- **Composition tendency**: fixture
- **Cross-page continuity**: fixture
- **Spacing posture**: fixture
- **Spacing anchors**: fixture

## VI. Icon Usage Specification

- **Primary bundled library**: none

| Icon Path | Suitable Scenarios |
| --- | --- |

## VIII. Image Resource List

| Filename | Dimensions | Ratio | Purpose | Type | Image pattern | Crop Policy | Acquire Via | Status | Reference | text_policy | page_role |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## IX. Content Outline

### Part 1: Fixture

#### Slide 01 - Cover

- **Audience move**: none to aware
- **Relationships**: none
- **Title**: Cover
- **Core message**: fixture core message
- **Content**: fixture content block

#### Slide 02 - Body

- **Audience move**: aware to understood
- **Relationships**: order
- **Title**: Body
- **Core message**: fixture core message two
- **Content**: fixture content block two

## X. Speaker Notes Requirements

- **Generation**: disabled
"""


class ContextCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = Path(self._tmp.name)
        (self.project_path / "spec_lock.md").write_text(_SPEC_LOCK_TEXT, encoding="utf-8")
        (self.project_path / "design_spec.md").write_text(_DESIGN_SPEC_TEXT, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_compile_success_writes_stats_and_returns_compact_output(self) -> None:
        stats, output = compile_page_context(self.project_path, "P01")
        self.assertFalse(stats["fallback_occurred"])
        self.assertEqual(stats["schema"], CONTEXT_COMPILER_STATS_SCHEMA)
        self.assertIsNotNone(output)
        self.assertGreater(stats["compiled_chars"], 0)
        self.assertEqual(stats["compiled_chars"], len(output))
        self.assertEqual(stats["loaded_capabilities"], [])
        self.assertIn("project:design_spec.md", stats["loaded_sources"])
        self.assertIn("project:spec_lock.md", stats["loaded_sources"])
        self.assertGreaterEqual(stats["compile_latency_ms"], 0)

        stats_path = self.project_path / "analysis" / "context-compiler" / "P01.compile.json"
        self.assertTrue(stats_path.is_file())
        on_disk = json.loads(stats_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, stats)

    def test_compile_second_page_reflects_its_own_relationships_block(self) -> None:
        _stats, output = compile_page_context(self.project_path, "P02")
        self.assertIn("Body", output)
        self.assertIn("order", output)

    def test_compile_fallback_on_missing_slide_block(self) -> None:
        stats, output = compile_page_context(self.project_path, "P09")
        self.assertTrue(stats["fallback_occurred"])
        self.assertIsNone(output)
        self.assertIsNone(stats["compiled_chars"])
        self.assertIn("Slide 09", stats["fallback_reason"])

        stats_path = self.project_path / "analysis" / "context-compiler" / "P09.compile.json"
        self.assertTrue(stats_path.is_file())

    def test_compile_fallback_on_malformed_page_token_writes_no_stats_file(self) -> None:
        stats, output = compile_page_context(self.project_path, "not-a-page")
        self.assertTrue(stats["fallback_occurred"])
        self.assertIsNone(output)
        stats_dir = self.project_path / "analysis" / "context-compiler"
        self.assertFalse(stats_dir.exists())

    def test_compile_fallback_when_required_artifact_missing(self) -> None:
        (self.project_path / "spec_lock.md").unlink()
        stats, output = compile_page_context(self.project_path, "P01")
        self.assertTrue(stats["fallback_occurred"])
        self.assertIsNone(output)
        self.assertIn("spec_lock.md", stats["fallback_reason"])


if __name__ == "__main__":
    unittest.main()
