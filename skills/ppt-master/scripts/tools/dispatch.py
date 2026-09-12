#!/usr/bin/env python3
"""
PPT Master - Semantic Tool Dispatch (P3 tools + P4 hooks, shared core)

The actual "payload in, envelope out" logic behind scripts/semantic_tools.py,
extracted so a second caller (the P5 Workbench's Runtime API,
scripts/workbench/runtime_api.py) can invoke the exact same tool + hook
sequence in-process -- same envelopes, same exit-code convention, same
shadow/enforce behavior -- without going through a subprocess and without a
second implementation that could drift from the CLI's.

scripts/semantic_tools.py's own main() is a thin wrapper around invoke():
parse argv/JSON into a payload dict, call invoke(), print the envelope,
return the exit code. Nothing about the CLI's documented contract changes.

Usage:
    from tools.dispatch import invoke
    envelope, exit_code = invoke("deck.export", {"project": "...", ...})

Dependencies:
    None beyond existing sibling PPT Master modules
"""

from __future__ import annotations

from runtime import hooks
from tools.chart import chart_create
from tools.errors import ToolError, error_envelope, ok_envelope
from tools.export import deck_export
from tools.formula import formula_create
from tools.shape import shape_create
from tools.table import table_create
from tools.validate import slide_validate

TOOLS = {
    "shape.create": shape_create,
    "formula.create": formula_create,
    "chart.create": chart_create,
    "table.create": table_create,
    "slide.validate": slide_validate,
    "deck.export": deck_export,
}


def invoke(tool_name: str, payload: dict[str, object]) -> tuple[dict[str, object], int]:
    """Run one semantic tool through its PreToolUse/PostToolUse hooks.

    Returns (envelope, exit_code) with the exact same shape/codes
    scripts/semantic_tools.py's CLI has always returned:
        0  ok: true
        1  ok: false (a recognized input/state/hook-block problem)
        2  unknown tool name (the CLI never hits this -- argparse's
           subparser choices already constrain it -- but an API caller can)
    """
    tool_fn = TOOLS.get(tool_name)
    if tool_fn is None:
        return (
            error_envelope([{"code": "UNKNOWN_TOOL", "message": f"no such tool: {tool_name!r}"}]),
            2,
        )

    try:
        pre_result = hooks.run_pre_tool_hooks(tool_name, payload)
        fields, warnings = tool_fn(payload)
        post_result = hooks.run_post_tool_hooks(tool_name, payload, fields, warnings)
    except ToolError as exc:
        return error_envelope([exc.to_dict()], warnings=[]), 1
    except Exception as exc:  # pragma: no cover - last-resort, unclassified
        return error_envelope([{"code": "INTERNAL_ERROR", "message": str(exc)}]), 1

    hook_warnings = [
        f"{result.code}: {result.message}"
        for result in (pre_result, post_result)
        if result.decision != "allow"
    ]
    envelope = ok_envelope(fields, warnings=warnings + hook_warnings)
    return envelope, (0 if envelope.get("ok", True) else 1)
