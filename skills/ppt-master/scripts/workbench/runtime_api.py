#!/usr/bin/env python3
"""
PPT Master - Workbench Runtime Status API (P5)

Thin Flask route registration over already-existing, already-tested P1-P4
functions -- no rule lives here. Every mutating route calls the exact same
function the CLI calls (tools.dispatch.invoke, runtime.hook_rules.stop/resume)
so shadow/enforce and the three-attempt Stop policy behave identically to
the CLI by construction, not by careful duplication.

register(app, project_path) is called once from svg_editor/server.py's
create_app() -- this module never launches its own Flask app or claims its
own lock/port.

Usage:
    from workbench.runtime_api import register
    register(app, project_path)

Dependencies:
    flask (already a declared dependency of svg_editor/server.py)
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, request

from runtime.build_state import load
from runtime.hook_rules.resume import reconcile
from runtime.hook_rules.stop import stop_check
from runtime.resume import resume_report
from tools.dispatch import invoke
from workbench import view_model
from workbench.plan_reader import plan_view


def _resolve_page(project_path: Path, state, slide_id: str) -> str | None:
    """svg_path recorded on the slide, or a best-effort glob fallback for a
    slide that was authored before P5 (never recorded via --from-file)."""
    slide = state.slides.get(slide_id) if state else None
    if slide is not None and slide.svg_path:
        return Path(slide.svg_path).name
    digits = slide_id[1:]
    matches = sorted((project_path / "svg_output").glob(f"{digits}_*.svg"))
    if not matches:
        matches = sorted((project_path / "svg_output").glob(f"0{digits}_*.svg"))
    return matches[0].name if matches else None


def register(app: Flask, project_path: Path) -> None:
    project_path = Path(project_path)

    @app.route("/api/runtime/project")
    def runtime_project():  # type: ignore[unused-variable]
        state = load(project_path)
        if state is None:
            return jsonify({"ok": True, "mode": "legacy"})
        return jsonify({"ok": True, "mode": "build-state", **view_model.project_view(state)})

    @app.route("/api/runtime/build-state")
    def runtime_build_state():  # type: ignore[unused-variable]
        state = load(project_path)
        if state is None:
            return jsonify({"ok": True, "mode": "legacy"})
        return jsonify({
            "ok": True,
            "mode": "build-state",
            "state": state.to_json(),
            "progress": view_model.build_progress(state),
            "export": view_model.export_status(state),
        })

    @app.route("/api/runtime/slides")
    def runtime_slides():  # type: ignore[unused-variable]
        state = load(project_path)
        if state is None:
            return jsonify({"ok": True, "mode": "legacy", "slides": []})
        return jsonify({"ok": True, "mode": "build-state", "slides": view_model.slides_view(state)})

    @app.route("/api/runtime/slides/<slide_id>")
    def runtime_slide_detail(slide_id: str):  # type: ignore[unused-variable]
        state = load(project_path)
        if state is None or slide_id not in state.slides:
            return jsonify({"ok": False, "errors": [{"code": "SLIDE_NOT_FOUND", "message": slide_id}]})
        return jsonify({"ok": True, "slide": view_model.slide_view(slide_id, state.slides[slide_id])})

    @app.route("/api/runtime/slides/<slide_id>/validate", methods=["POST"])
    def runtime_validate_slide(slide_id: str):  # type: ignore[unused-variable]
        state = load(project_path)
        payload: dict[str, object] = {"project": str(project_path)}
        page = _resolve_page(project_path, state, slide_id)
        if page:
            payload["page"] = page
        envelope, _ = invoke("slide.validate", payload)
        return jsonify(envelope)

    @app.route("/api/runtime/deck/validate", methods=["POST"])
    def runtime_validate_deck():  # type: ignore[unused-variable]
        body = request.get_json(silent=True) or {}
        payload = {"project": str(project_path), "stage": body.get("stage", "final")}
        if body.get("quick_generate"):
            payload["quick_generate"] = True
        envelope, _ = invoke("slide.validate", payload)
        return jsonify(envelope)

    @app.route("/api/runtime/deck/export", methods=["POST"])
    def runtime_export_deck():  # type: ignore[unused-variable]
        body = request.get_json(silent=True) or {}
        payload = {"project": str(project_path), **body}
        envelope, _ = invoke("deck.export", payload)
        return jsonify(envelope)

    @app.route("/api/runtime/resume")
    def runtime_resume():  # type: ignore[unused-variable]
        report = resume_report(project_path)
        report["reconciliation"] = reconcile(project_path, apply=False)
        return jsonify({"ok": True, **report})

    @app.route("/api/runtime/resume/apply", methods=["POST"])
    def runtime_resume_apply():  # type: ignore[unused-variable]
        findings = reconcile(project_path, apply=True)
        return jsonify({"ok": True, "reconciliation": findings})

    @app.route("/api/runtime/stop-check")
    def runtime_stop_check():  # type: ignore[unused-variable]
        result = stop_check(project_path)
        return jsonify({"ok": True, **result})

    @app.route("/api/runtime/active-slide", methods=["POST"])
    def runtime_active_slide():  # type: ignore[unused-variable]
        body = request.get_json(silent=True) or {}
        app.config["WORKBENCH_ACTIVE_SLIDE"] = body.get("slide_id")
        return jsonify({"ok": True, "active_slide": app.config["WORKBENCH_ACTIVE_SLIDE"]})

    @app.route("/api/runtime/plan")
    def runtime_plan():  # type: ignore[unused-variable]
        state = load(project_path)
        return jsonify({"ok": True, "plan": plan_view(project_path, state)})
