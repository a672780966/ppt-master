"""PPT Master - Persistent Build Runtime (P1)

Execution/orchestration state for a project: phase, per-slide revision and
dirty/stale tracking, a job queue, a validation-gate mirror, and export
readiness. Owns none of a project's design intent, content, quality
provenance, or audit history — see references/artifact-ownership.md.
"""
