#!/usr/bin/env python3
"""
PPT Master - Lifecycle Hook Result Types (P4)

The one shared shape every hook rule returns. Deliberately three decisions
only (ALLOW / WARN / BLOCK) -- no MODIFY_ARGS / DEFER / ASK_USER / AUTO_RETRY
in this version; see references/artifact-ownership.md and
scripts/docs/lifecycle_hooks.md for why.

Usage:
    Imported by scripts/runtime/hooks.py and scripts/runtime/hook_rules/*.py.

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

from dataclasses import dataclass, field

DECISIONS = ("allow", "warn", "block")


@dataclass
class HookResult:
    hook: str  # "PreToolUse" | "PostToolUse" | "Stop" | "Resume"
    tool: str
    decision: str = "allow"
    code: str | None = None
    message: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ValueError(f"invalid HookResult.decision: {self.decision!r}")
        if self.decision != "allow" and not self.code:
            raise ValueError(f"a {self.decision!r} HookResult needs a code")

    @property
    def is_blocking(self) -> bool:
        return self.decision == "block"

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"hook": self.hook, "tool": self.tool, "decision": self.decision}
        if self.code:
            payload["code"] = self.code
        if self.message:
            payload["message"] = self.message
        if self.details:
            payload["details"] = self.details
        return payload


def allow(hook: str, tool: str) -> HookResult:
    return HookResult(hook=hook, tool=tool, decision="allow")


def warn(hook: str, tool: str, code: str, message: str, **details: object) -> HookResult:
    return HookResult(hook=hook, tool=tool, decision="warn", code=code, message=message, details=details)


def block(hook: str, tool: str, code: str, message: str, **details: object) -> HookResult:
    return HookResult(hook=hook, tool=tool, decision="block", code=code, message=message, details=details)
