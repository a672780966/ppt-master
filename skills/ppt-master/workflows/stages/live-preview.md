---
description: Main-pipeline editor stage for starting live preview and applying submitted annotations.
---

# Live Preview Stage

> (1) Start/reopen the browser SVG editor when no preview service is running; (2) apply user-submitted annotations after Step 7 export. Executor's mandatory auto-startup lives in [`generate-pptx`](../generate-pptx.md) Step 6 — never re-launch a preview that is already running. Editor behavior, lifecycle, ports, and remote access are documented in [`svg_editor.md`](../../scripts/docs/svg_editor.md).

## When to Run

- **Step 1** — no preview service is running and the user wants to look at the deck or click an element (post-export re-entry in a fresh chat, or the user clicked **Exit preview** earlier).
- **Step 2** — Step 7 has produced at least one PPTX and the user signals that annotations should be applied: quoting the browser prompt (`Changes saved to svg_output...` / `修改已保存到 svg_output...`) or saying `apply my annotations` / `apply my edits` / `应用注解` / `开始应用`.

**When not to run**: the service is already running → give the URL; a precise chat edit ("change page 3 title to X") → edit the SVG directly; a full regeneration → main workflow; Step 7 has never run → finish the main pipeline first.

---

## Step 1: Start / reopen the editor

```bash
python3 ${SKILL_DIR}/scripts/svg_editor/server.py <project_path> --daemon
```

Plain mode, no `--live` (reserved for Step 6). Launch immediately — the user already asked. Report the actual URL from the launch output or `<project_path>/live_preview/lock.json`, never an inferred `6060`; on a remote host add `--no-browser` and forward the port. Then tell the user, in their language, in one short message:

- the editor URL;
- **Direct edit** for deterministic tweaks (wording, color, coordinates, attributes): select an element → change the right-panel controls → preview updates immediately, nothing is written until **Apply changes**; `Ctrl+Z` / **Undo** drops staged edits; re-export stays chat-driven ("re-export" / "重新导出");
- **Annotate** for changes needing AI judgement or re-layout: select an element → write the instruction (optionally from a quick type such as move / resize / replace image / copy / relayout) → **Add annotation** → **Apply changes** → return to chat and say `apply my annotations` (or, on a build_state-adopted project, click **Apply with AI** directly in the Workbench — see Step 2 and [`ai_edit.md`](../../scripts/docs/ai_edit.md));
- to skip the editor, describe the change in chat.

---

## Step 2: Apply submitted annotations

🚧 **GATE**: `<project_path>/exports/` contains at least one `*.pptx`; otherwise tell the user to finish the main pipeline first.

**When `<project_path>/build_state.json` exists** (P6 Live AI Edit Loop, [`ai_edit.md`](../../scripts/docs/ai_edit.md)): each pending annotation goes through the same revision-guarded, undoable AI Edit pipeline "Apply with AI" in the Workbench uses — not a manual file edit.

1. `python3 ${SKILL_DIR}/scripts/ai_edit_cli.py list <project_path> --status queued` (a queued job already exists if the user clicked **Apply with AI** in the Workbench) — or, to formalize a pending annotation from `check_annotations.py`'s list yourself: `python3 ${SKILL_DIR}/scripts/ai_edit_cli.py create <project_path> --slide <PNN> --scope element --selection <element_id> --instruction "<the annotation text>" --origin annotation --annotation-id <element_id>`.
2. `python3 ${SKILL_DIR}/scripts/ai_edit_cli.py context <project_path> <job_id>` — read the compiled `AIEditContext` (the selected object's real, current facts; never trust anything cached from an earlier read).
3. Reason about the instruction, then `python3 ${SKILL_DIR}/scripts/ai_edit_cli.py submit-plan <project_path> <job_id> --plan-file <path>` with a structured EditPlan (never a full SVG rewrite for a targeted change).
4. On `STALE_EDIT` (one automatic rebase already happened — see `ai_edit.md`): re-run `context`, reason again, `submit-plan` once more. On `EDIT_CONFLICT_REQUIRES_RETRY`: tell the user the page changed again during the edit and the instruction needs to be redone. On `completed`: continue to Step 3. On `completed_with_validation_error`/`failed`: report the validation/error message and offer to undo (`ai_edit_cli.py undo <project_path> <job_id>`) or retry.
5. More annotations → repeat from 1; "done" or "stop preview" → end.

**Legacy (no `build_state.json` on this project)**:

1. `python3 ${SKILL_DIR}/scripts/check_annotations.py <project_path>` — its output lists each pending change as `file → element_id → annotation text → content preview`; use it directly as the to-do list. If it reports none, tell the user and stop.
2. For each annotation: edit the targeted element in `<project_path>/svg_output/<file>` per the text; remove `data-edit-target` and `data-edit-annotation` from it; append one `annotation_applied` JSONL record (`ts`, `file`, `element_id`, original text) to `<project_path>/live_preview/annotations.jsonl`.
3. Re-enter [`generate-pptx`](../generate-pptx.md) Step 7.2, wait for its success criterion, then run Step 7.3; rerun Step 7.1 only when speaker notes changed.
4. Tell the user, in their language: annotations applied, new PPTX exported, preview still running (refresh or reselect the page if the browser shows the old slide).
5. More annotations → repeat from 1; "done" or "stop preview" → end.

