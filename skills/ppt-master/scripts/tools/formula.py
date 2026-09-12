#!/usr/bin/env python3
"""
PPT Master - Semantic Tool: formula.create

Wraps svg_to_pptx.native_objects.formula_compiler's LaTeX -> OMML compiler.
The agent supplies latex + frame + style; it never writes a
data-pptx-inline-formula / data-pptx-replace-with="formula" marker, an OMML
XML string, or a linearized Unicode preview by hand.

The preview text is a best-effort, semantically-equivalent linearization
(references/native-formula.md's own bar-formula example does the same:
"(-b +- sqrt(b^2-4ac)) / 2a" as one line of ordinary text, not a real
stacked fraction). It covers \\frac, \\sqrt, ^{...}, _{...}, \\pm and a
handful of common symbols; anything else falls through to a stripped,
best-effort token join and the result carries a "preview_approximate"
warning so the caller knows to eyeball it.

Dependencies:
    None beyond existing sibling PPT Master modules
"""

from __future__ import annotations

import hashlib
import json
import re

from svg_to_pptx.native_objects.formula_compiler import (
    compile_latex_to_inline_omml,
    compile_latex_to_omml,
)
from svg_to_pptx.native_objects.formula_parser import FormulaCompileError

from .errors import ToolError

_KNOWN_COMMAND_RE = re.compile(
    r"\\frac\{(?P<num>[^{}]*)\}\{(?P<den>[^{}]*)\}"
    r"|\\sqrt\{(?P<radicand>[^{}]*)\}"
    r"|\\(?P<symbol>pm|mp|times|cdot|leq|geq|neq|approx|infty|"
    r"alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|omega)\b"
)
_SYMBOL_UNICODE = {
    "pm": "\u00b1",
    "mp": "\u2213",
    "times": "\u00d7",
    "cdot": "\u00b7",
    "leq": "\u2264",
    "geq": "\u2265",
    "neq": "\u2260",
    "approx": "\u2248",
    "infty": "\u221e",
    "alpha": "\u03b1",
    "beta": "\u03b2",
    "gamma": "\u03b3",
    "delta": "\u03b4",
    "theta": "\u03b8",
    "lambda": "\u03bb",
    "mu": "\u03bc",
    "pi": "\u03c0",
    "sigma": "\u03c3",
    "omega": "\u03c9",
}
_SUPERSCRIPT_DIGITS = str.maketrans("0123456789", "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079")
_SUBSCRIPT_CHARS = str.maketrans(
    "0123456789aeiou",
    "\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089\u2090\u2091\u1d62\u2092\u1d64",
)
_SUP_RE = re.compile(r"\^\{([^{}]*)\}|\^(\w)")
_SUB_RE = re.compile(r"_\{([^{}]*)\}|_(\w)")


def _linearize_once(latex: str) -> tuple[str, bool]:
    """One linearization pass; returns (text, matched_anything)."""
    matched = False

    def _replace(match: re.Match[str]) -> str:
        nonlocal matched
        matched = True
        if match.group("num") is not None:
            return f"({_linearize(match.group('num'))}) / ({_linearize(match.group('den'))})"
        if match.group("radicand") is not None:
            return f"\u221a({_linearize(match.group('radicand'))})"
        symbol = match.group("symbol")
        return _SYMBOL_UNICODE.get(symbol, symbol)

    text = _KNOWN_COMMAND_RE.sub(_replace, latex)

    def _sup(match: re.Match[str]) -> str:
        nonlocal matched
        body = match.group(1) if match.group(1) is not None else match.group(2)
        translated = body.translate(_SUPERSCRIPT_DIGITS)
        matched = True
        return translated if translated != body else f"^{body}"

    def _sub(match: re.Match[str]) -> str:
        nonlocal matched
        body = match.group(1) if match.group(1) is not None else match.group(2)
        translated = body.translate(_SUBSCRIPT_CHARS)
        matched = True
        return translated if translated != body else f"_{body}"

    text = _SUP_RE.sub(_sup, text)
    text = _SUB_RE.sub(_sub, text)
    return text, matched


def _linearize(latex: str) -> str:
    text = latex
    for _ in range(8):
        text, matched = _linearize_once(text)
        if not matched:
            break
    return text


