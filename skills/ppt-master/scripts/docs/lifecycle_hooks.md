# Lifecycle Hooks (P4)

Deterministic, idempotent checks the Build Controller runs automatically
around the Semantic Tool Layer, so discipline that used to live only in
Markdown prose (`workflows/generate-pptx.md`: "check the gate before
exporting," "call `submit-slide` as bookkeeping," "don't claim completion
early") is now *enforced by the harness*, not just written for the model to
remember. Four lifecycle points, no more: **PreToolUse**, **PostToolUse**,
**Stop**, **Resume**. Not real Claude Code hooks — this is the Skill's own
Build Controller concept, named to match that mental model, wired directly
into `scripts/semantic_tools.py` (Pre/Post) and two `scripts/build_state.py`
subcommands (`stop-check`, and `resume`'s `reconciliation` field).

**Hard rules** (do not relax without reopening the P4 plan):
- A hook never calls a model. It inspects `build_state.json` (and, for
  Resume, re-hashes a file already recorded in it), validates, and
  transitions state — nothing else.
- A hook never mutates a page SVG or any other authored artifact. Finding
  `TEXT_OVERFLOW` is `slide.validate`'s job; deciding how to fix it stays
  the agent's.
- A hook never calls another tool or another hook. `scripts/semantic_tools.py`
  calls hooks; hooks never call `scripts/semantic_tools.py`, `svg_to_pptx.py`,
  or `svg_quality_checker.py` themselves.
- Only three decisions exist: `allow`, `warn`, `block`. No `MODIFY_ARGS`,
  `DEFER`, `ASK_USER`, or `AUTO_RETRY` in this version.
- A project with no `build_state.json` always `allow`s — P4 never makes
  build_state adoption mandatory, matching `resume_report()`'s own
  `"legacy"` fallback.

## `HookResult` (`scripts/runtime/hook_types.py`)

```json
{"hook": "PreToolUse", "tool": "deck.export", "decision": "block",
 "code": "DECK_NOT_EXPORTABLE", "message": "...",
 "details": {"reasons": [{"type": "stale_validation", "slide": "P03", "revision": 8, "validated_revision": 7}]}}
```

`decision` is `allow` | `warn` | `block`; `code`/`message` are required for
anything other than `allow`. This short form is what the agent sees (as a
warning string folded into the tool's own envelope, or as the `errors` entry
of a raised `ToolError` for an enforced block); the full context always also
goes to `validation/workflow.log` as `HOOK_PRE_TOOL` / `HOOK_POST_TOOL` /
`HOOK_STOP` / `HOOK_RESUME` entries (only for non-`allow` decisions — a
routine `allow` is never logged, to avoid flooding a log that
`artifact-ownership.md` documents as "inspect only on explicit user
request").

## Shadow vs. enforce (`build_state.json` `hooks.mode`, default `"shadow"`)

```
python3 scripts/build_state.py set-hooks-mode <project_path> <shadow|enforce>
```

In `"shadow"` mode, a PreToolUse rule that would `block` instead downgrades
to a `warn` — `"[shadow] would block: <message>"` — and the tool runs
anyway. In `"enforce"` mode the same `block` raises `tools.errors.ToolError`
straight through `scripts/semantic_tools.py`'s existing `except ToolError`
handler, so `{"ok": false, "errors": [...]}` and exit code 1 are unchanged;
no new envelope shape for the caller to learn. Rollout: replay a corpus in
shadow first, read `workflow.log`'s `HOOK_PRE_TOOL decision=block
mode=shadow` entries for false positives, only then flip to `enforce`.

## PreToolUse(`deck.export`) — `scripts/runtime/hook_rules/export.py`

The one hook explicitly asked for first. Walks `build_state.json` and
collects `details.reasons` (one stable top-level code,
`DECK_NOT_EXPORTABLE`, carrying every reason found — not a code per
condition):

| `reasons[].type` | Condition |
|---|---|
| `stale_slide` / `building_slide` / `failed_slide` | a slide's `status` is `stale` / `building` / `failed` |
| `stale_validation` | a `ready`/`dirty` slide's `revision != validated_revision` — no `slide.validate` has run against its *current* content |
| `final_gate_not_passed` | `validation.final_gate != "passed"` |
| `pending_job` | a job is `queued` or `in_progress` |
| `plan_revision_mismatch` | a slide's `plan_revision` lags the deck's current one (belt-and-suspenders; `bump_plan_revision` should already have cascaded it to `stale`) |

No reasons → `allow` (or `warn` with code `EARLY_GATE_PENDING` if
`early_gate` is still `pending` on a >6-page deck that legitimately hasn't
reached it yet — never blocking on that alone).

## PostToolUse — `scripts/runtime/hook_rules/{export,validation}.py`

- **`deck.export`**: on a successful export, automatically calls the same
  `controller.set_export(dirty=False, last_export=...)` +
  `controller.set_phase("exported")` the Executor used to be asked to call
  by hand in `generate-pptx.md` Step 7.3 — plus records
  `export.plan_revision` / `export.deck_revision` (the latter = sum of all
  slide revisions at export time, an audit trail only; the actual gate
  keeps using `export.dirty` + the live `reasons` list above, never this
  number). A failed export (`fields.ok` false) leaves `build_state.json`
  completely untouched.
- **`slide.validate`**: binds `validated_revision = <slide's current
  revision>` and `validation_status` (`"passed"` / `"passed-with-warnings"`
  / `"failed"`, from the tool's own `ok` + `warnings`) under the same file
  lock `submit-slide` uses. A whole-deck call (no `page`) stamps every
  currently-known slide; an unparseable `page` basename skips silently
  (optional bookkeeping, never a hard failure). This binding, not a
  separate "is this stale" inference, is what PreToolUse(`deck.export`)
  compares against.
- **`shape.create` / `formula.create` / `chart.create` / `table.create`**:
  local-only — asserts `artifact_id`/`native_type`/`svg_fragment` are
  present on the result. No project path, no `build_state.json` touch:
  these four tools return a fragment with no project of their own
  (`references/artifact-ownership.md`). Page-level validation after a
  fragment is inserted is `slide.validate`'s job (or a future `slide.patch`
  PostToolUse, P5+) — not this hook's.

## Stop — `build_state.py stop-check` (`scripts/runtime/hook_rules/stop.py`)

```
python3 scripts/build_state.py stop-check <project_path> [--json] [--max-blocks 3]
```

Wraps the existing `resume_report()` (P1) rather than reimplementing it —
"nothing left to do" and "safe to call done" are the same question. `mode:
"legacy"` or `next_action: "nothing_pending"` → `{"decision": "allow"}`,
resets `hooks.stop_block_count` to `0`. Anything else increments
`stop_block_count`; while `<= --max-blocks` (default 3), returns
`{"decision": "block", "code": "BUILD_INCOMPLETE", "remaining": [...]}`
(exit code `5`). Once it exceeds `--max-blocks`, it stops blocking and
surfaces a failure instead — `{"status": "failed", "reason":
"QUALITY_GATE_UNRESOLVED", "blocking_errors": [...]}` (exit code `6`) — so
an unresolved gate surfaces to the user rather than looping the model
forever. Fixing the underlying condition and calling `stop-check` again
resets the counter on the next `allow`.

## Resume — `build_state.py resume`'s `reconciliation` field (`scripts/runtime/hook_rules/resume.py`)

Answers a narrower question than the full quality pipeline: not "is this
deck good," but "does `build_state.json` still match what is on disk" after
a context loss. `resume --json` gains a read-only-by-default
`reconciliation` array:

| `code` | Condition |
|---|---|
| `ARTIFACT_MISSING` | a recorded `svg_path` (or `design_spec.md`/`spec_lock.md` on a non-`quick` route, or `export.last_export` once `phase == "exported"`) no longer exists on disk |
| `EXTERNAL_ARTIFACT_CHANGE` | the file exists but its current sha256 no longer matches the slide's `content_hash` — Live Preview or a human edited it directly after the last `submit-slide` |

`resume --json` never mutates on its own. `--apply-reconciliation` flips
every `EXTERNAL_ARTIFACT_CHANGE` slide's `status` to `"dirty"` under the
same lock `submit-slide` uses; `ARTIFACT_MISSING` is never auto-healed
either way — a missing file needs a human/agent decision, not a silent
status flip. `svg_path` is populated by `submit-slide --from-file`
(recorded relative to the project, POSIX-style) — without it there is
nothing to re-hash, which is why P4 added the field.

## Adopting hooks on a pre-P4 project

`build_state.json` written before P4 loads unchanged (`hooks.mode`
defaults to `"shadow"`, every slide's `validated_revision`/`validation_status`
default to `null`/absent). The first `deck.export` after adoption will
correctly report `stale_validation` for every slide that was never run
through the new `slide.validate` binding — this is not a false positive,
it is P4 accurately saying "no evidence exists yet, under the new rule, that
this exact revision was checked." Running `slide.validate` once (whole-deck
or per-page) against the current content clears it, exactly like establishing
a fact for the first time. Confirmed against a real pre-P4 project during
this phase's regression (see `references/benchmarking/corpus.md`
`hooks-p4-adoption` / `hooks-p4-fault-injection`).
