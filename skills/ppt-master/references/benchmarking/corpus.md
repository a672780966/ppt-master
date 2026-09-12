# P0 Benchmark Corpus

Fixed, version-controlled corpus for measuring PPT Master runtime changes
against a real before/after number. This is infrastructure for the "P0
Baseline Benchmark" phase of the Build Runtime upgrade — it exists so later
phases (starting with Persistent Build State) are judged against the metrics
table in [`README.md`](README.md), never against "fewer files" or intuition.

**Hard rule — frozen fixtures**: once a case's fixture is committed, it is
never edited. A required change is a new case id, not an in-place edit,
because every future regression run must replay byte-identical input.

## Case matrix

| id | route/profile | fixture | status |
|---|---|---|---|
| `text-teaching` | **Quick** ([`profiles/quick-generate.md`](../../workflows/profiles/quick-generate.md)); also re-run under **Default** for the P2 Context Compiler regression | `scripts/tests/fixtures/benchmark/text-teaching/lesson.md` | **run — baseline + P2 recorded** |
| `text-image-teaching` | Default | `scripts/tests/fixtures/benchmark/text-image-teaching/{lesson.md, cover.jpg}` | defined-not-run |
| `research-report` | Default | `scripts/tests/fixtures/benchmark/research-report/report.md` | defined-not-run |
| `chart-table-dense` | Default | `scripts/tests/fixtures/benchmark/chart-table-dense/quarterly.csv` | defined-not-run |
| `formula-dense` | Default | `scripts/tests/fixtures/benchmark/formula-dense/derivation.md` | defined-not-run |
| `template-based` | Default + Create Template | `scripts/tests/fixtures/benchmark/template-based/brief.md` + an installed brand/deck template id | defined-not-run |
| `beautify` | [`profiles/beautify-pptx.md`](../../workflows/profiles/beautify-pptx.md) | `scripts/tests/fixtures/benchmark/beautify/legacy_deck.pptx` | defined-not-run |
| `edit-native` | [`edit-native-pptx.md`](../../workflows/edit-native-pptx.md) | `scripts/tests/fixtures/benchmark/edit-native/native_deck.pptx` | defined-not-run |
| `image-to-pptx` | [`profiles/image-to-pptx.md`](../../workflows/profiles/image-to-pptx.md) | `scripts/tests/fixtures/benchmark/image-to-pptx/scanned_pages/*.png` | defined-not-run |
| `notes-animation` | Default, Notes + Custom Animations enabled | `scripts/tests/fixtures/benchmark/notes-animation/talk.md` | defined-not-run |
| `semantic-tools` | P3 Semantic Tool Layer acceptance ([`semantic_tools.md`](../../scripts/docs/semantic_tools.md)) | `scripts/tests/fixtures/benchmark/semantic-tools/page_spec.json` | **run — recorded below** |
| `hooks-p4` | P4 Lifecycle Hooks acceptance ([`lifecycle_hooks.md`](../../scripts/docs/lifecycle_hooks.md)); reuses the `text-teaching` P2 project and the `semantic-tools` P3 project rather than a new fixture | n/a (state-only regression, no new authored content) | **run — recorded below** |
| `workbench-p5` | P5 Unified Workbench acceptance ([`workbench.md`](../../scripts/docs/workbench.md)); reuses the same P2/P3 projects `hooks-p4` left behind | n/a (state/API-only regression) | **run — recorded below** |
| `ai-edit-p6` | P6 Live AI Edit Loop acceptance ([`ai_edit.md`](../../scripts/docs/ai_edit.md)); reuses the same P2/P3 projects, plus one fresh tempdir project for the plain-text case | n/a (job/patch-only regression, no new authored content) | **run — recorded below** |

A case's own fixture directory holds either the finished input file(s) (for
`text-teaching`) or, until it is actually run, a `README.md` placeholder
noting `defined-not-run` and pointing back to this file.