def linearize_preview(latex: str) -> tuple[str, bool]:
    """Best-effort Unicode preview; returns (preview_text, is_approximate)."""
    text = _linearize(latex.strip())
    leftover_command = "\\" in text
    text = re.sub(r"[{}]", "", text)
    text = re.sub(r"\\([A-Za-z]+)", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text, leftover_command


def formula_create(payload: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """Compile one LaTeX formula and render its marker fragment.

    Never writes any file. display="inline" returns a <tspan> fragment for
    the agent to place inside its own <text> element; display="block"
    (default) returns a complete <g data-pptx-replace-with="formula">.
    """
    latex = payload.get("latex")
    if not isinstance(latex, str) or not latex.strip():
        raise ToolError("INVALID_LATEX", "latex must be a non-empty string")

    display = payload.get("display", "block")
    if display not in ("inline", "block"):
        raise ToolError("INVALID_DISPLAY", "display must be 'inline' or 'block'")

    try:
        if display == "inline":
            compile_latex_to_inline_omml(latex)
        else:
            compile_latex_to_omml(latex)
    except FormulaCompileError as exc:
        raise ToolError("UNSUPPORTED_LATEX", str(exc)) from exc

    preview_text, approximate = linearize_preview(latex)
    warnings = ["preview_approximate: linearized preview may not fully match the LaTeX"] if approximate else []

    if display == "inline":
        fragment = f'<tspan data-pptx-inline-formula="{_escape_attr(latex)}">{_escape_text(preview_text)}</tspan>'
        digest = hashlib.sha256(latex.encode("utf-8")).hexdigest()[:12]
        return {
            "artifact_id": f"formula:inline:{digest}",
            "native_type": "formula",
            "svg_fragment": fragment,
            "preview_text": preview_text,
        }, warnings

    element_id = payload.get("id")
    if not isinstance(element_id, str) or not element_id:
        raise ToolError("INVALID_ID", "id is required for a block formula")
    frame_raw = payload.get("frame")
    if not isinstance(frame_raw, dict):
        raise ToolError("INVALID_FRAME", "frame must be an object with x/y/width/height")
    try:
        x, y, width, height = (
            float(frame_raw["x"]), float(frame_raw["y"]),
            float(frame_raw["width"]), float(frame_raw["height"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("INVALID_FRAME", f"frame must have numeric x/y/width/height: {exc}") from exc
    if width <= 0 or height <= 0:
        raise ToolError("INVALID_FRAME", "frame width/height must be positive")

    font_size = float(payload.get("font_size", 32))
    if not (0 < font_size <= 400):
        raise ToolError("INVALID_STYLE", "font_size must be in (0, 400]")
    color = str(payload.get("color", "#000000"))
    align = payload.get("align", "center")
    if align not in ("left", "center", "right"):
        raise ToolError("INVALID_STYLE", "align must be 'left', 'center', or 'right'")

    text_anchor = {"left": "start", "center": "middle", "right": "end"}[align]
    text_x = {"left": x, "center": x + width / 2, "right": x + width}[align]
    text_y = y + height / 2 + font_size * 0.35

    metadata = json.dumps(
        {"latex": latex, "display": "block", "font_size": font_size, "color": color, "align": align},
        ensure_ascii=False,
    )
    fragment = (
        f'<g id="{_escape_attr(element_id)}" data-pptx-replace-with="formula" '
        f'data-pptx-x="{x:g}" data-pptx-y="{y:g}" '
        f'data-pptx-width="{width:g}" data-pptx-height="{height:g}" '
        f'data-pptx-bounds="{x:g} {y:g} {width:g} {height:g}">'
        f'<metadata type="application/json"><![CDATA[{metadata}]]></metadata>'
        f'<text x="{text_x:g}" y="{text_y:g}" text-anchor="{text_anchor}" '
        f'font-size="{font_size:g}" fill="{_escape_attr(color)}">{_escape_text(preview_text)}</text>'
        f"</g>"
    )
    return {
        "artifact_id": f"formula:{element_id}",
        "native_type": "formula",
        "svg_fragment": fragment,
        "preview_text": preview_text,
    }, warnings


def _escape_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace('"', "&quot;")
        .replace("<", "&lt;").replace(">", "&gt;")
    )


def _escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
