"""Generic platform-worker subprocess invocation (issue #66 defines the
concrete platform workers; this module only implements the transport and
classification described in docs/content-distribution.md section 6).
Stdlib only (ADR-0012).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Any

# Fields whose values must never leave a worker result unredacted, mirroring
# the audit-log redaction rule (docs/content-distribution.md section 8).
_SECRET_KEY_RE = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|COOKIE)", re.IGNORECASE)

DEFAULT_TIMEOUT_SECONDS = 60


def _redact_result(result: dict[str, Any]) -> dict[str, Any]:
    redacted = {}
    for k, v in result.items():
        if _SECRET_KEY_RE.search(k):
            redacted[k] = "[REDACTED]"
        else:
            redacted[k] = v
    return redacted


def run_worker(
    executable: str,
    operation: str,
    payload: dict[str, Any],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Invokes `python3 <executable> <operation>` with `payload` as JSON on
    stdin, and returns the parsed JSON result (redacted).

    A non-zero exit, a timeout, or stdout that does not parse as a single
    JSON object is never treated as success - it is downgraded to
    {"status": "uncertain", ...} so the caller can route it to
    manual-review rather than silently losing the outcome.
    """
    try:
        proc = subprocess.run(
            [sys.executable, executable, operation],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "uncertain", "url": None, "retryable": False, "note": "worker timed out"}
    except OSError as exc:
        return {"status": "uncertain", "url": None, "retryable": False, "note": f"worker invocation failed: {exc}"}

    if proc.returncode != 0:
        return {
            "status": "uncertain",
            "url": None,
            "retryable": False,
            "note": f"worker exited {proc.returncode}",
        }

    try:
        result = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {"status": "uncertain", "url": None, "retryable": False, "note": "worker stdout was not valid JSON"}

    if not isinstance(result, dict) or "status" not in result:
        return {"status": "uncertain", "url": None, "retryable": False, "note": "worker result missing status"}

    return _redact_result(result)
