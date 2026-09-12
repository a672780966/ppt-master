#!/usr/bin/env python3
"""
PPT Master - Workbench Confirmation API (P5)

Reuses confirm_ui's actual, real Flask app in-process rather than
reimplementing its Stage 1/2 validation pipeline (dozens of interlocking
private validators in confirm_ui/server.py -- typography/palette/candidate-
list/submission-stage checks that are far too easy to subtly get wrong by
hand-porting individually). confirm_ui.server.create_app() is called once
per Workbench session to build an isolated Flask app object, and every
/api/confirm/* route here proxies to it through Werkzeug's in-process test
client -- no socket, no subprocess, no polling, no 590s wait, but still
100% of confirm_ui's own real, unmodified logic actually executing.

Hard rule -- idle_timeout=0, lock_file=None: create_app() unconditionally
starts a background idle-watchdog thread that calls os._exit() on the whole
process once idle past its timeout (confirmed by reading
confirm_ui/server.py:2552-2563 directly). Passing idle_timeout=0 makes that
thread return immediately and never loop -- required so a confirm_ui-owned
watchdog can never kill the Workbench's own long-lived process. lock_file=
None means confirm_ui's own /api/shutdown route (also registered on this
isolated app) never touches a real lock file; this module never calls that
route anyway.

confirm_ui/server.py itself is completely untouched by this file -- only
its already-public create_app() factory is imported.

Usage:
    from workbench.confirm_api import register
    register(app, project_path)

Dependencies:
    flask (already a declared dependency of confirm_ui/server.py)
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, Response, request

from confirm_ui.server import create_app as create_confirm_app


def register(app: Flask, project_path: Path) -> None:
    project_path = Path(project_path)
    confirm_app = create_confirm_app(str(project_path), idle_timeout=0, lock_file=None, server_port=None)
    confirm_client = confirm_app.test_client()

    def _proxy(method: str, path: str) -> Response:
        if method == "GET":
            inner = confirm_client.get(path)
        else:
            inner = confirm_client.post(path, json=request.get_json(silent=True) or {})
        return Response(inner.get_data(), status=inner.status_code, content_type=inner.content_type)

    @app.route("/api/confirm/state")
    def confirm_state():  # type: ignore[unused-variable]
        return _proxy("GET", "/api/recommendations")

    @app.route("/api/confirm/session")
    def confirm_session():  # type: ignore[unused-variable]
        return _proxy("GET", "/api/session")

    @app.route("/api/confirm/catalogs")
    def confirm_catalogs():  # type: ignore[unused-variable]
        return _proxy("GET", "/api/catalogs")

    @app.route("/api/confirm/submit", methods=["POST"])
    def confirm_submit():  # type: ignore[unused-variable]
        return _proxy("POST", "/api/confirm")
