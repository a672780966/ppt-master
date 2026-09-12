#!/usr/bin/env python3
"""
PPT Master - Resume Hook: State/Disk Reconciliation (P4)

Answers a narrower question than the full quality pipeline: not "is this
deck good," but "does build_state.json still match what is actually on
disk." Runs after a context loss, alongside the existing resume_report()
(P1) rather than replacing it -- this is reconciliation, not re-validation.

Read-only by default (reconcile() never mutates); an explicit apply=True is
required to flip a drifted slide to "dirty" -- an ARTIFACT_MISSING finding is
never auto-healed either way, since a missing file needs a human/agent
decision, not a silent status flip.

Usage:
    Imported by scripts/build_state.py (the `resume` subcommand).

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

import json
from pathlib import Path

from runtime.build_state import load, locked_state
from runtime.revisions import sha256_of_file
from workflow_log import append_note


def _log(project_path: Path, findings: list[dict[str, object]], *, applied: bool) -> None:
    try:
        append_note(
            project_path,
            f"HOOK_RESUME applied={applied}\n" + json.dumps(findings, ensure_ascii=False),
        )
    except OSError:
        pass  # advisory only -- artifact-ownership.md: recording failure never blocks


def reconcile(project_path: Path, *, apply: bool = False) -> list[dict[str, object]]:
    project_path = Path(project_path)
    state = load(project_path)
    if state is None:
        return []

    findings: list[dict[str, object]] = []
    drifted_slides: list[str] = []

    for slide_id, slide in sorted(state.slides.items()):
        if not slide.svg_path:
            continue
        file_path = project_path / slide.svg_path
        if not file_path.is_file():
            findings.append({
                "code": "ARTIFACT_MISSING", "slide": slide_id, "path": slide.svg_path,
            })
            continue
        if slide.content_hash and sha256_of_file(file_path) != slide.content_hash:
            findings.append({
                "code": "EXTERNAL_ARTIFACT_CHANGE", "slide": slide_id, "path": slide.svg_path,
            })
            drifted_slides.append(slide_id)

    if state.route != "quick":
        for name in ("design_spec.md", "spec_lock.md"):
            if not (project_path / name).is_file():
                findings.append({"code": "ARTIFACT_MISSING", "path": name})

    if state.phase == "exported" and state.last_export and not (project_path / state.last_export).is_file():
        findings.append({"code": "ARTIFACT_MISSING", "path": state.last_export})

    if apply and drifted_slides:
        with locked_state(project_path, create_if_missing=False) as locked:
            for slide_id in drifted_slides:
                slide = locked.slides.get(slide_id)
                if slide is not None:
                    slide.status = "dirty"
                    slide.dirty = True
            locked.export_dirty = True

    if findings:
        _log(project_path, findings, applied=apply and bool(drifted_slides))
    return findings
