#!/usr/bin/env python3
"""
PPT Master - EditPlan Schema and Validation (P6)

The frozen, narrow contract between "the model reasoned about an
instruction" and "the Patch Engine mutates an SVG." Model output is never
a full SVG or freeform text -- always this structured shape, and every
operation is scope/target/tool-allowlist checked before a single byte is
written (references/artifact-ownership.md's "Runtime owns the mutation"
principle, applied to AI edits).

EditPlan shape:
    {"scope": "element"|"selection"|"slide", "base_revision": int,
     "summary": str, "operations": [Operation, ...]}

First-version operation vocabulary (exactly nine, per the P6 spec -- do
not add more without reopening that decision):
    set_text          {target, value}
    set_style         {target, style: {attr: value, ...}}
    set_geometry       {target, geometry: {x?, y?, width?, height?, ...}}
    translate          {target, dx, dy}
    resize             {target, width?, height?}
    replace_fragment   {target, fragment: "<svg fragment>"}
    insert_fragment    {parent, fragment: "<svg fragment>", index?}
    delete_element     {target}
    semantic_tool      {target, tool, arguments: {...}}  -- tool must be
                       in SEMANTIC_TOOL_ALLOWLIST (the only tools that are
                       *authoring* operations in tools.dispatch.TOOLS)

Usage:
    from runtime.edit_plan import validate_edit_plan, EditPlanError
    operations = validate_edit_plan(plan, scope=job.scope,
                                     selection_ids=job.selection_ids,
                                     current_ids=current_ids)

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

SCOPES = ("element", "selection", "slide")

OPERATION_TYPES = frozenset({
    "set_text", "set_style", "set_geometry", "translate", "resize",
    "replace_fragment", "insert_fragment", "delete_element", "semantic_tool",
})

# The only tools.dispatch.TOOLS entries that make sense as *authoring*
# operations from an EditPlan -- slide.validate/deck.export are Semantic
# Tools too, but they are never reachable through an AI edit.
SEMANTIC_TOOL_ALLOWLIST = frozenset({
    "shape.create", "chart.create", "table.create", "formula.create",
})

_REQUIRED_TOP_LEVEL = ("scope", "base_revision", "operations")

_OPERATION_REQUIRED_FIELDS = {
    "set_text": ("target", "value"),
    "set_style": ("target", "style"),
    "set_geometry": ("target", "geometry"),
    "translate": ("target", "dx", "dy"),
    "resize": ("target",),
    "replace_fragment": ("target", "fragment"),
    "insert_fragment": ("parent", "fragment"),
    "delete_element": ("target",),
    "semantic_tool": ("target", "tool", "arguments"),
}


class EditPlanError(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _fail(code: str, message: str, **details: object) -> None:
    raise EditPlanError(code, message, **details)


def operation_scope_id(op: dict[str, object]) -> str:
    """The one id an operation's scope check is anchored on."""
    return str(op["parent"]) if op["type"] == "insert_fragment" else str(op["target"])


def validate_edit_plan(
    plan: object,
    *,
    scope: str,
    selection_ids: list[str],
    current_ids: set[str],
) -> list[dict[str, object]]:
    """Validate an EditPlan's shape, scope, targets, and tool allowlist.

    Returns the validated `operations` list on success. Raises
    EditPlanError on the first failure -- callers must treat any raise
    here as "zero writes, nothing to roll back."
    """
    if not isinstance(plan, dict):
        _fail("INVALID_EDIT_PLAN", "EditPlan must be a JSON object")
    for key in _REQUIRED_TOP_LEVEL:
        if key not in plan:
            _fail("INVALID_EDIT_PLAN", f"EditPlan missing required field: {key}")
    if plan["scope"] not in SCOPES:
        _fail("INVALID_EDIT_PLAN", f"invalid scope: {plan['scope']!r}")
    if plan["scope"] != scope:
        _fail("INVALID_EDIT_PLAN", f"EditPlan scope {plan['scope']!r} does not match the job's scope {scope!r}")
    if not isinstance(plan["base_revision"], int):
        _fail("INVALID_EDIT_PLAN", "base_revision must be an integer")

    operations = plan["operations"]
    if not isinstance(operations, list) or not operations:
        _fail("INVALID_EDIT_PLAN", "operations must be a non-empty list")

    validated: list[dict[str, object]] = []
    for index, op in enumerate(operations):
        if not isinstance(op, dict) or op.get("type") not in OPERATION_TYPES:
            _fail("INVALID_EDIT_PLAN", f"operations[{index}] has an invalid or missing type")
        op_type = op["type"]
        for field_name in _OPERATION_REQUIRED_FIELDS[op_type]:
            if field_name not in op:
                _fail("INVALID_EDIT_PLAN", f"operations[{index}] ({op_type}) missing required field: {field_name}")

        anchor_id = operation_scope_id(op)
        if scope in ("element", "selection") and anchor_id not in selection_ids:
            _fail("OUT_OF_SCOPE_EDIT", f"operations[{index}] targets {anchor_id!r}, outside the selection", target=anchor_id)
        if anchor_id not in current_ids:
            code = "SELECTION_NO_LONGER_EXISTS" if anchor_id in selection_ids else "TARGET_NOT_FOUND"
            _fail(code, f"operations[{index}] target does not exist: {anchor_id!r}", target=anchor_id)

        if op_type == "semantic_tool" and op["tool"] not in SEMANTIC_TOOL_ALLOWLIST:
            _fail("INVALID_EDIT_PLAN", f"operations[{index}]: tool {op['tool']!r} is not in the P6 allowlist")

        validated.append(op)

    return validated
