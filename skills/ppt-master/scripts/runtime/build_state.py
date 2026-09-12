#!/usr/bin/env python3
"""
PPT Master - Persistent Build State Core

Load/save build_state.json with atomic writes and a short physical-critical-
section lock. Owns only execution/orchestration state: phase, per-slide
revision/status/dirty tracking, a job queue, a validation-gate mirror, and
export readiness. Never a source of design intent, content, quality
provenance, or audit history (see references/artifact-ownership.md).

Usage:
    Imported by scripts/build_state.py and its sibling runtime modules.

Examples:
    from runtime.build_state import load, save, locked_state

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA_ID = "ppt-master.build-state.v1"
STATE_RELATIVE_PATH = Path("build_state.json")
LOCK_RELATIVE_PATH = Path(".build_state.lock")

SLIDE_ID_RE = re.compile(r"^P[0-9]{2,}$")
CONTENT_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

SLIDE_STATUSES = (
    "planned",
    "building",
    "ready",
    "dirty",
    "validating",
    "failed",
    "stale",
)
JOB_TYPES = ("ai-edit", "direct-edit", "regen", "export")
JOB_STATUSES = ("queued", "in_progress", "done", "rejected", "cancelled")
ROUTES = ("default", "quick", "beautify", "image-to-pptx", "edit-native")
PHASES = ("planned", "building", "ready", "exporting", "exported")
GATE_STATUSES = ("pending", "passed", "failed")
EARLY_GATE_STATUSES = GATE_STATUSES + ("skipped",)
HOOKS_MODES = ("shadow", "enforce")
VALIDATION_STATUSES = ("passed", "passed-with-warnings", "failed")


class BuildStateError(RuntimeError):
    """Raised for malformed build_state.json content or invalid arguments."""


class StaleEditError(RuntimeError):
    """A slide mutation's --expected-revision did not match the on-disk revision."""

    def __init__(self, slide_id: str, expected: int, actual: int) -> None:
        self.slide_id = slide_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"{slide_id} is at revision {actual}; expected {expected}"
        )


class BuildStateLockedError(RuntimeError):
    """Another build_state.py invocation is holding the write lock."""