## `text-teaching` — the case actually run for P0

Chosen as the one case executed now because Quick has no blocking Confirm UI
interaction: it completes in one continuous session, so
`time_to_first_slide`, `time_to_usable_preview`, and `total_build_time` are
all measurable without an unbounded human confirmation wait in the middle.

- Fixture: [`../../scripts/tests/fixtures/benchmark/text-teaching/lesson.md`](../../scripts/tests/fixtures/benchmark/text-teaching/lesson.md) — synthetic but representative (a real, specific 45-minute intro-programming lesson brief), not a toy prompt and not real user material (avoids uncontrolled variables like file length, images, or privacy).
- Exact chat instruction to give the agent (record any deviation in the run's result JSON `notes` field so future re-runs stay comparable):

  > 用 Quick 模式，根据 lesson.md 生成一份 8–10 页、16:9 的 Python 列表与字典入门教学 PPT。

- Route: Quick generate, invoked through the normal `routing.md` selection (explicit "Quick" intent) — no special-cased execution path.

## `text-teaching` — second run for P2 (Context Compiler regression)

Context Compiler (`page-context --compile`) only applies to Default Generate PPTX (Quick never creates `design_spec.md`/`spec_lock.md`, which the compiler projects). Validating it therefore requires the same fixture through Default, not Quick. Confirm UI's human click is skipped (explicit delegation, [`confirm-surface.md`](../confirm-surface.md)) but the design decisions it would have produced are not — they are frozen, version-controlled input:

- Frozen Stage-1/Stage-2 confirmation decisions: [`../../scripts/tests/fixtures/benchmark/text-teaching/default_confirmation.json`](../../scripts/tests/fixtures/benchmark/text-teaching/default_confirmation.json) — same mode (`instructional`), visual style (`swiss-minimal`), palette, typography, canvas, and 9-page roster as the Quick run, so the two are the same design, not a different deck.
- Full chain actually run: Strategist output (design_spec.md/spec_lock.md authored from the frozen record) → `page-context --compile` before each page → real 9-page SVG authoring → early gate (after P05) → final gate → real `svg_to_pptx.py` export (no `--quick-generate`) → `build_state.py`/`resume` exercised at every stage.
- Per-page compiled-context stats: `<project>/analysis/context-compiler/P<NN>.compile.json` + a consolidated `_summary.json` (includes the honest legacy-vs-compiled character-count caveat — this fixture's `spec_lock.md` is small enough that the old "read the pair once + one 5-page lock re-read" model already reads fewer *total* characters than nine small per-page compiler calls; the compiler's validated property is a small, bounded, position-independent per-page cost, not an unconditionally smaller total on every deck size).
- Benchmark record: `references/benchmarking/results/text-teaching_20260911_162105.json` (`route: "default"`) — explicitly not wall-clock-comparable to the Quick baseline record (different pipeline entirely); see its `notes` field.
- A strict Default legacy-context vs. Default compiled-context A/B (same fixture, same frozen decisions, only the compiler toggled) is a valid future follow-up, not performed here.

## `semantic-tools` — P3 Semantic Tool Layer acceptance

A separate, deliberately small corpus entry (not a `text-teaching` re-run):
one page combining exactly 1 native shape + 1 formula + 1 chart + 1 table,
each produced by calling `scripts/semantic_tools.py` (`shape.create`,
`formula.create`, `chart.create`, `table.create`) instead of hand-authoring
the marker XML, then assembled into a real project, checked, and exported
for real — verifying the full chain (agent → semantic tool → existing
renderer/compiler → SVG → native PPTX) and, critically, that the exported
objects are genuinely native, not images standing in for tool convenience.

- Fixture: [`../../scripts/tests/fixtures/benchmark/semantic-tools/page_spec.json`](../../scripts/tests/fixtures/benchmark/semantic-tools/page_spec.json) — frozen like every other corpus entry.
- Project run: `projects/semantic-tools-p3-fixture_20260912/` — page assembled from the four tool-returned fragments as top-level `<g>` siblings under `<svg>`; real `svg_quality_checker.py` run reached 0 blocking errors (two payload-completeness gaps the checker's SVG-first parity check caught on the first table attempt — missing explicit per-cell fill/color and an internal-dividers-only border grid — are fixed in `scripts/tools/table.py`, not worked around in the fixture); real `svg_to_pptx.py --quick-generate --no-notes --native-charts-and-tables` export succeeded.
- Native-object verification (the actual point of this fixture, done by inspecting the exported `.pptx` directly with `python-pptx` and raw slide XML, not by trusting the tool's own claim): the shape is a real `RIGHT_ARROW` `AUTO_SHAPE`; `p-tools-hours-chart` has `shape.has_chart == True`; `p-tools-goals-table` has `shape.has_table == True`; the formula compiled to genuine OOXML Office Math — `ppt/slides/slide1.xml` contains both `<m:oMath` and the `a14:m` DrawingML-extension wrapper `native-formula.md` §2.2 documents as the real export target, not a flattened text approximation.

## `hooks-p4` — P4 Lifecycle Hooks acceptance

No new fixture: P4 is a state-machine/harness layer, not a content-authoring
one, so its real regression is running the CLI (`scripts/semantic_tools.py`,
`scripts/build_state.py`) against projects that already existed —
`text-teaching-p2-default_ppt169_20260912` (real, 9-page Default deck,
`build_state.json` written before P4 existed) and
`semantic-tools-p3-fixture_20260912` (real, 1-page, no `build_state.json`
until this run).

**Backward compatibility + adoption on a real pre-P4 project**
(`text-teaching-p2-default_ppt169_20260912`):
1. `build_state.py show` loads the file written before P4's schema fields
   existed without error; every new field (`hooks.mode`, per-slide
   `validated_revision`/`validation_status`/`svg_path`,
   `export.plan_revision`/`export.deck_revision`) defaults correctly.
2. `stop-check` on the untouched file returns `allow` (P1's `resume_report`
   already considered this deck finished; Stop's coarse check is
   unaffected by the new, separate per-slide validation-binding axis).
3. `set-hooks-mode ... enforce`, then `deck.export` → **blocked**,
   `DECK_NOT_EXPORTABLE` naming `stale_validation` for all 9 slides — an
   accurate, not false, positive: no `slide.validate` call has ever bound a
   `validated_revision` for this deck, since the mechanism didn't exist
   when it was built.
4. Real `slide.validate --stage final` over the whole deck (0 blocking
   errors, 2 pre-existing cosmetic `TEXT_OVERFLOW` warnings already known
   from the P2 run) binds every slide's `validated_revision`. Retried
   `deck.export` → **allowed**, runs for real, produces a genuine new
   `.pptx` + postflight report, and its PostToolUse hook auto-transitions
   `export.dirty -> false` / `project.phase -> "exported"` /
   `export.plan_revision`+`export.deck_revision` without a manual
   `set-export`/`set-phase` call. `stop-check` → `allow`.

**Fault injection #1 — stale validation blocks export**: real
`submit-slide P05` (bumping its revision, simulating a real edit) without
re-validating → real `deck.export` in `enforce` mode → blocked,
`DECK_NOT_EXPORTABLE` / `reasons: [{"type": "stale_validation", "slide":
"P05", ...}]` naming exactly that slide. Real `slide.validate` on
`05_dict_ops.svg` → retried `deck.export` → succeeds.

**Fault injection #2 — stale slide blocks Stop, with the give-up cap**: real
`submit-slide P07 --status stale` → `stop-check --max-blocks 2` → blocked
`BUILD_INCOMPLETE` naming `P07 is stale` (exit `5`), `attempts: 1`; called
again → still blocked, `attempts: 2`; called a third time → gives up:
`{"status": "failed", "reason": "QUALITY_GATE_UNRESOLVED"}` (exit `6`),
does not block a fourth time. Fixed (`submit-slide P07 --status ready` +
real `slide.validate` + real `deck.export` to clear `export.dirty`) →
`stop-check` → `allow`, `hooks.stop_block_count` back to `0`.
`validation/workflow.log` under this project shows the real `HOOK_PRE_TOOL`
/ `HOOK_STOP` audit entries for every one of the above non-`allow` decisions
— confirmed by direct inspection, not assumed.

**Shadow mode, real** (`semantic-tools-p3-fixture_20260912`, freshly
`build_state.py init`'d for this run — `hooks.mode` defaults to
`"shadow"`): `submit-slide P01` without validating, then real `deck.export
--native-charts-and-tables` → **runs anyway** (`ok: true`, a real new
`.pptx`), with `"DECK_NOT_EXPORTABLE: [shadow] would block: ..."` folded
into the response's own `warnings` array — confirming a project can adopt
hooks, observe what they would have blocked, and only then flip to
`"enforce"`, exactly as the rollout in `lifecycle_hooks.md` prescribes.

## `workbench-p5` — P5 Unified Workbench acceptance

No new fixture: the Workbench is a UI/API adapter, not a content-authoring
phase, so its real regression is the actual Flask apps
(`svg_editor.server.create_app()`, `workbench.runtime_api`/`confirm_api`)
exercised through their test clients against the same two real projects
`hooks-p4` left on disk — `text-teaching-p2-default_ppt169_20260912`
(9-page Default, `hooks.mode: "enforce"`, every slide already validated)
and `semantic-tools-p3-fixture_20260912` (1-page, `hooks.mode: "shadow"`).

- **Case A (load a pre-P4 project cleanly)**: `/api/runtime/project` and
  `/api/runtime/slides` against the real P2 project return the correct
  route/phase/9-slide roster with zero schema errors — no separate
  migration step, matching the same backward-compatibility property
  confirmed at the CLI layer in `hooks-p4`.
- **Case C/G (stale-validation block → validate → export, and the same
  enforce-mode blocking behavior) via the HTTP API**: real `submit-slide`
  bump on the already-validated P05 → `/api/runtime/slides` shows "Needs
  validation" → `POST /api/runtime/deck/export` returns
  `DECK_NOT_EXPORTABLE` naming P05's `stale_validation` reason → `POST
  /api/runtime/slides/P05/validate` → retried export succeeds. Identical
  outcome to `hooks-p4`'s CLI-driven version of the same scenario, this
  time proving the Workbench's routes never reimplement the rule — they
  call the exact same `tools.dispatch.invoke()` the CLI calls.
- **Case D (direct-edit revision wiring — the one new runtime-logic path
  P5 adds)**: a real `POST /api/slide/01_cover.svg/edit` +
  `POST /api/save-all` against a live `svg_editor.server.create_app()`
  instance (fresh tempdir project) is followed by `build_state.json`
  showing the slide's revision bumped, `status: "dirty"`, and its
  `validated_revision` now stale by comparison — confirming §19/§20's
  exact requirement: a direct edit through the real editor is no longer
  invisible to the Runtime.
- **Case F (shadow mode) via the API**: real `POST /api/runtime/deck/export`
  against the shadow-mode P3 project runs to completion (`ok: true`, a real
  new `.pptx`) with a `"... [shadow] would block: ..."` entry in the
  response's own `warnings` array — the Workbench never has to know what a
  "hook" is to display this correctly; it is just another warning string.
- **Confirm Panel, real round-trip (not simulated)**: a minimal
  `free_design` Stage-1 fixture (`template_options.json` +
  `recommendations.stage1.json`) proxied through
  `workbench.confirm_api`'s `/api/confirm/state` and `/api/confirm/submit`
  reaches `confirm_ui.server`'s real, unmodified validation pipeline
  in-process (no subprocess, no port, no `--wait-only`) — an incomplete
  submission is rejected with confirm_ui's own real error message
  (`"Stage 1 payload must include template_selection with mode and
  selection_keys"`, discovered empirically against the real code, not
  guessed), and a valid one writes genuine `result.json` /
  `template_selection.json` files identical in shape to what the
  standalone `confirm_ui/server.py` would have produced.

Cases B (a partial in-progress build's status mix) and E (Resume's
read-by-default / explicit-apply reconciliation) are exercised as real
(tempdir-project) API integration tests in
`scripts/tests/test_workbench_runtime_api.py` rather than repeated here.

## `ai-edit-p6` — P6 Live AI Edit Loop acceptance

No new fixture: reuses `text-teaching-p2-default_ppt169_20260912` and
`semantic-tools-p3-fixture_20260912` again, plus one fresh tempdir project
for the plain-text case. For every case below that calls for "the AI
produces an EditPlan," the author of this test file genuinely reasoned
about the instruction the way the host-native `AIEditRunner` design
intends — there is no mocked model anywhere in this phase's regression.

- **Case A (single text edit, real export)**: a real `create` job against
  a fresh project's over-long title, a real compiled context read back
  confirming the exact current text, a real reasoned EditPlan
  (`set_text`) submitted and applied, revision bumped, and a real
  `deck.export` afterward succeeds.
- **Case D (native chart, the most important P6 case)**: a real
  `semantic_tool` (`chart.create`) edit on the real
  `p-tools-hours-chart` object, followed by a real `deck.export` with
  `--native-charts-and-tables`, followed by opening the resulting `.pptx`
  as a zip and confirming a genuine `ppt/charts/chart*.xml` part exists —
  the chart was never flattened to an image. **Real finding along the
  way**: `chart.create`'s `type: "line"` fallback rendering has a
  pre-existing fidelity gap against the strict native-charts-and-tables
  checker (missing point-marker/legend projection) that `chart.create`'s
  own payload schema has no parameter to satisfy — out of scope for P6 to
  fix; the regression instead exercises a `column`-type data update,
  which exercises the exact same `semantic_tool` round-trip and Native
  Object Integrity Gate this case is actually about.
- **Case E (formula, integrity gate + real recovery)**: a direct `resize`
  op on the real `p-tools-average` formula object is confirmed rejected
  with `NATIVE_OBJECT_INTEGRITY_ERROR` and zero writes, then `retry`
  spawns a fresh job and a `semantic_tool` (`formula.create`) edit is
  applied cleanly, confirmed still carrying `data-pptx-replace-with="formula"`.
- **Case P (legacy project)**: the real pre-P6
  `text-teaching-p2-default_ppt169_20260912` project has no
  `ai_edit_active_job`/`ai_edit_last_job` fields on any slide and still
  loads cleanly; `list_jobs` against it returns an empty list rather than
  erroring on a project that has never had a `runtime/ai_edits/` directory.

Cases B/C/F/G/H/I/J/K/L/M/N/O are exercised as real (tempdir-project, no
mocked business logic) tests across `scripts/tests/test_ai_edit_jobs.py`,
`scripts/tests/test_patch_engine.py`, and
`scripts/tests/test_workbench_ai_edit_api.py` — including a genuine
revision race (a real `submit_slide` call interleaved between a job's
context-read and its plan-submit), a genuine double-conflict stop, a
genuine deleted-selection detection, and a genuine undo-then-conflict
check — rather than repeated here.

**Shared-real-project regression hygiene**: running this phase's
regression against `semantic-tools-p3-fixture_20260912` exposed a latent
fragility in `workbench-p5`'s own shadow-mode test (it assumed an
"ambient" stale-validation condition left over from whichever test ran
last, rather than creating one itself) — fixed to explicitly create and
tear down its own staleness condition, since a shared real project's state
is not private to any one test file.
