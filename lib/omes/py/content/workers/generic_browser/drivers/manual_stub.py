"""Default "manual/headless-stub" browser driver (issue #66).

This is the ONLY driver OMES ships. It never launches a real browser and
has no third-party dependency: `bootstrap` simulates a manual login by
writing a marker file into the worker's own `session_dir` (a real driver,
e.g. an operator-installed Playwright/Chromium driver, would instead open
an actual browser window for the operator to log in, then let the
browser persist real cookies into that same directory); `publish` and
`verify` only ever write/read evidence files under `evidence_dir` and
never touch the network.

Use this driver to exercise the manager/CLI/state-machine plumbing
end-to-end without any real platform account or browser dependency. For
actual publishing, an operator installs a real driver (Playwright,
Selenium, or Hermes's own browser automation) and points
`OMES_CONTENT_BROWSER_DRIVER` at it - see docs/content-distribution.md
section 6 and `../drivers/__init__.py`.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_WORKERS_DIR = os.path.dirname(os.path.dirname(_THIS_DIR))
if _WORKERS_DIR not in sys.path:
    sys.path.insert(0, _WORKERS_DIR)

from base import enforce_path_boundary, ok_result, typed_failure  # noqa: E402

_LOGIN_MARKER = "manual_stub_logged_in.json"


def bootstrap(payload: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    session_dir = ctx["session_dir"]
    marker = enforce_path_boundary(session_dir / _LOGIN_MARKER, ctx["allowed_roots"])
    marker.write_text(
        json.dumps({"platform": ctx["platform"], "bootstrapped_at": payload.get("_now", "")}),
        encoding="utf-8",
    )
    os.chmod(marker, 0o600)
    return ok_result(note="manual_stub: session marked as logged in (no real browser/session data written)")


def _is_logged_in(ctx: dict[str, Any]) -> bool:
    return (ctx["session_dir"] / _LOGIN_MARKER).is_file()


def publish(payload: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    if not _is_logged_in(ctx):
        return typed_failure("needs_login", "manual_stub: no bootstrapped session for this platform")

    source_path = payload.get("source_path")
    if not source_path or not Path(source_path).is_file():
        return typed_failure("nonretryable", f"source_path missing or not a file: {source_path!r}")

    evidence_dir = ctx["evidence_dir"]
    evidence_path = enforce_path_boundary(evidence_dir / f"{ctx['job_id']}-publish.json", ctx["allowed_roots"])
    evidence_path.write_text(
        json.dumps(
            {
                "job_id": ctx["job_id"],
                "platform": ctx["platform"],
                "caption": payload.get("caption"),
                "note": "manual_stub evidence: no real screenshot bytes are ever produced or stored",
            }
        ),
        encoding="utf-8",
    )
    url = f"https://example.invalid/manual-stub/{ctx['platform']}/{ctx['job_id']}"
    return ok_result(url=url, screenshot_ref=str(evidence_path.relative_to(evidence_dir.parent.parent)), note="manual_stub: published (simulated)")


def verify(payload: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    url = payload.get("url")
    if not url or not url.startswith("https://example.invalid/manual-stub/"):
        return typed_failure("uncertain", f"manual_stub cannot verify an unrecognized URL: {url!r}")
    evidence_dir = ctx["evidence_dir"]
    evidence_path = evidence_dir / f"{ctx['job_id']}-publish.json"
    if not evidence_path.is_file():
        return typed_failure("uncertain", "manual_stub: no publish evidence found for this job")
    return ok_result(url=url, note="manual_stub: verified (simulated)")