def utc_timestamp() -> str:
    """Return a compact UTC timestamp, matching workflow_log.py's format."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def state_path(project_path: Path) -> Path:
    return Path(project_path) / STATE_RELATIVE_PATH


def _lock_path(project_path: Path) -> Path:
    return Path(project_path) / LOCK_RELATIVE_PATH


@dataclass
class SlideState:
    revision: int = 0
    plan_revision: int = 0
    status: str = "planned"
    dirty: bool = False
    content_hash: str | None = None
    svg_path: str | None = None
    validated_revision: int | None = None
    validation_status: str | None = None
    ai_edit_active_job: str | None = None
    ai_edit_last_job: str | None = None
    updated_at: str | None = None

    def validate(self, slide_id: str) -> None:
        if self.status not in SLIDE_STATUSES:
            raise BuildStateError(f"{slide_id}: invalid status {self.status!r}")
        if self.content_hash is not None and not CONTENT_HASH_RE.match(self.content_hash):
            raise BuildStateError(f"{slide_id}: invalid content_hash {self.content_hash!r}")
        if self.validation_status is not None and self.validation_status not in VALIDATION_STATUSES:
            raise BuildStateError(f"{slide_id}: invalid validation_status {self.validation_status!r}")


@dataclass
class Job:
    id: str
    type: str
    slide: str | None = None
    expected_revision: int | None = None
    status: str = "queued"
    created_at: str | None = None
    note: str = ""

    def validate(self) -> None:
        if self.type not in JOB_TYPES:
            raise BuildStateError(f"{self.id}: invalid job type {self.type!r}")
        if self.status not in JOB_STATUSES:
            raise BuildStateError(f"{self.id}: invalid job status {self.status!r}")
        if self.slide is not None and not SLIDE_ID_RE.match(self.slide):
            raise BuildStateError(f"{self.id}: invalid slide id {self.slide!r}")


@dataclass
class BuildState:
    phase: str = "planned"
    route: str = "default"
    plan_revision: int = 0
    plan_status: str = "active"
    slides: dict[str, SlideState] = field(default_factory=dict)
    jobs: list[Job] = field(default_factory=list)
    early_gate: str = "pending"
    final_gate: str = "pending"
    final_gate_report_sha256: str | None = None
    export_dirty: bool = True
    last_export: str | None = None
    export_plan_revision: int | None = None
    export_deck_revision: int | None = None
    hooks_mode: str = "shadow"
    stop_block_count: int = 0
    project_updated_at: str | None = None
    plan_updated_at: str | None = None

    def validate(self) -> None:
        if self.phase not in PHASES:
            raise BuildStateError(f"invalid project.phase {self.phase!r}")
        if self.route not in ROUTES:
            raise BuildStateError(f"invalid project.route {self.route!r}")
        if self.plan_status not in ("active", "superseded"):
            raise BuildStateError(f"invalid plan.status {self.plan_status!r}")
        if self.early_gate not in EARLY_GATE_STATUSES:
            raise BuildStateError(f"invalid validation.early_gate {self.early_gate!r}")
        if self.final_gate not in GATE_STATUSES:
            raise BuildStateError(f"invalid validation.final_gate {self.final_gate!r}")
        if self.hooks_mode not in HOOKS_MODES:
            raise BuildStateError(f"invalid hooks.mode {self.hooks_mode!r}")
        if self.stop_block_count < 0:
            raise BuildStateError(f"invalid hooks.stop_block_count {self.stop_block_count!r}")
        for slide_id, slide in self.slides.items():
            if not SLIDE_ID_RE.match(slide_id):
                raise BuildStateError(f"invalid slide id {slide_id!r}")
            slide.validate(slide_id)
        for job in self.jobs:
            job.validate()

    def to_json(self) -> dict[str, object]:
        return {
            "schema": SCHEMA_ID,
            "hooks": {
                "mode": self.hooks_mode,
                "stop_block_count": self.stop_block_count,
            },
            "project": {
                "phase": self.phase,
                "route": self.route,
                "updated_at": self.project_updated_at,
            },
            "plan": {
                "revision": self.plan_revision,
                "status": self.plan_status,
                "updated_at": self.plan_updated_at,
            },
            "slides": {
                slide_id: asdict(slide) for slide_id, slide in sorted(self.slides.items())
            },
            "jobs": [asdict(job) for job in self.jobs],
            "validation": {
                "early_gate": self.early_gate,
                "final_gate": self.final_gate,
                "final_gate_report_sha256": self.final_gate_report_sha256,
            },
            "export": {
                "dirty": self.export_dirty,
                "last_export": self.last_export,
                "plan_revision": self.export_plan_revision,
                "deck_revision": self.export_deck_revision,
            },
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> "BuildState":
        if data.get("schema") != SCHEMA_ID:
            raise BuildStateError(
                f"unsupported build_state.json schema: {data.get('schema')!r}"
            )
        project = data.get("project", {})
        plan = data.get("plan", {})
        validation = data.get("validation", {})
        export = data.get("export", {})
        hooks = data.get("hooks", {})
        raw_slides = data.get("slides", {})
        raw_jobs = data.get("jobs", [])
        if not isinstance(raw_slides, dict) or not isinstance(raw_jobs, list):
            raise BuildStateError("build_state.json has malformed slides/jobs")
        slides = {
            slide_id: SlideState(**slide_fields)
            for slide_id, slide_fields in raw_slides.items()
        }
        jobs = [Job(**job_fields) for job_fields in raw_jobs]
        state = cls(
            phase=project.get("phase", "planned"),
            route=project.get("route", "default"),
            project_updated_at=project.get("updated_at"),
            plan_revision=plan.get("revision", 0),
            plan_status=plan.get("status", "active"),
            plan_updated_at=plan.get("updated_at"),
            slides=slides,
            jobs=jobs,
            early_gate=validation.get("early_gate", "pending"),
            final_gate=validation.get("final_gate", "pending"),
            final_gate_report_sha256=validation.get("final_gate_report_sha256"),
            export_dirty=export.get("dirty", True),
            last_export=export.get("last_export"),
            export_plan_revision=export.get("plan_revision"),
            export_deck_revision=export.get("deck_revision"),
            hooks_mode=hooks.get("mode", "shadow"),
            stop_block_count=hooks.get("stop_block_count", 0),
        )
        state.validate()
        return state


def _migrate(data: dict[str, object]) -> dict[str, object]:
    """Extension point for a future schema version.

    Schema stays ppt-master.build-state.v1 for the P4 field additions
    (hooks.mode/stop_block_count, per-slide svg_path/validated_revision/
    validation_status, export.plan_revision/deck_revision) -- from_json()'s
    own .get(..., default) calls already default every one of them for an
    on-disk file written before P4, so no rewrite is needed here today.
    """
    return data


def load(project_path: Path) -> BuildState | None:
    """Load build_state.json. Returns None when absent (a legacy project)."""
    path = state_path(project_path)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildStateError(f"cannot read {path}: {exc}") from exc
    return BuildState.from_json(_migrate(raw))


def save(project_path: Path, state: BuildState) -> Path:
    """Atomically replace build_state.json with the given state."""
    state.validate()
    path = state_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_json(), ensure_ascii=False, indent=2) + "\n"
    temporary_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary_path.write_text(payload, encoding="utf-8")
    os.replace(temporary_path, path)
    return path


def _claim_lock(project_path: Path, timeout_s: float) -> Path:
    lock_path = _lock_path(project_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return lock_path
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise BuildStateLockedError(
                    f"{lock_path} is held by another build_state.py invocation "
                    f"(timeout after {timeout_s}s)"
                ) from None
            time.sleep(0.05)


def _release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except OSError:
        pass


@contextmanager
def locked_state(
    project_path: Path,
    *,
    timeout_s: float = 5.0,
    create_if_missing: bool = True,
) -> Iterator[BuildState]:
    """Claim the write lock, yield the current (or a fresh) state, save on exit.

    Protects the physical read-modify-write critical section against two
    near-simultaneous build_state.py invocations. Semantic staleness across a
    slide's own revision is a separate concern, handled by expected_revision
    compare-and-swap in runtime/revisions.py — both mechanisms are needed.
    """
    project_path = Path(project_path)
    lock_path = _claim_lock(project_path, timeout_s)
    try:
        state = load(project_path)
        if state is None:
            if not create_if_missing:
                raise BuildStateError(f"no build_state.json under {project_path}")
            state = BuildState()
        yield state
        save(project_path, state)
    finally:
        _release_lock(lock_path)
