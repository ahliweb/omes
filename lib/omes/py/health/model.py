"""lib/omes/py/health/model.py - the layered health/readiness data model
shared by lib/omes/py/health/hermes.py (issue #79).

Standard library only (ADR-0012). This module has no knowledge of Hermes,
Ollama, or Telegram specifics - it defines the shape every layer reports
in, and how layers combine into the top-level `ready`/`connected` result.
Keeping this separate from hermes.py makes the aggregation rules
independently testable and reusable by any future layer.
"""

from __future__ import annotations

from typing import Any, Optional

# The five signal names issue #79 requires OMES to distinguish. Not every
# layer reports every signal - a layer reports whichever are meaningful
# for it (see docs/hermes-integration.md "Health and readiness" for what
# each layer's signals prove).
SIGNAL_NAMES = ("enabled", "active", "reachable", "ready", "connected")

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_NOT_APPLICABLE = "not_applicable"
# Issue #292: "evidence is absent, ambiguous, or could not be obtained"
# (timeout, auth required, clock skew, nothing configured to prove). It is
# deliberately NOT a pass: aggregate() treats it as blocking readiness.
# Only the inference-endpoint evidence layers (inference_endpoint.py) emit
# it today; the Hermes layers still use pass|fail|not_applicable.
STATUS_UNKNOWN = "unknown"

AUTHORITY_HERMES = "hermes"
AUTHORITY_OMES_HOST = "omes-host"
AUTHORITY_EXTERNAL_PROVIDER = "external-provider"


def layer_result(
    status: str,
    signals: Optional[dict] = None,
    proves: str = "",
    remediation: Optional[str] = None,
    detail: str = "",
    authority: str = "omes-host",
    source: str = "omes",
    compatibility_fallback: bool = False,
    removal_trigger: Optional[str] = None,
    reason: Optional[str] = None,
    checked_at: Optional[str] = None,
) -> dict:
    """Builds one layer's result object. `signals` maps a subset of
    SIGNAL_NAMES to True/False/None (None = not evaluated for this
    layer). `proves` is a short human sentence describing exactly what a
    "pass" on this layer establishes - and, just as importantly, what it
    does NOT establish (docs/hermes-integration.md's "green signals can
    lie" principle, issue #79's core requirement).

    Every layer identifies its authority ("hermes", "omes-host", or
    "external-provider") and source ("hermes doctor", "systemd", etc.,
    issue #178). If a fallback is used because stable Hermes lacks a
    machine-readable signal, compatibility_fallback is True with an
    explicit removal_trigger.

    `reason` (a stable machine-readable code, e.g. "timeout") and
    `checked_at` (an RFC 3339 UTC timestamp of the observation) are
    additive, optional extensions introduced by issue #292; they are
    omitted from the result when None, so every pre-existing caller's
    output is byte-for-byte unchanged.
    """
    res = {
        "status": status,
        "signals": signals or {},
        "proves": proves,
        "remediation": remediation,
        "detail": detail,
        "authority": authority,
        "source": source,
        "compatibility_fallback": compatibility_fallback,
    }
    if removal_trigger is not None:
        res["removal_trigger"] = removal_trigger
    if reason is not None:
        res["reason"] = reason
    if checked_at is not None:
        res["checked_at"] = checked_at
    return res


def aggregate(layers: dict) -> tuple:
    """Combines per-layer results into the top-level (ready, connected)
    booleans.

    - `ready` requires host, runtime, and gateway to each be `pass` (a
      layer that is `not_applicable` - which none of these three ever
      are - would also count as not blocking), AND the provider layer to
      be `pass` OR `not_applicable` (a provider that was never
      configured must not block readiness; a configured-but-unhealthy
      provider does).
    - `connected` reflects the channel layer specifically: `pass` ->
      True, `not_applicable` (no channel configured) -> True (nothing to
      be disconnected from), `fail` -> False. It does NOT roll into
      `ready` - a deployment can be "ready" (host/runtime/gateway/provider
      all healthy) while its messaging channel is disconnected, and
      callers should surface both facts distinctly rather than collapsing
      them into one boolean (issue #79's explicit requirement to
      distinguish these signals).
    """
    # STATUS_UNKNOWN (issue #292) blocks exactly like STATUS_FAIL: absent
    # or ambiguous evidence must never read as ready.
    blocking = (STATUS_FAIL, STATUS_UNKNOWN)
    ready = True
    for name in ("host", "runtime", "gateway"):
        layer = layers.get(name, {})
        if layer.get("status") in blocking:
            ready = False

    provider = layers.get("provider", {})
    if provider.get("status") in blocking:
        ready = False

    channel = layers.get("channel", {})
    connected = channel.get("status") not in blocking

    return ready, connected


def build_result(layers: dict) -> dict:
    ready, connected = aggregate(layers)
    return {"layers": layers, "ready": ready, "connected": connected}
