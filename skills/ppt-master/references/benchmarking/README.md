# P0 Benchmark Procedure

This is a **documented manual/agent-run procedure, not push-button
automation**. There is no script here that drives PPTX generation itself —
a human or agent runs the real PPT Master skill exactly as an end user
would, through whichever real route [`corpus.md`](corpus.md) names for that
case. Two log markers plus a post-hoc analyzer (`scripts/benchmark_report.py`)
are the only wrapping around that real run.

This exists so later runtime changes (Persistent Build State, and whatever
follows it) are judged against real metrics recorded here, not against
"fewer files" or a subjective sense of improvement.

## Running one case

1. `python3 ${SKILL_DIR}/scripts/project_manager.py init <case_id>_<fmt>_<date> [--quick-generate]`
   (skip explicit `init` for Beautify / Edit Native PPTX, which enter through
   their own routes against an existing PPTX per
   [`routing.md`](../../workflows/routing.md)).
2. Copy the case's fixture input(s) from its row in [`corpus.md`](corpus.md)
   into the new project (or reference them directly, per the case's route).
3. Mark the start of the timed session:
   ```bash
   python3 ${SKILL_DIR}/scripts/workflow_log.py <project_path> "benchmark-start"
   ```
4. Run the case through its real route/profile, unmodified, to a finished
   PPTX or a documented failure. This is the thing being measured — it must
   not be special-cased or short-circuited.
5. Mark the end of the timed session:
   ```bash
   python3 ${SKILL_DIR}/scripts/workflow_log.py <project_path> "benchmark-end"
   ```
6. Run the analyzer, supplying every field it cannot derive from files on
   disk:
   ```bash
   python3 ${SKILL_DIR}/scripts/benchmark_report.py <project_path> --case-id <case_id> --route <route> \
       --set input_tokens=<N> --set output_tokens=<N> \
       --set manual_correction_count=<N> --set visual_score=<1-5> \
       --set resume_success=N/A
   ```
7. Commit the resulting JSON under
   [`results/`](results/).

## What is measured, and how

Every field in [`benchmark_run.schema.json`](../../templates/schemas/benchmark_run.schema.json)
is tagged `x-source: auto` or `x-source: manual` in the schema itself. This
table restates why each is one or the other:

| Field | Source | Why |
|---|---|---|
| `wall_clock_seconds`, `total_build_seconds` | auto | end-marker timestamp minus start-marker timestamp, read from `validation/workflow.log`'s `NOTE` records |
| `tool_call_count` | auto (approximate lower bound) | count of `PYTHON run=<id>` envelopes between the two markers in `validation/workflow.log`; this only sees Python-script tool calls the transcript tees ([`workflow_transcript.py`](../../scripts/workflow_transcript.py)), not every chat/Read/Edit tool call, so it undercounts total agent tool use |
| `time_to_first_slide_seconds` | auto | `mtime` of the first SVG in `svg_output/` (sorted lexicographically — real authoring uses `<index>_<page_name>.svg`, e.g. `01_cover.svg`, not a literal `P01.svg`) minus start-marker timestamp |
| `time_to_usable_preview_seconds` | auto | `mtime(live_preview/lock.json)` minus start-marker timestamp |
| `checker_error_count`, `checker_warning_count` | auto | `validation/svg_quality_report.json`'s `categories.blocking` / `categories.introduced` |
| `rework_count` | auto | extra `svg_quality_checker.py --stage early|final` invocations beyond the first pass of each stage, from `validation/workflow.log` |
| `pptx_postflight_result` | auto | `status` field in `validation/<output_stem>.report.json` |
| `input_tokens`, `output_tokens` | manual | no per-session token/usage data lands in any project file; read from the host's own usage reporting and supply with `--set` |
| `manual_correction_count` | manual | requires a human judgment call on what counted as a hand fix |
| `visual_score` (1–5) | manual | a quick subjective read of the exported deck; no rubric-scoring tool exists for a single numeric score (the existing [`visual-review.md`](../../workflows/stages/visual-review.md) rubric is `ok/fixed/needs_human/render_failed` per element, not a single deck-level number) |
| `resume_success` | manual | always `N/A` for every P0 record — Persistent Build State (P1) does not exist yet at baseline time; this field exists now purely so the schema does not need to change again once P1 lands |

`validation/workflow.log` stays exactly what it has always been — a cold,
append-only audit log, read-only here. Nothing in this procedure or in
`benchmark_report.py` writes runtime state into it, and nothing treats it as
anything but a timestamp/argv source.

## Extending the corpus

The 9 other cases in [`corpus.md`](corpus.md) are defined (id, route,
fixture path) but not yet run. Running one: author its fixture file(s) under
`scripts/tests/fixtures/benchmark/<case_id>/` (replacing that case's
placeholder `README.md`), update its `status` in `corpus.md` to
`run — baseline recorded`, then follow the same 7 steps above.
