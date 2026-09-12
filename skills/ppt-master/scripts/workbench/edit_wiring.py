#!/usr/bin/env python3
"""
PPT Master - Direct-Edit Revision Wiring (P5)

The one genuinely new piece of runtime logic P5 adds: svg_editor/server.py's
save_all() is the sole place a direct SVG edit ever reaches disk
(references/artifact-ownership.md), and until now it had zero interaction
with build_state.json -- a user could drag/resize/edit a property and the
Runtime would never know the slide changed. record_direct_edit() closes
that loop the same way every other P1-P4 mutation does: through
runtime.revisions.submit_slide's expected_revision CAS, never a second
writer path.

No build_state.json -> no-op (legacy project; matches every other P1-P4
fallback -- P5 never makes build_state adoption mandatory). Unmappable
filename -> no-op (never invents a slide id). A StaleEditError here means
another writer (the Agent, a hook) bumped the revision between the read and
this call -- safe to retry with a fresh read, since this mutation is always
"bump from wherever it currently is," never a content merge; true
conflict UX is P6's job (P5 SS23).

Usage:
    from workbench.edit_wiring import record_direct_edit
    record_direct_edit(project_path, svg_file)  # after a successful tree.write()

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

from pathlib import Path

from runtime.build_state import SlideState, StaleEditError, load
from runtime.revisions import submit_slide
from runtime.slide_id import slide_id_from_page

_MAX_RETRIES = 3


def record_direct_edit(
    project_path: Path,
    svg_file: Path,
    *,
    status: str = "dirty",
) -> SlideState | None:
    """Bump the edited slide's revision under CAS. Returns None on a no-op
    (no build_state.json, or an unmappable filename); raises StaleEditError
    only if every retry loses the race (should not happen under normal
    single-editor-session load).
    """
    project_path = Path(project_path)
    if load(project_path) is None:
        return None

    slide_id = slide_id_from_page(Path(svg_file).name)
    if slide_id is None:
        return None

    last_error: StaleEditError | None = None
    for _ in range(_MAX_RETRIES):
        state = load(project_path)
        if state is None:
            return None
        slide = state.slides.get(slide_id)
        expected_revision = slide.revision if slide is not None else 0
        try:
            return submit_slide(
                project_path, slide_id,
                expected_revision=expected_revision,
                status=status, from_file=svg_file,
            )
        except StaleEditError as exc:
            last_error = exc
            continue
    assert last_error is not None
    raise last_error
