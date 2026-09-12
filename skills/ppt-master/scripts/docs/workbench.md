# Unified Workbench (P5)

One persistent server process (`scripts/svg_editor/server.py`, extended —
not a new server) and one browser tab spanning the whole project
lifecycle: confirmation, generation, editing, validation, export. Replaces
the old pattern of a separate Confirm UI server the Executor launches,
polls with a blocking `--wait-only` loop (up to 590s), and shuts down
before Design Spec authoring even begins.

**Hard rule — UI Adapter, never Runtime**: every route in
`scripts/workbench/` reads or transitions state through an already-existing
P1–P4 function (`runtime.build_state`, `tools.dispatch`,
`runtime.hook_rules.*`) or through `confirm_ui.server`'s own real,
unmodified logic. No route recomputes whether a slide is valid, whether
export is allowed, or what "done" means — the Workbench displays Runtime
Truth, it never creates it (`references/artifact-ownership.md`).

## Launch

Unchanged CLI, now doing more:

```bash
python3 ${SKILL_DIR}/scripts/svg_editor/server.py <project_path> --live --daemon
```

No new flag. `create_app()` now additionally registers the routes below;
the existing lock (`live_preview/lock.json`), idle watchdog, and
`/api/shutdown` are untouched.

## Routes

Existing (`svg_editor/server.py`, unchanged): `/`, `/api/config`,
`/api/health` (now also carries `session_id`, `active_slide`, and a
`build_state` summary), `/images/<path>`, `/api/slides`,
`/api/slide/<name>`, `/api/slide/<name>/annotate[/…]`,
`/api/slide/<name>/edit`, `/api/slide/<name>/undo`, `/api/save-all`,
`/api/shutdown`.

New (`scripts/workbench/runtime_api.py`):

```
GET  /api/runtime/project              project/route/phase/plan_revision/deck_revision/hooks.mode
GET  /api/runtime/build-state          full state + build_progress() + export_status()
GET  /api/runtime/slides               per-slide view models (workbench/view_model.py)
GET  /api/runtime/slides/<id>          one slide's detail
POST /api/runtime/slides/<id>/validate → tools.dispatch.invoke("slide.validate", {project, page})
POST /api/runtime/deck/validate        → tools.dispatch.invoke("slide.validate", {project, stage})
POST /api/runtime/deck/export          → tools.dispatch.invoke("deck.export", {project, ...body})
GET  /api/runtime/resume               resume_report() + reconcile(apply=False) -- read-only
POST /api/runtime/resume/apply         → reconcile(project, apply=True) -- the only mutating resume call
GET  /api/runtime/stop-check           → hook_rules.stop.stop_check(project)
GET  /api/runtime/plan                 Plan Panel fields (workbench/plan_reader.py)
POST /api/runtime/active-slide         session bookkeeping only (app.config, process-lifetime)
```

New (`scripts/workbench/confirm_api.py`) — proxies to a real, isolated
`confirm_ui.server.create_app()` instance via Werkzeug's in-process test
client, never a subprocess:

```
GET  /api/confirm/state      -> confirm_ui's real /api/recommendations
GET  /api/confirm/session    -> confirm_ui's real /api/session
GET  /api/confirm/catalogs   -> confirm_ui's real /api/catalogs
POST /api/confirm/submit     -> confirm_ui's real /api/confirm (writes result.json / template_selection.json)
```

Every response is `{"ok": bool, ...}` (the P3 envelope shape) at HTTP 200 —
`ok`/`error` in the body carries success/failure, matching how the CLI
tools already communicate; the confirm proxy passes through whatever
status code confirm_ui's own route returned (it is real HTTP-shaped Flask
response forwarding, not re-wrapped).

