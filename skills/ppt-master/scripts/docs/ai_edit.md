# Live AI Edit Loop (P6)

The Artifact Edit Transaction behind "select this, ask AI to change it,
get a safe undoable change back." Model decides the edit; Runtime owns
the mutation (SS1.1 of the P6 spec) — the model never touches a file
directly, and every mutation goes through the exact revision/scope/
native-object/validation machinery every other P1–P5 change already uses.

## Why there's no model API call anywhere in this file

Investigated before P6 was designed, not assumed: this codebase has no
precedent for Python code calling an external LLM API to make an
authoring decision. Strategist, Executor, and the pre-P6 manual
Annotate-apply flow are all the orchestrating Claude Code agent itself
reasoning over role-doc instructions and acting via tool calls —
`check_annotations.py` is the exact existing precedent for "list pending
work, let the agent read it and act." `AIEditRunner` follows that same
host-native pattern: `scripts/ai_edit_cli.py` is the agent's discovery
and submission surface, `workflows/stages/ai-edit.md` documents the
procedure. There is no `ANTHROPIC_API_KEY`, no provider routing, no new
framework — the "model" is whichever agent session is already running
this Skill.

## The one transaction

```
Selection -> Instruction -> AIEditContext -> EditPlan
  -> Scope/Revision Validation -> Deterministic Patch
  -> Revision++ -> Auto-Validate -> Workbench Refresh
```

1. **Create** (`POST /api/runtime/ai-edits` or `ai_edit_cli.py create`):
   captures `slide_id`, `expected_revision` (the slide's *current*
   revision, captured now — never trust a client-submitted number),
   `scope`, `selection_ids`, `instruction`. Compiles and persists the
   `AIEditContext` immediately (`runtime/ai_edit_context.py`, wraps P2's
   `build_page_context` — never a second Page Context). Job status:
   `queued`.
2. **Read context** (`GET .../context` or `ai_edit_cli.py context`): the
   orchestrating agent reads `object_facts` (one per selected id — `kind`,
   `text`, `bounds`, `style`, `parent_id`, `semantic_role`,
   `native_object_type`, all re-derived server-side from the *current*
   on-disk SVG, never from anything the browser submitted) plus whatever
   `page_context` P2 could compile (`null` with a warning on a route with
   no `design_spec.md`/`spec_lock.md`, e.g. Quick — not a failure).
3. **Reason.** The agent's own capability, no extra call.
4. **Submit** (`POST .../plan` or `ai_edit_cli.py submit-plan`): a
   structured EditPlan (`runtime/edit_plan.py`) — never a full SVG, never
   freeform text.
5. **Validate + Apply** (`runtime/patch_engine.py::apply_edit_plan`, one
   transaction): scope/target/tool-allowlist checked
   (`runtime/edit_plan.py::validate_edit_plan`) before a single byte is
   written; every operation applied to a deep-copied working tree; the
   Native Object Integrity Gate enforced; a structural round-trip check
   (well-formed XML, no duplicate ids); `before.svg` snapshotted; an
   atomic (`temp file + os.replace`) write; then the exact same
   `workbench.edit_wiring.record_direct_edit()` (revision++, dirty,
   export.dirty) and `tools.dispatch.invoke("slide.validate", ...)` (P4's
   PostToolUse binds `validated_revision`) every other direct edit uses.
6. **Refresh.** The Workbench's existing 2s poll (extended in P5) or the
   job's own 1.5s status poll picks up the result — no new refresh
   mechanism.

## EditPlan schema (`runtime/edit_plan.py`)

```json
{"scope": "element|selection|slide", "base_revision": 8,
 "summary": "...", "operations": [{"type": "...", ...}]}
```

Nine operation types, frozen for this version — do not add more without
reopening that decision: `set_text {target, value}`, `set_style {target,
style}`, `set_geometry {target, geometry}`, `translate {target, dx, dy}`,
`resize {target, width?, height?}`, `replace_fragment {target, fragment}`,
`insert_fragment {parent, fragment, index?}`, `delete_element {target}`,
`semantic_tool {target, tool, arguments}` (`tool` must be one of
`shape.create`/`chart.create`/`table.create`/`formula.create` — the only
`tools.dispatch.TOOLS` entries that are *authoring* operations).
`translate`/`resize`/`set_geometry` write through a `data-pptx-bounds`/
`data-pptx-frame` marker when present (keeping any companion
`data-pptx-x/y/width/height` attrs in sync), else plain `x`/`y`/`width`/
`height`/`cx`/`cy` attrs, else (translate only) a composed `transform`.

## Native Object Integrity Gate

Before any non-`semantic_tool` operation, the target (and every ancestor)
is checked for a native fingerprint — `data-pptx-replace-with` (incl.
legacy `data-pptx-native`), `data-pptx-authoring`, `data-pptx-object`,
`data-pptx-prst`, or a `<metadata type="application/json">` child. Any
match → `NATIVE_OBJECT_INTEGRITY_ERROR`, zero writes. Only a matching
`semantic_tool` call (e.g. `chart.create` on a chart) may touch it — the
sanctioned "regenerate through the real tool" path. `arguments.frame` is
auto-filled from the target's current bounds when the operation omits it,
so "make it a line chart" doesn't need to repeat the geometry.

