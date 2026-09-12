#!/usr/bin/env python3
"""
PPT Master - Workbench AI Edit API (P6)

Thin Flask route registration over runtime.ai_edit_jobs -- no rule lives
here, exactly like workbench/runtime_api.py and confirm_api.py before it.
Every route is a parse-request -> call runtime.ai_edit_jobs -> jsonify
wrapper; scope/revision/native-integrity/conflict rules all live in
runtime/{ai_edit_jobs,edit_plan,patch_engine,edit_conflicts}.py.

register(app, project_path) is called once from svg_editor/server.py's
create_app(), same as the other workbench route modules.

Usage:
    from workbench.ai_edit_api import register
    register(app, project_path)

Dependencies:
    flask (already a declared dependency of svg_editor/server.py)
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, request

from runtime import ai_edit_jobs as jobs
from runtime.build_state import BuildStateError


def _error_envelope(code: str, message: str, **details: object) -> dict[str, object]:
    error = {"code": code, "message": message}
    error.update(details)
    return {"ok": False, "errors": [error]}


def _job_envelope(job: jobs.AIEditJob) -> dict[str, object]:
    return {"ok": True, "job": job.to_dict()}


def register(app: Flask, project_path: Path) -> None:
    project_path = Path(project_path)

    @app.route("/api/runtime/ai-edits", methods=["GET"])
    def list_ai_edits():  # type: ignore[unused-variable]
        slide = request.args.get("slide")
        status = request.args.get("status")
        job_list = jobs.list_jobs(project_path, slide_id=slide, status=status)
        return jsonify({"ok": True, "jobs": [j.to_dict() for j in job_list]})

    @app.route("/api/runtime/ai-edits", methods=["POST"])
    def create_ai_edit():  # type: ignore[unused-variable]
        body = request.get_json(silent=True) or {}
        try:
            job = jobs.create_job(
                project_path,
                slide_id=body.get("slide_id"),
                scope=body.get("scope", "selection"),
                selection_ids=body.get("selection_ids") or [],
                instruction=body.get("instruction", ""),
                origin=body.get("origin", "inline"),
                annotation_id=body.get("annotation_id"),
            )
        except (jobs.AIEditJobError, BuildStateError) as exc:
            code = getattr(exc, "code", "TARGET_NOT_FOUND")
            return jsonify(_error_envelope(code, str(exc), **getattr(exc, "details", {})))
        return jsonify(_job_envelope(job))

    @app.route("/api/runtime/ai-edits/<job_id>", methods=["GET"])
    def get_ai_edit(job_id: str):  # type: ignore[unused-variable]
        try:
            job = jobs.load_job(project_path, job_id)
        except jobs.AIEditJobError as exc:
            return jsonify(_error_envelope(exc.code, str(exc)))
        return jsonify(_job_envelope(job))

    @app.route("/api/runtime/ai-edits/<job_id>/context", methods=["GET"])
    def get_ai_edit_context(job_id: str):  # type: ignore[unused-variable]
        try:
            context = jobs.get_context(project_path, job_id)
        except jobs.AIEditJobError as exc:
            return jsonify(_error_envelope(exc.code, str(exc)))
        return jsonify({"ok": True, "context": context})

    @app.route("/api/runtime/ai-edits/<job_id>/plan", methods=["POST"])
    def submit_ai_edit_plan(job_id: str):  # type: ignore[unused-variable]
        plan = request.get_json(silent=True) or {}
        try:
            job = jobs.submit_plan(project_path, job_id, plan)
        except jobs.AIEditJobError as exc:
            return jsonify(_error_envelope(exc.code, str(exc), **exc.details))
        return jsonify(_job_envelope(job))

    @app.route("/api/runtime/ai-edits/<job_id>/cancel", methods=["POST"])
    def cancel_ai_edit(job_id: str):  # type: ignore[unused-variable]
        try:
            job = jobs.cancel_job(project_path, job_id)
        except jobs.AIEditJobError as exc:
            return jsonify(_error_envelope(exc.code, str(exc)))
        return jsonify(_job_envelope(job))

    @app.route("/api/runtime/ai-edits/<job_id>/retry", methods=["POST"])
    def retry_ai_edit(job_id: str):  # type: ignore[unused-variable]
        try:
            job = jobs.retry_job(project_path, job_id)
        except jobs.AIEditJobError as exc:
            return jsonify(_error_envelope(exc.code, str(exc)))
        return jsonify(_job_envelope(job))

    @app.route("/api/runtime/ai-edits/<job_id>/undo", methods=["POST"])
    def undo_ai_edit(job_id: str):  # type: ignore[unused-variable]
        try:
            job = jobs.undo_job(project_path, job_id)
        except jobs.AIEditJobError as exc:
            return jsonify(_error_envelope(exc.code, str(exc), **exc.details))
        return jsonify(_job_envelope(job))
