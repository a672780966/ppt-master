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
    set_style         {target, style: {attr: scalar-or-null, ...}}
    set_geometry      {target, geometry: {x/y/width/height/...: number}}
    translate         {target, dx, dy}
    resize            {target, width?, height?}
    replace_fragment  {target, fragment: "<svg fragment>"}
    insert_fragment   {parent, fragment: "<svg fragment>", index?}
    delete_element    {target}
    semantic_tool     {target, tool, arguments: {...}}  -- tool must be
                       in SEMANTIC_TOOL_ALLOWLIST (the only tools that are
                       *authoring* operations in tools.dispatch.TOOLS)

Usage:
    from runtime.edit_plan import validate_edit_plan, EditPlanError
    operations = validate_edit_plan(plan, scope=job.scope,
                                     selection_ids=job.selection_ids,
                                     current_ids=current_ids,
                                     expected_base_revision=job.base_revision)

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

# set_geometry is deliberately narrower than set_style: it may only carry
# numeric SVG geometry coordinates/dimensions. This prevents a malformed
# model plan from smuggling presentation attributes through the geometry path.
_GEOMETRY_FIELDS = frozenset({
    "x", "y", "width", "height", "cx", "cy", "r", "rx", "ry",
    "x1", "y1", "x2", "y2", "dx", "dy",
})
_STYLE_SCALAR_TYPES = (str, int, float)


class EditPlanError(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _fail(code: str, message: str, **details: object) -> None:
    raise EditPlanError(code, message, **details)


def _is_number(value: object) -> bool:
    # bool is an int subclass in Python but is never a meaningful SVG number.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require_nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("INVALID_EDIT_PLAN", f"{label} must be a non-empty string")
    return value


def _validate_operation_payload(op: dict[str, object], index: int) -> None:
    """Validate operation field *types*, not just field presence.

    Keeping this here means Patch Engine can trust a validated plan and will
    not strand a job in `applying` because `float()`, `.items()`, or `dict()`
    encountered an unexpected model-produced value type.
    """
    op_type = str(op["type"])
    prefix = f"operations[{index}] ({op_type})"

    if op_type == "insert_fragment":
        _require_nonempty_string(op.get("parent"), f"{prefix}.parent")
    else:
        _require_nonempty_string(op.get("target"), f"{prefix}.target")

    if op_type == "set_text":
        if not isinstance(op.get("value"), str):
            _fail("INVALID_EDIT_PLAN", f"{prefix}.value must be a string")

    elif op_type == "set_style":
        style = op.get("style")
        if not isinstance(style, dict):
            _fail("INVALID_EDIT_PLAN", f"{prefix}.style must be an object")
        for key, value in style.items():
            if not isinstance(key, str) or not key:
                _fail("INVALID_EDIT_PLAN", f"{prefix}.style keys must be non-empty strings")
            if value is not None and (not isinstance(value, _STYLE_SCALAR_TYPES) or isinstance(value, bool)):
                _fail(
                    "INVALID_EDIT_PLAN",
                    f"{prefix}.style[{key!r}] must be a string/number/null",
                )

    elif op_type == "set_geometry":
        geometry = op.get("geometry")
        if not isinstance(geometry, dict) or not geometry:
            _fail("INVALID_EDIT_PLAN", f"{prefix}.geometry must be a non-empty object")
        for key, value in geometry.items():
            if key not in _GEOMETRY_FIELDS:
                _fail("INVALID_EDIT_PLAN", f"{prefix}.geometry has unsupported field: {key!r}")
            if not _is_number(value):
                _fail("INVALID_EDIT_PLAN", f"{prefix}.geometry[{key!r}] must be numeric")

    elif op_type == "translate":
        if not _is_number(op.get("dx")) or not _is_number(op.get("dy")):
            _fail("INVALID_EDIT_PLAN", f"{prefix}.dx/.dy must be numeric")

    elif op_type == "resize":
        width = op.get("width")
        height = op.get("height")
        if width is None and height is None:
            _fail("INVALID_EDIT_PLAN", f"{prefix} requires width and/or height")
        for name, value in (("width", width), ("height", height)):
            if value is None:
                continue
            if not _is_number(value) or float(value) <= 0:
                _fail("INVALID_EDIT_PLAN", f"{prefix}.{name} must be a positive number")

    elif op_type in ("replace_fragment", "insert_fragment"):
        _require_nonempty_string(op.get("fragment"), f"{prefix}.fragment")
        if op_type == "insert_fragment" and "index" in op and op["index"] is not None:
            if type(op["index"]) is not int or op["index"] < 0:
                _fail("INVALID_EDIT_PLAN", f"{prefix}.index must be a non-negative integer")

    elif op_type == "semantic_tool":
        _require_nonempty_string(op.get("tool"), f"{prefix}.tool")
        if not isinstance(op.get("arguments"), dict):
            _fail("INVALID_EDIT_PLAN", f"{prefix}.arguments must be an object")


def operation_scope_id(op: dict[str, object]) -> str:
    """The one id an operation's scope check is anchored on."""
    return str(op["parent"]) if op["type"] == "insert_fragment" else str(op["target"])


def validate_edit_plan(
    plan: object,
    *,
    scope: str,
    selection_ids: list[str],
    current_ids: set[str],
    expected_base_revision: int | None = None,
) -> list[dict[str, object]]:
    """Validate an EditPlan's shape, types, revision, scope, targets and tools.

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
    if type(plan["base_revision"]) is not int or plan["base_revision"] < 0:
        _fail("INVALID_EDIT_PLAN", "base_revision must be a non-negative integer")
    if expected_base_revision is not None and plan["base_revision"] != expected_base_revision:
        _fail(
            "INVALID_EDIT_PLAN",
            f"EditPlan base_revision {plan['base_revision']} does not match the job's base revision {expected_base_revision}",
            plan_base_revision=plan["base_revision"],
            expected_base_revision=expected_base_revision,
        )
    if "summary" in plan and not isinstance(plan["summary"], str):
        _fail("INVALID_EDIT_PLAN", "summary must be a string when present")

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

        _validate_operation_payload(op, index)

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
