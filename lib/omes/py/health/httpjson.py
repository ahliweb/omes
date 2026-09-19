"""lib/omes/py/health/httpjson.py - tiny stdlib-only bounded-timeout JSON
HTTP helper shared by the health checkers under lib/omes/py/health/.

Standard library only (ADR-0012 / docs/adr/0012-python-stdlib-for-workflow-engines.md):
uses urllib.request and json, nothing else. Every call takes an explicit
timeout and never retries silently - callers decide their own retry
policy, if any.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional


class HttpJsonError(Exception):
    """Raised for any network, timeout, HTTP-status, or JSON-parse failure.

    Callers should catch this and turn it into a "fail" check result with
    a remediation string; it deliberately does not distinguish more than
    is necessary for that (the .reason attribute carries a short, safe
    machine-readable category).
    """

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


def request_json(
    url: str,
    method: str = "GET",
    payload: Optional[dict] = None,
    timeout: float = 10.0,
) -> Any:
    """Performs one bounded-timeout HTTP request and returns the parsed
    JSON body. Raises HttpJsonError on any failure - connection refused,
    timeout, non-2xx status, or a response body that is not valid JSON.

    Never logs the request/response body itself (callers are responsible
    for only passing synthetic, non-secret payloads - see docs/security.md
    and issue #71's "never log full prompts/outputs" requirement); this
    function itself does not log anything.
    """
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local-only, bounded)
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise HttpJsonError("http_status", f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", exc))
        if "timed out" in reason.lower():
            raise HttpJsonError("timeout", reason) from exc
        raise HttpJsonError("unreachable", reason) from exc
    except TimeoutError as exc:
        raise HttpJsonError("timeout", str(exc)) from exc

    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpJsonError("malformed_json", str(exc)) from exc


def capped(text: str, limit: int = 120) -> str:
    """Length-caps a string for safe, synthetic-only echoing in output
    (issue #71: "never log full prompts/outputs beyond a length-capped,
    synthetic-only echo"). Never used on secret values.
    """
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"