## Conflict control (`runtime/edit_conflicts.py`)

A revision conflict at submit time — the slide changed since the job's
`base_revision` was captured — is never resolved by rewriting
`expected_revision` and replaying the same plan (hard-banned). Exactly one
automatic rebase: discard the plan, rebuild `AIEditContext` at the new
revision, confirm every `selection_id` still resolves
(`SELECTION_NO_LONGER_EXISTS` otherwise), job returns to `queued` for one
fresh plan. A second conflict after that one rebase →
`EDIT_CONFLICT_REQUIRES_RETRY`, job → `conflicted`, zero writes — the
human redoes the edit from scratch. `INVALID_EDIT_PLAN` gets one schema-
repair attempt (`plan_attempts` capped at 2 total submissions); a third
still-invalid submission fails permanently.

## Job lifecycle (`runtime/ai_edit_jobs.py`)

`queued -> applying -> completed | completed_with_validation_error |
failed | conflicted`, plus `cancelled` (from `queued`) and `interrupted`
(reconciliation only). Persisted under
`<project>/runtime/ai_edits/<job_id>/{job,request,context,plan,result}.json`
+ `before.svg` — never inside `build_state.json`, which only carries two
small pointers per slide (`ai_edit_active_job`, `ai_edit_last_job`, both
optional/backward-compatible). "One active AI mutator per slide" is
enforced by scanning *persisted* job status, not an in-memory map — jobs
are created/submitted from two different processes (the long-running
Workbench and one-shot `ai_edit_cli.py` invocations), so only durable
state is trustworthy across both. `reconcile_interrupted_jobs()` runs once
at Workbench startup: any job not yet in a terminal status is flipped to
`interrupted` — never auto-applies a leftover plan.

## Undo

Every successful apply snapshots `before.svg`. Undo restores it verbatim,
then bumps revision *forward* through the same `record_direct_edit` +
`slide.validate` path — revision is monotonic, it never decrements back
to the pre-edit number. `UNDO_CONFLICT` if the slide moved on since the
AI edit's own result revision (`runtime.edit_conflicts.check_undo_conflict`)
— no three-way merge.

## Annotation integration

"Apply with AI" on an existing annotation creates the exact same job
(`origin: "annotation"`, `scope: "element"`, `selection_ids: [that
element's id]`, `instruction: <the annotation text>`) — never a second
pipeline. On a validated success, the element's `data-edit-target`/
`data-edit-annotation` attrs are cleared in the same atomic write (bypasses
`is_editable_attr`'s user-facing protection deliberately — this is
sanctioned system cleanup, not a user edit). **Known trade-off**: this
clearing happens whenever the apply itself succeeds, before validation's
outcome is known, so `completed_with_validation_error` jobs also clear
the on-disk annotation attributes — `check_annotations.py`'s file-attribute
scan will no longer show them as pending. The job's own persisted status
(visible via `GET /api/runtime/ai-edits/<id>` and the Workbench's job
panel) is the authoritative "did this actually resolve cleanly" signal
for annotations applied through this pipeline; it does not re-litigate
that decision through file attributes a second way.

## Error vocabulary

`{"ok": false, "errors": [{"code": ..., "message": ...}]}` — the P3
envelope. `STALE_EDIT`, `EDIT_CONFLICT_REQUIRES_RETRY`,
`SELECTION_NO_LONGER_EXISTS`, `INVALID_EDIT_PLAN`, `OUT_OF_SCOPE_EDIT`,
`TARGET_NOT_FOUND`, `NATIVE_OBJECT_INTEGRITY_ERROR`, `SLIDE_AI_EDIT_BUSY`,
`AI_EDIT_CONTEXT_TOO_LARGE`, `UNDO_CONFLICT`, `AI_EDIT_CANCELLED`.
(`SCOPE_ESCALATION_REQUIRED` and a dedicated auto-repair loop are not
implemented in this version — see What's not in this version below.)

## API surface

```
POST /api/runtime/ai-edits                 create (browser or origin=annotation)
GET  /api/runtime/ai-edits                 list (?slide=, ?status=)
GET  /api/runtime/ai-edits/<id>             job detail
GET  /api/runtime/ai-edits/<id>/context     compiled AIEditContext
POST /api/runtime/ai-edits/<id>/plan        submit EditPlan -> validate+apply
POST /api/runtime/ai-edits/<id>/cancel
POST /api/runtime/ai-edits/<id>/retry       spawns a fresh job (SS35, SS41's "Retry on latest version")
POST /api/runtime/ai-edits/<id>/undo
```

CLI mirror (`scripts/ai_edit_cli.py`): `list`, `create`, `context`,
`submit-plan`, `cancel`, `retry`, `undo` — calling the exact same
`runtime.ai_edit_jobs` functions the Flask routes call.

## What's not in this version (explicit non-goals, do not add without reopening scope)

Full AI chat, a model router/provider framework, parallel AI edits,
multi-user collaboration, an automatic repair loop after a validation
failure, multiple generated design alternatives, full revision-history
timeline/branching, whole-slide-rewrite as anything other than a last
resort, sub-group-level object identity (P6.0's audit found no real need
for it), an API-key-gated autonomous model backend (host-native only, by
explicit user decision during planning).
