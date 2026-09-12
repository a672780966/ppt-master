# Semantic Tool Layer (P3)

Structured-input/structured-output tools over existing PPT Master
implementation. The agent gives data — a preset name, LaTeX, chart/table
data, a project path — never a script name, a marker attribute format, or
`svg_quality_checker.py`'s report shape.

```bash
python3 scripts/semantic_tools.py shape.create --input '<json>'
python3 scripts/semantic_tools.py formula.create --input-file payload.json
python3 scripts/semantic_tools.py chart.create --input '<json>'
python3 scripts/semantic_tools.py table.create --input '<json>'
python3 scripts/semantic_tools.py slide.validate --input '<json>'
python3 scripts/semantic_tools.py deck.export --input '<json>'
```

**Never inline LaTeX (or any backslash-heavy payload) into `--input` on a
command line**: shell layers can silently reinterpret backslash sequences
before this process ever sees them (`\frac{-b}{2a}` has arrived as a
literal form-feed control character purely from shell requoting, with the
JSON payload otherwise correct). Write the payload to a file and use
`--input-file`, or pipe it over stdin — both bypass shell argv re-quoting
entirely.

## Contract

Every tool returns one JSON envelope on stdout:

```json
{"ok": true, "...tool-specific fields...", "warnings": ["..."]}
{"ok": false, "errors": [{"code": "...", "message": "...", "..."}], "warnings": []}
```

Exit codes: `0` (`ok: true`; for `slide.validate` this also means the slide
passed), `1` (`ok: false` — a recognized problem, see `errors`), `2`
(malformed CLI usage or malformed `--input`/`--input-file` JSON).

**Hard rule — never writes `build_state.json`**: a semantic tool performs
its one action and returns a structured result. Whatever that means for
revision/dirty/status/job state is `scripts/build_state.py`'s decision, made
by the caller after reading the result — never inside the tool. See
[`../../references/artifact-ownership.md`](../../references/artifact-ownership.md).

**Hard rule — never a page-file generator**: `shape.create` /
`formula.create` / `chart.create` / `table.create` return a self-contained
SVG fragment string. None of them write into `svg_output/<page>.svg` — the
main agent still hand-inserts every fragment into the page it is authoring
([`../../references/executor-base.md`](../../references/executor-base.md)
"Main-agent ownership").

## `shape.create`

Wraps `pptx_to_svg.preset_authoring.render_preset_shape_fragment` (the same
function `preset_shape_svg.py render` calls). Input: `id`, `preset`,
`frame` (`{x,y,width,height}`), `fill`, `stroke`, `stroke_width`,
`fill_opacity`, `adjustments`, `object_kind` (`shape` default, `connector`
for connector presets), `filter_id`. Output: `artifact_id`, `native_type:
"shape"`, `svg_fragment`. Errors: `UNKNOWN_PRESET`, `INVALID_ID`,
`INVALID_FRAME`, `INVALID_OBJECT_KIND`, `INVALID_SHAPE_INPUT`.

## `formula.create`

Wraps `svg_to_pptx.native_objects.formula_compiler`'s LaTeX -> OMML compiler
to validate the LaTeX is within the supported Microsoft 365 profile, plus a
best-effort Unicode linearizer for the required SVG preview (`\frac`,
`\sqrt`, `^{}`/`_{}`, `\pm` and a handful of symbols; an unrecognized
command falls through to a stripped best-effort join and the result carries
a `preview_approximate` warning). Input: `latex`, `display` (`"block"`
default or `"inline"`), and for `display: "block"`: `id`, `frame`,
`font_size`, `color`, `align`. Output for `block`: a complete
`<g data-pptx-replace-with="formula">` fragment matching
[`../../references/native-formula.md`](../../references/native-formula.md)
§2.2. Output for `inline`: a bare `<tspan data-pptx-inline-formula="...">`
fragment for the agent to place inside its own `<text>` element (per §2.1,
inline markers carry no `id`/frame of their own). Errors:
`UNSUPPORTED_LATEX`, `INVALID_ID`, `INVALID_FRAME`, `INVALID_STYLE`,
`INVALID_DISPLAY`.

## `chart.create`

