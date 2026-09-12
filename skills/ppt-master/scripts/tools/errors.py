#!/usr/bin/env python3
"""
PPT Master - Semantic Tool Layer: shared error envelope

Every tool in scripts/tools/ raises ToolError for an input/state problem it
recognizes and lets any other exception propagate as an unclassified
internal error. scripts/semantic_tools.py is the only place that catches
either and turns it into the stable JSON envelope.

Dependencies:
    None (standard library only)
"""

from __future__ import annotations


class ToolError(Exception):
    """One structured, stable-coded tool failure."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"code": self.code, "message": self.message}
        payload.update(self.details)
        return payload


def error_envelope(errors: list[dict[str, object]], *, warnings: list[str] | None = None) -> dict[str, object]:
    return {"ok": False, "errors": errors, "warnings": warnings or []}


def ok_envelope(fields: dict[str, object], *, warnings: list[str] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"ok": True}
    payload.update(fields)
    payload["warnings"] = warnings or []
    return payload
