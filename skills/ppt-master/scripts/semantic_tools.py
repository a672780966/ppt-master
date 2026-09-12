#!/usr/bin/env python3
"""
PPT Master - Semantic Tool Layer CLI (P3)

Stable CLI entry point for the six semantic tools: shape.create,
formula.create, chart.create, table.create, slide.validate, deck.export.
Implementation lives in ``tools/``. The agent gives structured data (a
preset name, LaTeX, chart/table data, a project path) and gets a
structured, stable-shaped result back; it never needs to know
preset_shape_svg.py, native-formula.md's marker attributes, OMML, or
svg_quality_checker.py's report format.

Hard rule: no subcommand here writes build_state.json directly. See
references/artifact-ownership.md and scripts/build_state.py. The one
exception is runtime.hooks' PreToolUse/PostToolUse calls wired into
tools/dispatch.py's invoke() below (P4 Lifecycle Hooks) -- deterministic
revision-binding/export-gate transitions only, never a design or content
decision; see scripts/docs/lifecycle_hooks.md. tools/dispatch.py's invoke()
is the actual "payload in, envelope out" implementation, shared with the P5
Workbench's Runtime API (scripts/workbench/runtime_api.py) so both callers
get identical shadow/enforce behavior by construction -- this file is just
the CLI's argv/JSON parsing shell around it.

**Hard rule -- never inline LaTeX (or any backslash-heavy payload) into
--input on a command line**: shell layers (observed concretely with Git
Bash forwarding to a native Windows python.exe) can silently collapse or
reinterpret backslash sequences before this process ever sees them --
"\\frac{-b}{2a}" arrived here once as "\x0crac{-b}{2a}" (a real form-feed
control character) purely from shell requoting, with no bug in this file or
in tools/formula.py. Write the JSON to a file and use --input-file, or pipe
it over stdin (both bypass shell argv re-quoting entirely); reserve
--input for payloads with no backslashes.

Usage:
    python3 scripts/semantic_tools.py shape.create --input '<json>'
    python3 scripts/semantic_tools.py formula.create --input-file payload.json
    python3 scripts/semantic_tools.py chart.create --input '<json>'
    python3 scripts/semantic_tools.py table.create --input '<json>'
    python3 scripts/semantic_tools.py slide.validate --input '<json>'
    python3 scripts/semantic_tools.py deck.export --input '<json>'
    (omit --input to read the JSON payload from stdin)

Examples:
    python3 scripts/semantic_tools.py shape.create --input \
        '{"id":"p03-arrow","preset":"rightArrow","frame":{"x":100,"y":100,"width":200,"height":80},"fill":"#2563EB"}'
    echo '{"project":"projects/demo","page":"05_comparison.svg"}' | \
        python3 scripts/semantic_tools.py slide.validate

Exit codes:
    0  ok: true  (tool executed; for slide.validate this also means the slide passed)
    1  ok: false (a recognized input/state problem -- see the "errors" field)
    2  malformed CLI usage (argparse) or malformed --input JSON

Dependencies:
    None beyond existing sibling PPT Master modules
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from attribution_guard import require_skill_integrity  # noqa: E402
from console_encoding import configure_utf8_stdio  # noqa: E402

configure_utf8_stdio()

from tools.dispatch import TOOLS  # noqa: E402
from tools.dispatch import invoke as dispatch_invoke  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PPT Master Semantic Tool Layer (P3): structured tools over existing implementation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="tool", required=True)
    for name in TOOLS:
        sub = subparsers.add_parser(name)
        sub.add_argument("--input", default=None, help="JSON payload; avoid for backslash-heavy data (see module docstring)")
        sub.add_argument("--input-file", default=None, help="Path to a JSON payload file; robust for LaTeX/backslash-heavy data")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.input is not None and args.input_file is not None:
        print(json.dumps({"ok": False, "errors": [{"code": "INVALID_JSON", "message": "pass only one of --input or --input-file"}]}))
        return 2
    if args.input_file is not None:
        raw_input = Path(args.input_file).read_text(encoding="utf-8")
    else:
        raw_input = args.input if args.input is not None else sys.stdin.read()
    try:
        payload = json.loads(raw_input)
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "errors": [{"code": "INVALID_JSON", "message": str(exc)}]}))
        return 2
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "errors": [{"code": "INVALID_JSON", "message": "payload must be a JSON object"}]}))
        return 2

    envelope, exit_code = dispatch_invoke(args.tool, payload)
    print(json.dumps(envelope, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    require_skill_integrity()
    raise SystemExit(main())