**Why `confirm_ui.server.create_app()` and not hand-picked helper
functions**: confirm_ui's Stage-2 validation is a long chain of ~15
interlocking private validators (typography, palette, candidate lists,
submission-stage checks). Reusing its real `create_app()` output through an
in-process test client runs that exact, unmodified logic — the only
alternative (hand-porting individual `_stage2_*_error` functions) risks
subtly reimplementing the rule instead of reusing it. **Hard rule**:
`create_app()` is always called with `idle_timeout=0, lock_file=None` —
confirm_ui's own `create_app()` unconditionally starts a background thread
that calls `os._exit()` on the whole process once idle past its timeout
(confirmed by reading `confirm_ui/server.py` directly); `idle_timeout=0`
makes that thread return immediately and never loop, which is required so
a confirm_ui-owned watchdog can never kill the Workbench's own long-lived
process. This module never calls confirm_ui's own `/api/shutdown` route.

## The one new piece of runtime logic: direct-edit revision wiring

`scripts/workbench/edit_wiring.py::record_direct_edit()` is called from
`svg_editor/server.py`'s `save_all()`, immediately after each successful
`tree.write(...)`, per file. It resolves the filename to a slide id
(`runtime.slide_id.slide_id_from_page` — shared with P4's `slide.validate`
PostToolUse binding, not duplicated), then calls
`runtime.revisions.submit_slide(..., status="dirty", from_file=svg_file)`
under the same CAS every other P1–P4 mutation uses. No `build_state.json`
→ no-op (legacy project). Unmappable filename → no-op. A `StaleEditError`
(another writer bumped the revision between the read and the call) is
retried up to 3 times with a fresh revision read — safe because this
mutation is always "bump from wherever it currently is," never a content
merge; true conflict UX is P6's job. A failure here never blocks the
actual SVG save — the file is already safely on disk regardless of this
bookkeeping.

## View-model vocabulary (`scripts/workbench/view_model.py`)

Pure mapping, no Flask/IO — unit-tested in complete isolation.

| Internal `status` | Base label | Overridden when `ready`/`dirty` and validation isn't `valid` |
|---|---|---|
| `planned` | Planned | never |
| `building` | Building | never |
| `ready` | Ready | → "Needs validation" (not yet/stale) or "Validation failed" |
| `dirty` | Modified | → "Needs validation" or "Validation failed" |
| `validating` | Validating | never |
| `failed` | Failed | never |
| `stale` | Stale | never |

| `validation_state` | Label |
|---|---|
| `not_validated` | Not validated |
| `valid` | Validated |
| `stale_validation` | Outdated |
| `failed` | Validation failed |

Export status (`export_status()`) is never "does the pptx file exist on
disk" — it's `never` (no `last_export` yet) / `outdated`
(`export.dirty`) / `ready` (clean), the same fields P4's `deck.export`
PreToolUse hook already gates on.

## Shadow / enforce display

`hooks_mode_view()`: `"shadow"` → "Advisory", `"enforce"` → "Enforced" —
shown as a small topbar badge, never as raw hook terminology. A
shadow-mode export that would have blocked still runs (`ok: true`) with a
`"<CODE>: [shadow] would block: ..."` entry folded into the same
`warnings` array `tools.dispatch.invoke()` already returns — the Workbench
renders it as a plain warning banner, not a new UI concept.

## What the Workbench never shows by default

`svg_quality_checker.py`, `build_state.py`, `hook_registry.py`,
`PostToolUse`/`PreToolUse`, OMML/DrawingML, `semantic_tools.py`,
`resume_report` — all of that lives behind the Diagnostics drawer
(`#workbench-diagnostics`, raw `/api/runtime/build-state` JSON), never the
main panels.

## Legacy Mode

`confirm_ui/server.py`'s own standalone launch/`--wait-only`/`--shutdown`
sequence and `svg_editor/server.py`'s plain (non-Workbench-aware — i.e.
just never calling the new routes) usage are both completely unmodified
and remain available; `workflows/generate-pptx.md` keeps that sequence
documented as the Legacy fallback branch alongside the new default.
