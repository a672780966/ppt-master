#!/usr/bin/env python3
"""
PPT Master - Build Job Queue (P1)

FIFO job queue layered on runtime.build_state: enqueue, list, dequeue (peek
the oldest queued job), and terminal transitions (done/reject/cancel).

Usage:
    Imported by scripts/build_state.py.

Examples:
    from runtime.jobs import enqueue, list_jobs, dequeue, mark_done, mark_rejected

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

import itertools
from pathlib import Path

from runtime.build_state import (
    JOB_TYPES,
    SLIDE_ID_RE,
    BuildStateError,
    Job,
    locked_state,
    utc_timestamp,
)

_JOB_ID_RE_WIDTH = 3


def _next_job_id(existing: list[Job]) -> str:
    used = set()
    for job in existing:
        try:
            used.add(int(job.id.split("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    next_number = next(n for n in itertools.count(1) if n not in used)
    return f"job-{next_number:0{_JOB_ID_RE_WIDTH}d}"


def enqueue(
    project_path: Path,
    *,
    job_type: str,
    slide: str | None = None,
    expected_revision: int | None = None,
    note: str = "",
) -> Job:
    if job_type not in JOB_TYPES:
        raise BuildStateError(f"invalid job type: {job_type!r}")
    if slide is not None and not SLIDE_ID_RE.match(slide):
        raise BuildStateError(f"invalid slide id: {slide!r}")
    with locked_state(project_path) as state:
        job = Job(
            id=_next_job_id(state.jobs),
            type=job_type,
            slide=slide,
            expected_revision=expected_revision,
            status="queued",
            created_at=utc_timestamp(),
            note=note,
        )
        state.jobs.append(job)
        return job


def list_jobs(project_path: Path, *, status: str | None = None) -> list[Job]:
    from runtime.build_state import load

    state = load(project_path)
    if state is None:
        return []
    if status is None:
        return list(state.jobs)
    return [job for job in state.jobs if job.status == status]


def dequeue(project_path: Path) -> Job | None:
    """Return (without mutating) the oldest queued job, FIFO by creation order."""
    from runtime.build_state import load

    state = load(project_path)
    if state is None:
        return None
    queued = [job for job in state.jobs if job.status == "queued"]
    if not queued:
        return None
    return min(queued, key=lambda job: job.created_at or "")


def _transition(project_path: Path, job_id: str, new_status: str, *, reason: str = "") -> Job:
    with locked_state(project_path, create_if_missing=False) as state:
        for job in state.jobs:
            if job.id == job_id:
                job.status = new_status
                if reason:
                    job.note = reason
                return job
        raise BuildStateError(f"no job with id {job_id!r}")


def mark_in_progress(project_path: Path, job_id: str) -> Job:
    return _transition(project_path, job_id, "in_progress")


def mark_done(project_path: Path, job_id: str) -> Job:
    return _transition(project_path, job_id, "done")


def mark_rejected(project_path: Path, job_id: str, *, reason: str) -> Job:
    return _transition(project_path, job_id, "rejected", reason=reason)


def mark_cancelled(project_path: Path, job_id: str, *, reason: str = "") -> Job:
    return _transition(project_path, job_id, "cancelled", reason=reason)
