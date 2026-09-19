#!/usr/bin/env python3
"""Fake platform worker implementing the contract in
docs/content-distribution.md section 6, for tests only. Controllable
outcomes via environment variables (never real media/credentials):

  FAKE_WORKER_PUBLISH_STATUS   ok|uncertain|error (default: ok)
  FAKE_WORKER_PUBLISH_URL      resulting URL for a successful publish
  FAKE_WORKER_PUBLISH_RETRYABLE  "true"/"false" (only for status=error)
  FAKE_WORKER_VERIFY_STATUS    ok|uncertain|error (default: ok)
  FAKE_WORKER_VERIFY_URL       resulting URL for a successful verify
  FAKE_WORKER_COUNTER_FILE     path; each `publish` call appends one line,
                                so tests can assert publish was called at
                                most once even across a resume cycle.
"""
import json
import os
import sys


def _read_payload():
    raw = sys.stdin.read()
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def _bump_counter():
    path = os.environ.get("FAKE_WORKER_COUNTER_FILE")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("publish\n")


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "retryable": False, "note": "missing operation"}))
        return 0
    operation = sys.argv[1]
    _read_payload()

    if operation == "prepare":
        print(json.dumps({"status": "ok", "ready": True}))
        return 0

    if operation == "publish":
        _bump_counter()
        status = os.environ.get("FAKE_WORKER_PUBLISH_STATUS", "ok")
        if status == "ok":
            result = {
                "status": "ok",
                "url": os.environ.get("FAKE_WORKER_PUBLISH_URL", "https://example.invalid/post/1"),
                "screenshot_ref": None,
                "retryable": False,
                "note": "fake publish ok",
            }
        elif status == "uncertain":
            result = {"status": "uncertain", "url": None, "screenshot_ref": None, "retryable": False, "note": "fake publish uncertain"}
        else:
            retryable = os.environ.get("FAKE_WORKER_PUBLISH_RETRYABLE", "true") == "true"
            result = {"status": "error", "url": None, "screenshot_ref": None, "retryable": retryable, "note": "fake publish error"}
        print(json.dumps(result))
        return 0

    if operation == "verify":
        status = os.environ.get("FAKE_WORKER_VERIFY_STATUS", "ok")
        if status == "ok":
            result = {
                "status": "ok",
                "url": os.environ.get("FAKE_WORKER_VERIFY_URL", "https://example.invalid/post/1"),
                "retryable": False,
                "note": "fake verify ok",
            }
        elif status == "uncertain":
            result = {"status": "uncertain", "url": None, "retryable": False, "note": "fake verify uncertain"}
        else:
            retryable = os.environ.get("FAKE_WORKER_VERIFY_RETRYABLE", "true") == "true"
            result = {"status": "error", "url": None, "retryable": retryable, "note": "fake verify error"}
        print(json.dumps(result))
        return 0

    if operation == "collect-evidence":
        print(json.dumps({"status": "ok", "evidence_paths": []}))
        return 0

    if operation == "revoke-session":
        print(json.dumps({"status": "ok"}))
        return 0

    print(json.dumps({"status": "error", "retryable": False, "note": f"unknown operation {operation!r}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