There is no existing "render a chart's visible SVG from data" script in PPT
Master — this is new, deliberately small geometry code for the five most
common types, not a wrapper. `type`: `column`, `bar`, `line`, `pie`,
`donut`; any other value is `UNSUPPORTED_CHART_TYPE` (the error's
`supported_types` field lists the current five), never a silent downgrade.
Input: `id`, `type`, `frame`, `categories`, `series` (`pie`/`donut` require
exactly one), `style.colors` / `style.axis_color`, `title`, `show_legend`,
`axes.minimum`/`axes.maximum`. Output: `artifact_id`, `native_type:
"chart"`, `chart_type`, `svg_fragment` — a complete, self-contained
`<g data-pptx-replace-with="chart">` per
[`../../references/native-data-interface.md`](../../references/native-data-interface.md)
§2, with `data-pptx-fallback-sha256` already stamped (see "Fingerprint
timing" below). Errors: `UNSUPPORTED_CHART_TYPE`, `INVALID_ID`,
`INVALID_FRAME`, `INVALID_DATA`, `INVALID_STYLE`.

## `table.create`

Same situation as `chart.create`: `scripts/semantic_table.py` only
compacts/expands the `ppt-master.semantic-table.v2` payload, it has no grid
renderer. Input: `id`, `frame`, `columns` (optional header row), `rows`,
`style` (`header_fill`, `header_text`, `body_fill`, `band_fill`,
`body_text`, `border_color`, `font_size`, `header_font_size`, `padding`).
Output: `artifact_id`, `native_type: "table"`, `svg_fragment` — a complete
`<g data-pptx-replace-with="table">`, self-stamped. The JSON payload states
every cell's `fill`/`color`/`bold` explicitly (not just deck-level
`style.band_fill`) and a full four-sided grid including the outer perimeter
— both were required to pass the checker's SVG-first parity check that
`--native-charts-and-tables` gates on; an implicit-banding or
internal-dividers-only first attempt failed it (see the P3 acceptance
fixture's git history/benchmark notes for the concrete findings). Errors:
`INVALID_ID`, `INVALID_FRAME`, `INVALID_DATA`, `INVALID_STYLE`.

### Fingerprint timing (why chart/table need no separate `stamp_native_fallbacks.py` run)

`svg_native_fallback_fingerprint` (`pptx_shapes/semantic_hash.py`) hashes a
marker's own visible subtree plus only the *reachable document-level
`<defs>` dependencies* it references by `url(#id)` — confirmed by reading
its implementation directly. `chart.create`/`table.create` never reference
an external `<defs>`, so the fragment is fully self-contained and the tool
calls `stamp_native_fallback_baseline` on it before returning — the agent
inserts the returned fragment verbatim and the stamp is already correct;
`stamp_native_fallbacks.py --write` remains available for hand-authored
markers or a marker edited after insertion.

## `slide.validate`

Wraps `svg_quality_checker.py` as a subprocess (its report has no stable
error codes — confirmed by reading `checker.py`: `categories.*.issues`
entries are plain `{"file", "message"}` free-form-English pairs). This tool
adds the classification layer: a short, stable `code` (`TEXT_OVERFLOW`,
`BOUNDS_OVERFLOW`, `ROOT_GROUP_OVERLAP`, `SPLIT_PARAGRAPH`,
`NONCANONICAL_STYLE`, `XML_MALFORMED`, `VIEWBOX_ISSUE`, `FOREIGN_OBJECT`,
`FONT_ISSUE`, `PAINT_ISSUE`, `STALE_NATIVE_FALLBACK`, or `UNCLASSIFIED` with
the original message preserved) plus `severity` and `suggested_action` per
issue, built from the real message patterns the checker is known to emit —
never a fabricated code for a message it doesn't recognize. Input:
`project`, `page` (optional basename under `svg_output/`; omit to validate
the whole deck), `stage` (`"page"` default when `page` given, else
`"final"`), `quick_generate`. Output: `ok` (true means no blocking errors —
this *is* the field a caller checks, not a separate tool-execution flag),
`errors` (classified, blocking only), `report_path` (the full checker
report stays on disk exactly as `svg_quality_checker.py` writes it — this
tool never deletes or replaces it, only reads and summarizes it; open it
directly for deep debugging). Errors (tool-execution failures, not slide
issues): `INVALID_PROJECT`, `INVALID_STAGE`, `CHECKER_FAILED`.

This is an additional, opt-in convenience path alongside the existing
mandatory gate commands in
[`../../workflows/generate-pptx.md`](../../workflows/generate-pptx.md) Step
6 and
[`../../workflows/profiles/quick-generate.md`](../../workflows/profiles/quick-generate.md)
§4 — it does not replace them; P3 builds the execution interface only, a
pre-export *gate* built on top of it is P4 Lifecycle Hooks' job.

## `deck.export`

Wraps `svg_to_pptx.py` as a subprocess: `project` / `output` /
`expected_state` in, PPTX path + postflight summary + warning summary out.
Input: `project`, `output` (optional explicit path, `-o`), `quick_generate`,
`no_notes`/`with_notes`, `native_charts_and_tables`, `expected_state`
(optional `{"slide_count": N}` — checked against the actual export and
reported as a **warning** on mismatch, never a hard failure; a strict
pre-export gate is P4's job, not this tool's). Output: `ok` (mirrors the
postflight `status`), `pptx_path`, `status`, `quality_gate`, `slide_count`,
`output_bytes`, `report_path`. Errors: `INVALID_PROJECT`, `EXPORT_FAILED`.
Same relationship to the existing mandatory Step 7 export commands as
`slide.validate` above — additional, not a replacement.

## Acceptance fixture

`scripts/tests/fixtures/benchmark/semantic-tools/page_spec.json` — one page
combining exactly 1 native shape + 1 formula + 1 chart + 1 table, each
produced through this CLI rather than hand-authored, verified end to end
(real checker pass, real `svg_to_pptx.py --native-charts-and-tables`
export, then the resulting `.pptx` inspected directly to confirm
`shape.has_chart` / `shape.has_table` / `auto_shape_type == RIGHT_ARROW`
and a real `<m:oMath>` element in the slide XML — not a rasterized image
standing in for any of the four).
