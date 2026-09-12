#!/usr/bin/env python3
"""
PPT Master - AI Edit Job Lifecycle (P6)

The orchestrator: creates jobs, persists them under
runtime/ai_edits/<job_id>/{request,context,plan,result}.json + before.svg,
enforces "one active AI mutator per slide" (SS25-27) by scanning persisted
job status rather than an in-memory map (jobs are also created/submitted
by scripts/ai_edit_cli.py, a separate process per invocation -- an
in-memory map would not survive that), and calls
runtime.edit_conflicts/runtime.edit_plan/runtime.patch_engine for every
actual decision. No business rule lives in this file beyond "what to
persist and when."

Usage:
    from runtime import ai_edit_jobs as jobs
    job = jobs.create_job(project_path, slide_id="P05", scope="selection",
                           selection_ids=["title-01"], instruction="...")
    job = jobs.submit_plan(project_path, job.job_id, plan_dict)

Dependencies:
    None beyond existing sibling PPT Master modules
"""

from __future__ import annotations

import json
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from runtime import controller
from runtime.ai_edit_context import SelectionContextError, build_selection_context
from runtime.build_state import BuildStateError, load as load_build_state
from runtime.edit_conflicts import (
    MAX_PLAN_ATTEMPTS,
    EditConflictError,
    check_undo_conflict,
    decide_plan_attempt,
    decide_revision_conflict,
)
from runtime.edit_plan import EditPlanError, validate_edit_plan
from runtime.patch_engine import PatchEngineError, apply_edit_plan, restore_before_svg

AI_EDITS_DIR_NAME = "ai_edits"
RUNTIME_DIR_NAME = "runtime"

ACTIVE_STATUSES = frozenset({"queued", "running", "patch_ready", "applying", "validating"})
TERMINAL_STATUSES = frozenset({"completed", "completed_with_validation_error", "failed", "conflicted", "cancelled", "interrupted"})


