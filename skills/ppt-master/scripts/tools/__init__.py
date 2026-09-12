"""PPT Master - Semantic Tool Layer (P3 Persistent Build Runtime)

Structured-input/structured-output wrappers over existing PPT Master
implementation (preset shape rendering, LaTeX->OMML compilation, the SVG
quality checker, the SVG->PPTX exporter). The agent gives data — a preset
name, LaTeX, chart/table data, a project path — never a script name, a
marker attribute format, or a checker report shape.

Hard rule (own only your action): a semantic tool never writes
build_state.json. It performs its one action and returns a structured
result; the caller (or a future P4 hook) decides what that means for
revision/dirty/status/job state via scripts/build_state.py. See
references/artifact-ownership.md.

Hard rule (never a page-file generator): shape.create / formula.create /
chart.create / table.create return a self-contained SVG fragment string.
None of them write into svg_output/<page>.svg — the main agent still
hand-inserts every fragment into the page it is authoring
(references/executor-base.md "Main-agent ownership").
"""
