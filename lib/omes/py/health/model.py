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


def layer_result(
    status: str,
    signals: Optional[dict] = None,
    proves: str = "",
    remediation: Optional[str] = None,
    detail: str = "",
) -> dict:
    """Builds one layer's result object. `signals` maps a subset of
    SIGNAL_NAMES to True/False/None (None = not evaluated for this
    layer). `proves` is a short human sentence describing exactly what a
    "pass" on this layer establishes - and, just as importantly, what it
    does NOT establish (docs/hermes-integration.md's "green signals can
    lie" principle, issue #79's core requirement).
    """
    return {
        "status": status,
        "signals": signals or {},
        "proves": proves,
        "remediation": remediation,
        "detail": detail,
    }


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
    ready = True
    for name in ("host", "runtime", "gateway"):
        layer = layers.get(name, {})
        if layer.get("status") == STATUS_FAIL:
            ready = False

    provider = layers.get("provider", {})
    if provider.get("status") == STATUS_FAIL:
        ready = False

    channel = layers.get("channel", {})
    connected = channel.get("status") != STATUS_FAIL

    return ready, connected


def build_result(layers: dict) -> dict:
    ready, connected = aggregate(layers)
    return {"layers": layers, "ready": ready, "connected": connected}