class AIEditJobError(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _ai_edits_root(project_path: Path) -> Path:
    return Path(project_path) / RUNTIME_DIR_NAME / AI_EDITS_DIR_NAME


def _job_dir(project_path: Path, job_id: str) -> Path:
    return _ai_edits_root(project_path) / job_id


@dataclass
class AIEditJob:
    job_id: str
    project_path: str
    slide_id: str
    scope: str
    selection_ids: list[str]
    instruction: str
    origin: str
    annotation_id: str | None
    status: str
    base_revision: int
    rebase_count: int
    plan_attempts: int
    created_at: str
    updated_at: str
    error: dict[str, object] | None = None
    result: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "AIEditJob":
        return cls(**data)

    def dir_path(self) -> Path:
        return _job_dir(Path(self.project_path), self.job_id)


def _save_job(job: AIEditJob) -> None:
    job.updated_at = _utc_timestamp()
    job_dir = job.dir_path()
    job_dir.mkdir(parents=True, exist_ok=True)
    tmp = job_dir / "job.json.tmp"
    tmp.write_text(json.dumps(job.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(job_dir / "job.json")


def load_job(project_path: Path, job_id: str) -> AIEditJob:
    path = _job_dir(project_path, job_id) / "job.json"
    if not path.is_file():
        raise AIEditJobError("TARGET_NOT_FOUND", f"no such AI Edit job: {job_id}", job_id=job_id)
    return AIEditJob.from_dict(json.loads(path.read_text(encoding="utf-8")))


def list_jobs(project_path: Path, *, slide_id: str | None = None, status: str | None = None) -> list[AIEditJob]:
    root = _ai_edits_root(project_path)
    if not root.is_dir():
        return []
    jobs: list[AIEditJob] = []
    for job_json in sorted(root.glob("*/job.json")):
        try:
            job = AIEditJob.from_dict(json.loads(job_json.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if slide_id is not None and job.slide_id != slide_id:
            continue
        if status is not None and job.status != status:
            continue
        jobs.append(job)
    return sorted(jobs, key=lambda j: j.created_at)


def _busy_job_for_slide(project_path: Path, slide_id: str) -> AIEditJob | None:
    for job in list_jobs(project_path, slide_id=slide_id):
        if job.status in ACTIVE_STATUSES:
            return job
    return None


def _svg_path_for_slide(project_path: Path, slide_id: str) -> Path | None:
    svg_dir = Path(project_path) / "svg_output"
    if not svg_dir.is_dir():
        return None
    digits = slide_id[1:]
    matches = sorted(svg_dir.glob(f"{digits}_*.svg")) or sorted(svg_dir.glob(f"0{digits}_*.svg"))
    return matches[0] if matches else None


def _current_ids(project_path: Path, slide_id: str) -> tuple[set[str], Path]:
    svg_path = _svg_path_for_slide(project_path, slide_id)
    if svg_path is None or not svg_path.is_file():
        raise AIEditJobError("TARGET_NOT_FOUND", f"no svg_output file found for {slide_id}", slide=slide_id)
    root = ET.parse(str(svg_path)).getroot()
    return {e.get("id") for e in root.iter() if e.get("id") is not None}, svg_path


def create_job(
    project_path: Path,
    *,
    slide_id: str,
    scope: str,
    selection_ids: list[str],
    instruction: str,
    origin: str = "inline",
    annotation_id: str | None = None,
) -> AIEditJob:
    project_path = Path(project_path)
    busy = _busy_job_for_slide(project_path, slide_id)
    if busy is not None:
        raise AIEditJobError("SLIDE_AI_EDIT_BUSY", f"{slide_id} already has an active AI edit job: {busy.job_id}", job_id=busy.job_id)

    state = load_build_state(project_path)
    if state is None or slide_id not in state.slides:
        raise AIEditJobError("TARGET_NOT_FOUND", f"no build_state slide record for {slide_id}", slide=slide_id)
    expected_revision = state.slides[slide_id].revision

    try:
        context = build_selection_context(
            project_path, slide_id=slide_id, expected_revision=expected_revision,
            plan_revision=state.plan_revision, scope=scope, selection_ids=selection_ids,
            instruction=instruction, origin=origin,
        )
    except SelectionContextError as exc:
        raise AIEditJobError(exc.code, exc.message, **exc.details) from exc

    job = AIEditJob(
        job_id=uuid.uuid4().hex[:12],
        project_path=str(project_path),
        slide_id=slide_id,
        scope=scope,
        selection_ids=list(selection_ids),
        instruction=instruction,
        origin=origin,
        annotation_id=annotation_id,
        status="queued",
        base_revision=expected_revision,
        rebase_count=0,
        plan_attempts=0,
        created_at=_utc_timestamp(),
        updated_at=_utc_timestamp(),
    )
    _save_job(job)
    (job.dir_path() / "request.json").write_text(
        json.dumps({"slide_id": slide_id, "scope": scope, "selection_ids": selection_ids,
                     "instruction": instruction, "origin": origin, "annotation_id": annotation_id,
                     "created_at": job.created_at}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (job.dir_path() / "context.json").write_text(json.dumps(context.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    controller.set_ai_edit_status(project_path, slide_id, active_job=job.job_id)
    return job


def get_context(project_path: Path, job_id: str) -> dict[str, object]:
    job = load_job(project_path, job_id)
    context_path = job.dir_path() / "context.json"
    return json.loads(context_path.read_text(encoding="utf-8"))


def submit_plan(project_path: Path, job_id: str, plan: dict[str, object]) -> AIEditJob:
    project_path = Path(project_path)
    job = load_job(project_path, job_id)
    if job.status == "cancelled":
        raise AIEditJobError("AI_EDIT_CANCELLED", f"job {job_id} was cancelled; plan discarded")
    if job.status not in ("queued",):
        raise AIEditJobError("INVALID_EDIT_PLAN", f"job {job_id} is not awaiting a plan (status={job.status})")

    state = load_build_state(project_path)
    current_revision = state.slides[job.slide_id].revision if state and job.slide_id in state.slides else None

    decision = decide_revision_conflict(current_revision, job.base_revision, job.rebase_count)
    if decision.outcome == "rebase":
        try:
            current_ids, _ = _current_ids(project_path, job.slide_id)
            for selection_id in job.selection_ids:
                if selection_id not in current_ids:
                    raise AIEditJobError("SELECTION_NO_LONGER_EXISTS", f"{selection_id} no longer exists after rebase", id=selection_id)
            context = build_selection_context(
                project_path, slide_id=job.slide_id, expected_revision=current_revision,
                plan_revision=state.plan_revision, scope=job.scope, selection_ids=job.selection_ids,
                instruction=job.instruction, origin=job.origin,
            )
        except (SelectionContextError, AIEditJobError) as exc:
            code = exc.code
            message = exc.message
            details = exc.details
            job.status = "failed"
            job.error = {"code": code, "message": message, **details}
            _save_job(job)
            controller.set_ai_edit_status(project_path, job.slide_id, active_job=None, last_job=job.job_id)
            raise AIEditJobError(code, message, **details) from exc
        (job.dir_path() / "context.json").write_text(json.dumps(context.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        job.base_revision = current_revision
        job.rebase_count = decision.rebase_count
        job.status = "queued"
        _save_job(job)
        raise AIEditJobError("STALE_EDIT", f"revision changed under the AI edit; rebased once, submit a fresh plan against revision {current_revision}", rebase_count=job.rebase_count)
    if decision.outcome == "stop":
        job.status = "conflicted"
        job.error = {"code": "EDIT_CONFLICT_REQUIRES_RETRY", "message": "the page changed again during rebase; this edit must be redone from scratch"}
        _save_job(job)
        controller.set_ai_edit_status(project_path, job.slide_id, active_job=None, last_job=job.job_id)
        raise AIEditJobError("EDIT_CONFLICT_REQUIRES_RETRY", job.error["message"])

    (job.dir_path() / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    job.plan_attempts += 1
    job.status = "applying"
    _save_job(job)

    current_ids, svg_path = _current_ids(project_path, job.slide_id)
    try:
        decide_plan_attempt(job.plan_attempts)
        operations = validate_edit_plan(
            plan,
            scope=job.scope,
            selection_ids=job.selection_ids,
            current_ids=current_ids,
            expected_base_revision=job.base_revision,
        )
        result = apply_edit_plan(
            project_path, job.slide_id, svg_path, operations,
            before_svg_path=job.dir_path() / "before.svg",
            clear_annotation_id=job.annotation_id if job.origin == "annotation" else None,
        )
    except (EditPlanError, PatchEngineError, EditConflictError) as exc:
        retryable_schema_error = (
            isinstance(exc, EditPlanError)
            and exc.code == "INVALID_EDIT_PLAN"
            and job.plan_attempts < MAX_PLAN_ATTEMPTS
        )
        job.status = "queued" if retryable_schema_error else "failed"
        job.error = {"code": exc.code, "message": exc.message, **getattr(exc, "details", {})}
        _save_job(job)
        if not retryable_schema_error:
            controller.set_ai_edit_status(project_path, job.slide_id, active_job=None, last_job=job.job_id)
        raise AIEditJobError(
            exc.code,
            exc.message,
            retryable=retryable_schema_error,
            attempts=job.plan_attempts,
            max_attempts=MAX_PLAN_ATTEMPTS,
            **getattr(exc, "details", {}),
        ) from exc

    validated_ok = bool(result.validate_envelope.get("ok"))
    job.status = "completed" if validated_ok else "completed_with_validation_error"
    job.result = {
        "new_revision": result.new_revision,
        "validated_revision": result.validated_revision,
        "validation_status": result.validation_status,
        "validate_ok": validated_ok,
    }
    _save_job(job)
    (job.dir_path() / "result.json").write_text(json.dumps(job.result, indent=2, ensure_ascii=False), encoding="utf-8")
    controller.set_ai_edit_status(project_path, job.slide_id, active_job=None, last_job=job.job_id)
    return job


def cancel_job(project_path: Path, job_id: str) -> AIEditJob:
    project_path = Path(project_path)
    job = load_job(project_path, job_id)
    if job.status not in ("queued", "running"):
        raise AIEditJobError("AI_EDIT_CANCELLED", f"job {job_id} cannot be cancelled from status {job.status}")
    job.status = "cancelled"
    _save_job(job)
    controller.set_ai_edit_status(project_path, job.slide_id, active_job=None, last_job=job.job_id)
    return job


def retry_job(project_path: Path, job_id: str) -> AIEditJob:
    """Spawn a fresh job with the same slide/scope/selection/instruction,
    against the current revision -- a clean restart, never a resurrection
    of the old job's stale plan/context."""
    project_path = Path(project_path)
    job = load_job(project_path, job_id)
    if job.status not in ("failed", "conflicted", "cancelled", "interrupted"):
        raise AIEditJobError("INVALID_EDIT_PLAN", f"job {job_id} is not in a retryable status ({job.status})")
    return create_job(
        project_path, slide_id=job.slide_id, scope=job.scope, selection_ids=job.selection_ids,
        instruction=job.instruction, origin=job.origin, annotation_id=job.annotation_id,
    )


def undo_job(project_path: Path, job_id: str) -> AIEditJob:
    project_path = Path(project_path)
    job = load_job(project_path, job_id)
    if job.status not in ("completed", "completed_with_validation_error"):
        raise AIEditJobError("UNDO_CONFLICT", f"job {job_id} has no applied edit to undo (status={job.status})")
    before_path = job.dir_path() / "before.svg"
    if not before_path.is_file():
        raise AIEditJobError("UNDO_CONFLICT", f"job {job_id} has no before.svg snapshot")

    state = load_build_state(project_path)
    current_revision = state.slides[job.slide_id].revision if state and job.slide_id in state.slides else None
    try:
        check_undo_conflict(current_revision, job.result["new_revision"])
    except EditConflictError as exc:
        raise AIEditJobError(exc.code, exc.message, **exc.details) from exc

    svg_path = _svg_path_for_slide(project_path, job.slide_id)
    if svg_path is None:
        raise AIEditJobError("TARGET_NOT_FOUND", f"no svg_output file found for {job.slide_id}")

    new_revision = restore_before_svg(project_path, job.slide_id, svg_path, before_path)
    job.status = "completed"
    job.result = {**(job.result or {}), "undone": True, "undo_revision": new_revision}
    _save_job(job)
    return job


def reconcile_interrupted_jobs(project_path: Path) -> list[str]:
    """Called once at Workbench startup (SS56-58): any job whose last known
    status is not yet terminal is flipped to "interrupted" -- never
    auto-applies a leftover plan."""
    flipped: list[str] = []
    for job in list_jobs(project_path):
        if job.status in ACTIVE_STATUSES:
            job.status = "interrupted"
            _save_job(job)
            try:
                controller.set_ai_edit_status(project_path, job.slide_id, active_job=None, last_job=job.job_id)
            except BuildStateError:
                pass
            flipped.append(job.job_id)
    return flipped
