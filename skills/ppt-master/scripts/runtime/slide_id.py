#!/usr/bin/env python3
"""
PPT Master - Slide-id / page-filename mapping (P4/P5 shared utility)

One tiny, deliberately shared function: two independent call sites (P4's
slide.validate PostToolUse binding, P5's direct-edit revision wiring) both
need to turn a page basename like "05_comparison.svg" into the SlideState
key "P05" that build_state.json actually uses. Promoted out of
runtime/hook_rules/validation.py's private helper so neither call site
reimplements the same regex.

Usage:
    from runtime.slide_id import slide_id_from_page

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

import re
from pathlib import Path

_PAGE_NUMBER_RE = re.compile(r"^0*([0-9]+)")


def slide_id_from_page(page: str) -> str | None:
    """"05_comparison.svg" -> "P05"; unparseable -> None (skip silently)."""
    stem = Path(page).stem
    match = _PAGE_NUMBER_RE.match(stem)
    if not match:
        return None
    return f"P{int(match.group(1)):02d}"
