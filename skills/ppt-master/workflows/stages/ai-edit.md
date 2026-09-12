---
description: Orchestrating-agent procedure for the P6 Live AI Edit Loop -- discover a queued AI Edit job, read its compiled context, reason about the instruction, submit a structured EditPlan.
---

# AI Edit Stage

> This is the "host-native `AIEditRunner`" the P6 plan settled on after confirming this codebase has no precedent for calling an external model API to make an authoring decision — every AI decision in this Skill is the orchestrating agent's own reasoning, expressed as a tool call. This stage formalizes that for AI Edit jobs the exact same way [`live-preview.md`](live-preview.md) Step 2 already formalized it for annotations (which now route through this same pipeline). Full contract, EditPlan schema, error codes, and the Native Object Integrity Gate: [`ai_edit.md`](../../scripts/docs/ai_edit.md).

## When to Run

- The user (via the Workbench's **Ask AI** panel or **Apply with AI** on an annotation) submitted an instruction and a job is sitting `queued`.
- The user asks in chat to apply an AI edit to a selection/element/whole slide on a `build_state.json`-adopted project.

**When not to run**: no `build_state.json` on the project (there is no AI Edit job system to run against — edit the SVG directly per the user's instruction, or fall back to [`live-preview.md`](live-preview.md)'s legacy Annotate-apply path); a precise deterministic tweak with no ambiguity ("change the fill to #FF0000") — editing the SVG attribute directly, or using the Semantic Tool Layer directly, is simpler and skips the job/context/plan ceremony entirely.

---

## Step 1: Discover

```bash
python3 ${SKILL_DIR}/scripts/ai_edit_cli.py list <project_path> --status queued --json
```

Mirrors `check_annotations.py`'s existing discovery role. Each entry is one job (`job_id`, `slide_id`, `scope`, `selection_ids`, `instruction`, `base_revision`). Nothing queued → nothing to do, stop here.

To start a job yourself instead of waiting for one from the Workbench (e.g. the user asked in chat, not through the browser):

```bash
python3 ${SKILL_DIR}/scripts/ai_edit_cli.py create <project_path> --slide <PNN> \
    --scope element|selection|slide --selection <id1,id2,...> \
    --instruction "<the user's instruction>" [--json]
```

`--selection` is required and non-empty for `element`/`selection` scope; omit it for `slide` scope. Never guess a `slide` scope from an ambiguous instruction — only use it when the user explicitly asked to edit the whole page (SS3/SS37 of the P6 spec: element/selection is the default, `slide` scope requires an explicit ask).

## Step 2: Read the compiled context

```bash
python3 ${SKILL_DIR}/scripts/ai_edit_cli.py context <project_path> <job_id> --json
```

This is re-derived from the *current* on-disk SVG every time — never reuse a context you read earlier in the conversation, even for the same job (a rebase after a conflict recompiles it). `object_facts[]` gives each selected id's `kind`/`text`/`bounds`/`style`/`parent_id`/`semantic_role`/`native_object_type`; `page_context` carries whatever P2 could compile (`null` with a warning on a route with no Design Spec, e.g. Quick — proceed anyway, just without the deck-wide lock facts).

**Hard rule**: if any selected object's `kind` is `chart`/`table`/`formula`, or `native_object_type` is set, the only EditPlan operation you may target it with is `semantic_tool` (calling `chart.create`/`table.create`/`formula.create` with the new arguments) — never `set_text`/`set_style`/`set_geometry`/`translate`/`resize`/`replace_fragment`/`delete_element` directly on it. The Runtime enforces this (`NATIVE_OBJECT_INTEGRITY_ERROR`), but reasoning it out up front avoids a wasted round trip.

## Step 3: Reason, then write the EditPlan

Your own judgement, no separate tool call. Prefer the narrowest operation that satisfies the instruction — a wording tweak is `set_text`, a nudge is `translate`, a resize is `resize`; reach for `replace_fragment`/`insert_fragment`/`delete_element` only when the instruction genuinely requires structural change, and `slide` scope only when the job itself was created with that scope. See [`ai_edit.md`](../../scripts/docs/ai_edit.md) for the full nine-operation vocabulary and exact field shapes. Write the plan to a file:

```json
{"scope": "selection", "base_revision": 8, "summary": "...",
 "operations": [{"type": "set_text", "target": "title-01", "value": "..."}]}
```

`base_revision` must equal the context's `expected_revision` you just read (not an earlier one).

## Step 4: Submit

```bash
python3 ${SKILL_DIR}/scripts/ai_edit_cli.py submit-plan <project_path> <job_id> --plan-file <path> --json
```

Branch on the result:

| Outcome | What happened | Next |
|---|---|---|
| `completed` | Applied and validated clean | Tell the user; the Workbench refreshes on its own poll |
| `completed_with_validation_error` | Applied, but `slide.validate` found an issue | Report the validation message; offer **Undo** (`ai_edit_cli.py undo <project_path> <job_id>`) or ask the user how to proceed — never loop back and auto-repair (SS62: one edit, one validate, per attempt) |
| `STALE_EDIT` | The slide changed since `base_revision`; one automatic rebase already happened | Re-run Step 2 (fresh context, new `base_revision`), reason again against the *current* page, submit once more — this is the only allowed retry |
| `EDIT_CONFLICT_REQUIRES_RETRY` | The slide changed a second time during that one rebase | Stop. Tell the user the page changed again while thinking and the instruction needs to be resubmitted from scratch (a fresh `create`) |
| `SELECTION_NO_LONGER_EXISTS` | A selected id no longer resolves | Stop. Tell the user the object they selected was removed; do not guess a replacement target |
| `INVALID_EDIT_PLAN` | Schema/scope/allowlist rejected the plan | One retry allowed with a corrected plan (`plan_attempts` capped at 2 total); a second rejection stops |
| `OUT_OF_SCOPE_EDIT` / `NATIVE_OBJECT_INTEGRITY_ERROR` | The plan reached outside the selection, or touched a native object directly | Stop, rewrite the plan correctly, submit again — this is not a retryable server-side condition, it's a plan bug |
| `SLIDE_AI_EDIT_BUSY` | Another job is already active on this slide | Wait, or check on the other job first (`ai_edit_cli.py list --slide <PNN>`) |
