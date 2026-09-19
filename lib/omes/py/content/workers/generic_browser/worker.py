#!/usr/bin/env python3
"""generic_browser worker - the one concrete platform-worker skeleton
implemented in issue #66.

Invoked by the manager exactly as documented in
docs/content-distribution.md section 6:

    python3 workers/generic_browser/worker.py <operation>

with the operation's JSON request on stdin and exactly one JSON object
printed to stdout. Selectors/platform-specific logic are not here at all
- this file only implements the transport, the filesystem permission
boundary (workers/base.py), and driver dispatch; the actual "how to click
publish" logic lives in a pluggable driver (see `drivers/__init__.py`).

Driver selection: `OMES_CONTENT_BROWSER_DRIVER`, if set, is a filesystem
path to an operator-installed driver module (for example a
Playwright/Chromium driver, or a path into a Hermes browser-automation
integration); this worker loads it dynamically. If unset, the default
`drivers.manual_stub` driver is used - it never launches a real browser
and only ever records evidence, so the whole manager/CLI/state-machine
pipeline can be exercised with zero browser dependency. OMES itself never
bundles or vendors a browser.

Operations implemented: prepare, bootstrap-session, publish, verify,
collect-evidence, revoke-session.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_WORKERS_DIR = os.path.dirname(_THIS_DIR)
for _p in (_WORKERS_DIR, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from base import (  # noqa: E402
    enforce_path_boundary,
    ensure_evidence_dir,
    ensure_session_dir,
    main_with_operations,
    typed_failure,
)


def _load_driver_from_path(path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location("omes_content_browser_driver", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load OMES_CONTENT_BROWSER_DRIVER={path!r}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _driver() -> ModuleType:
    driver_path = os.environ.get("OMES_CONTENT_BROWSER_DRIVER")
    if driver_path:
        return _load_driver_from_path(driver_path)
    from drivers import manual_stub  # noqa: PLC0415 - deliberately lazy/default

    return manual_stub


def _build_ctx(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = payload.get("allowed_paths") or {}
    allowed_roots = [v for v in allowed.values() if v]
    session_dir_raw = allowed.get("session_dir") or payload.get("session_dir")
    evidence_dir_raw = allowed.get("evidence_dir")
    return {
        "job_id": payload.get("job_id"),
        "platform": payload.get("platform") or "generic_browser",
        "session_dir": Path(session_dir_raw) if session_dir_raw else None,
        "evidence_dir": Path(evidence_dir_raw) if evidence_dir_raw else None,
        "allowed_roots": allowed_roots,
    }


def op_prepare(payload: dict[str, Any]) -> dict[str, Any]:
    ctx = _build_ctx(payload)
    if ctx["session_dir"] is None:
        return typed_failure("nonretryable", "prepare requires allowed_paths.session_dir")
    enforce_path_boundary(ctx["session_dir"], ctx["allowed_roots"])
    ensure_session_dir(ctx["session_dir"])
    source_path = payload.get("source_path")
    if source_path:
        enforce_path_boundary(source_path, ctx["allowed_roots"])
    return {"status": "ok", "ready": True}


def op_bootstrap_session(payload: dict[str, Any]) -> dict[str, Any]:
    ctx = _build_ctx(payload)
    if ctx["session_dir"] is None:
        return typed_failure("nonretryable", "bootstrap-session requires allowed_paths.session_dir")
    enforce_path_boundary(ctx["session_dir"], ctx["allowed_roots"])
    ensure_session_dir(ctx["session_dir"])
    return _driver().bootstrap(payload, ctx)


def op_publish(payload: dict[str, Any]) -> dict[str, Any]:
    ctx = _build_ctx(payload)
    if ctx["session_dir"] is None:
        return typed_failure("nonretryable", "publish requires allowed_paths.session_dir")
    enforce_path_boundary(ctx["session_dir"], ctx["allowed_roots"])
    if ctx["evidence_dir"] is not None:
        enforce_path_boundary(ctx["evidence_dir"], ctx["allowed_roots"])
        ensure_evidence_dir(ctx["evidence_dir"])
    source_path = payload.get("source_path")
    if source_path:
        enforce_path_boundary(source_path, ctx["allowed_roots"])
    return _driver().publish(payload, ctx)


def op_verify(payload: dict[str, Any]) -> dict[str, Any]:
    ctx = _build_ctx(payload)
    return _driver().verify(payload, ctx)


def op_collect_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    ctx = _build_ctx(payload)
    evidence_dir = ctx["evidence_dir"]
    if evidence_dir is None or not evidence_dir.is_dir():
        return {"status": "ok", "evidence_paths": []}
    enforce_path_boundary(evidence_dir, ctx["allowed_roots"])
    evidence_paths = []
    for candidate in sorted(evidence_dir.rglob("*")):
        if candidate.is_file():
            enforce_path_boundary(candidate, ctx["allowed_roots"])
            evidence_paths.append(str(candidate))
    return {"status": "ok", "evidence_paths": evidence_paths}


def op_revoke_session(payload: dict[str, Any]) -> dict[str, Any]:
    ctx = _build_ctx(payload)
    session_dir = ctx["session_dir"]
    if session_dir is not None and session_dir.is_dir():
        enforce_path_boundary(session_dir, ctx["allowed_roots"])
        for child in session_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    return {"status": "ok"}


OPERATIONS = {
    "prepare": op_prepare,
    "bootstrap-session": op_bootstrap_session,
    "publish": op_publish,
    "verify": op_verify,
    "collect-evidence": op_collect_evidence,
    "revoke-session": op_revoke_session,
}


def main(argv: list[str] | None = None) -> int:
    if argv is not None:
        sys.argv = [sys.argv[0], *argv]
    return main_with_operations(OPERATIONS)


if __name__ == "__main__":
    sys.exit(main())
