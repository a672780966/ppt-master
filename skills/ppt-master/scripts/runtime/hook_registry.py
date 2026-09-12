#!/usr/bin/env python3
"""
PPT Master - Lifecycle Hook Registry (P4)

Tool name -> rule function lookup for PreToolUse / PostToolUse, consulted by
runtime.hooks. Adding a rule for a future tool (e.g. a P5 slide.patch) means
adding one entry here, never touching scripts/semantic_tools.py again.

Usage:
    Imported by scripts/runtime/hooks.py.

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

from typing import Callable

from runtime.hook_rules.export import post_deck_export, pre_deck_export
from runtime.hook_rules.validation import post_create_result, post_slide_validate

PRE_TOOL_RULES: dict[str, Callable[[dict[str, object]], object]] = {
    "deck.export": pre_deck_export,
}

# slide.validate and deck.export have their own signatures (payload, fields,
# [warnings]) since slide.validate additionally needs the tool's warnings
# list to classify passed vs passed-with-warnings; the four .create tools
# only need (tool, fields). runtime.hooks dispatches by tool name rather
# than trying to unify all three signatures.
_CREATE_TOOLS = ("shape.create", "formula.create", "chart.create", "table.create")

POST_TOOL_RULES = {
    "slide.validate": post_slide_validate,
    "deck.export": post_deck_export,
    **{name: post_create_result for name in _CREATE_TOOLS},
}
