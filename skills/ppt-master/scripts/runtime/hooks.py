#!/usr/bin/env python3
"""
PPT Master - Lifecycle Hook Dispatch: PreToolUse / PostToolUse (P4)

The Build Controller side of the Semantic Tool Layer: scripts/semantic_tools.py
calls run_pre_tool_hooks() immediately before dispatching to a tool and
run_post_tool_hooks() immediately after it succeeds. Both are pure lookups
into runtime.hook_registry plus (for PreToolUse) the shadow/enforce branch;
neither hook ever calls another tool or a model (references/artifact-
ownership.md's Semantic Tool boundary applies here too: hooks inspect,
validate, and transition build_state.json -- they never author or repair an
artifact themselves).

Shadow vs enforce (per-project build_state.json "hooks.mode", default
"shadow"): a PreToolUse rule that would BLOCK in enforce mode instead
downgrades to a WARN carrying a "[shadow] would block: ..." message in
shadow mode, and the tool runs anyway -- this lets a corpus be replayed
against a new rule before it can ever actually stop a real export.

Usage:
    from runtime import hooks
    pre = hooks.run_pre_tool_hooks(tool_name, payload)   # may raise ToolError in enforce mode
    ...
    post = hooks.run_post_tool_hooks(tool_name, payload, fields, warnings)

Dependencies:
    None beyond existing sibling PPT Master modules
"""

from __future__ import annotations

import json
from pathlib import Path

from runtime.build_state import load
from runtime.hook_registry import POST_TOOL_RULES, PRE_TOOL_RULES
from runtime.hook_types import HookResult, allow
from workflow_log import append_note

_EVENT_TAG = {
    "PreToolUse": "HOOK_PRE_TOOL",
    "PostToolUse": "HOOK_POST_TOOL",
    "Stop": "HOOK_STOP",
    "Resume": "HOOK_RESUME",
}


def _hooks_mode(payload: dict[str, object]) -> str:
    project = payload.get("project")
    if not isinstance(project, str) or not project:
        return "shadow"
    state = load(Path(project))
    return state.hooks_mode if state is not None else "shadow"


def _audit(payload: dict[str, object], result: HookResult, *, mode: str | None = None) -> None:
    project = payload.get("project")
    if not isinstance(project, str) or not project:
        return
    tag = _EVENT_TAG.get(result.hook, result.hook)
    header = f"{tag} tool={result.tool} decision={result.decision}"
    if mode:
        header += f" mode={mode}"
    try:
        append_note(project, header + "\n" + json.dumps(result.to_dict(), ensure_ascii=False))
    except OSError:
        pass  # advisory only -- artifact-ownership.md: recording failure never blocks


def run_pre_tool_hooks(tool: str, payload: dict[str, object]) -> HookResult:
    """Run tool's registered PreToolUse rule, if any.

    Raises tools.errors.ToolError when the rule blocks and hooks.mode is
    "enforce" -- reuses the exact exception class scripts/semantic_tools.py
    already catches, so its {"ok": false, "errors": [...]} envelope and
    exit-code-1 contract need no change for this to plug in.
    """
    rule = PRE_TOOL_RULES.get(tool)
    if rule is None:
        return allow("PreToolUse", tool)

    result = rule(payload)
    if result.decision == "allow":
        return result

    mode = _hooks_mode(payload)
    if result.decision == "block" and mode != "enforce":
        result = HookResult(
            hook=result.hook, tool=result.tool, decision="warn",
            code=result.code, message=f"[shadow] would block: {result.message}",
            details=result.details,
        )
    _audit(payload, result, mode=mode)
    if result.decision == "block":
        from tools.errors import ToolError
        raise ToolError(result.code or "HOOK_BLOCK", result.message or "", **result.details)
    return result


def run_post_tool_hooks(
    tool: str,
    payload: dict[str, object],
    fields: dict[str, object],
    warnings: list[str] | None = None,
) -> HookResult:
    """Run tool's registered PostToolUse rule, if any. Never raises."""
    rule = POST_TOOL_RULES.get(tool)
    if rule is None:
        return allow("PostToolUse", tool)

    if tool == "slide.validate":
        result = rule(payload, fields, warnings)
    elif tool == "deck.export":
        result = rule(payload, fields)
    else:
        result = rule(tool, fields)
    if result.decision != "allow":
        _audit(payload, result)
    return result
