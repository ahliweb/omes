#!/usr/bin/env python3
"""lib/omes/py/health/inference_endpoint.py - `omes health inference-endpoint`:
provider-neutral, READ-ONLY evidence about an already-configured local or
private OpenAI-compatible inference endpoint (issue #292, ADR-0029,
docs/ai-inference-serving-architecture.md section 3).

Standard library only (ADR-0012). What this is and is not:

- It is preflight evidence and policy. It complements, and does not
  replace, `omes health ollama` (#71, lib/omes/py/health/ollama.py) and
  Hermes provider readiness (#79/#178, hermes.py). It never configures,
  selects or routes a model provider (Hermes owns that; ADR-0017/0029) and
  it never reads Hermes configuration, Hermes credentials or any Hermes
  private store: the endpoint is an explicit, non-secret operator
  reference (--endpoint / OMES_INFERENCE_ENDPOINT).
- The default probe is a single metadata `GET <base>/v1/models`: no
  prompt, no tool call, no credential. A passing result therefore proves
  "reachable, policy-compliant, TLS-verified endpoint that advertises the
  configured model id", NOT that the model can generate, call tools or is
  correct.
- Issue #354: an OPT-IN active synthetic probe, enabled ONLY by the
  explicit `--active-probe` flag (no environment variable can enable it).
  After every other required layer has passed it sends ONE fixed,
  non-sensitive chat-completion request (`POST <base>/v1/chat/completions`,
  fixed prompt, `max_tokens` 8, no stream, no tools) through the same
  vetted address, pinned connection, no-redirect, TLS-verified, bounded
  deadline/size path as the GET. No credential is sent (this command has
  no `secret_reference` support). The prompt and completion are never
  logged, persisted or echoed: only a bounded character count and an
  allowlisted finish reason are recorded. A pass proves ONLY that this
  fixture produced a well-formed non-empty completion from that model at
  that moment (model-dependent); HTTP 200 alone proves nothing. A timeout,
  401/403, malformed response or empty completion is never a pass. When
  the probe ran, `ready` additionally requires it to pass.
- Issue #354, two further opt-in layers that are only valid together with
  `--active-probe`: `--probe-tool-call` (one fixed request declaring one
  fixed harmless tool; pass only on a well-formed call of that tool) and
  `--probe-stream` (one fixed `stream: true` request; pass only on
  well-formed SSE `data:` chunks that end with `[DONE]`). Each is a
  separate layer (`active_probe_tool_call`, `active_probe_stream`) that
  exists in the output only when requested, so the default output shape is
  unchanged. They reuse every safety property above, run only after the
  plain active probe passed, and never print the tool arguments, the
  prompt or the streamed text. A pass proves only that fixture.
- Result shape: the existing layered model of lib/omes/py/health/model.py
  (status, signals, proves, remediation, detail, authority, source), with
  the additive `unknown` status and optional `reason`/`checked_at`
  fields. Statuses are pass|fail|not_applicable|unknown. Nothing is ever
  reported ready on a timeout, invalid response, TLS failure, missing
  model, 401/403, redirect, unsupported API or absent evidence.
- Destination policy, SSRF/DNS-rebinding defence and the bounded client
  live in endpoint_guard.py and pinned_http.py.

Usage:
  python3 inference_endpoint.py [--json] [--endpoint <url>] [--model <id>]
                                [--scope loopback|private] [--ca-file <path>]
                                [--active-probe [--probe-tool-call]
                                [--probe-stream]]

Exit codes: 0 status is pass (ready); 7 anything else (fail, unknown or
not_applicable). The JSON `status` distinguishes them.
"""

from __future__ import annotations

import argparse
import copy
import datetime
import email.utils
import json
import math
import os
import ssl
import sys
from typing import Any, Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import endpoint_guard  # noqa: E402
import pinned_http  # noqa: E402
from model import (  # noqa: E402
    AUTHORITY_EXTERNAL_PROVIDER,
    AUTHORITY_OMES_HOST,
    STATUS_FAIL,
    STATUS_NOT_APPLICABLE,
    STATUS_PASS,
    STATUS_UNKNOWN,
    layer_result,
)

EXIT_READY = 0
EXIT_NOT_READY = 7

EVIDENCE_VERSION = 1

DEFAULT_TIMEOUT_SECONDS = 10.0
MIN_TIMEOUT_SECONDS = 0.1
MAX_TIMEOUT_SECONDS = 30.0
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_MODELS = 10000
#: Opt-in active probe (issue #354). The method/path is fixed; the body is
#: built only from the constants below plus the validated model id.
ACTIVE_PROBE = ("POST", "/v1/chat/completions")
ACTIVE_PROBE_PROMPT = "Reply with the single word: ok"
ACTIVE_PROBE_MAX_TOKENS = 8
ACTIVE_PROBE_MAX_RESPONSE_BYTES = 64 * 1024
_FINISH_REASONS = ("stop", "length", "tool_calls", "content_filter")
#: Optional capability probes (issue #354). Fixed constants only; the tool
#: schema and prompts carry no user data. Names/args are harmless: the tool
#: takes one constant enum value and nothing is ever executed.
TOOL_PROBE_NAME = "omes_probe_echo"
TOOL_PROBE_PROMPT = 'Call the function omes_probe_echo with the argument value set to "ok". Do not answer with text.'
TOOL_PROBE_ARG_VALUE = "ok"
TOOL_PROBE_MAX_TOKENS = 64
TOOL_PROBE_MAX_CALLS = 8
TOOL_PROBE_MAX_ARGUMENT_CHARS = 1024
TOOL_PROBE_MAX_ID_CHARS = 256
STREAM_PROBE_PROMPT = "Reply with the single word: ok"
STREAM_PROBE_MAX_TOKENS = 8
STREAM_PROBE_MAX_EVENTS = 256
STREAM_PROBE_DONE = "[DONE]"
EXTRA_LAYERS = ("active_probe_tool_call", "active_probe_stream")
#: How long an evidence record may be trusted (see revalidate()).
MAX_AGE_SECONDS = 300
#: Largest tolerated difference between the endpoint's HTTP Date and
#: this host's clock before the evidence timing is considered unreliable.
MAX_CLOCK_SKEW_SECONDS = 300

LAYER_ORDER = ("config", "policy", "network", "tls", "api", "model", "freshness", "active_probe")
_REQUIRED_PASS = ("config", "policy", "network", "api", "model")

_PROVES = {
    "config": "Proves an endpoint reference was supplied and is a well-formed http(s) base URL with no embedded credentials, query or fragment. Does NOT prove the endpoint exists or is trustworthy.",
    "policy": "Proves the host name was resolved once and every resolved address is inside the allowed destination scope (loopback, or loopback plus RFC 1918/ULA private). Does NOT prove the destination is the intended server.",
    "network": "Proves a TCP connection to a vetted address succeeded within the deadline. Does NOT prove a model server is listening.",
    "tls": "Proves (https only) a TLS 1.2+ session was established with certificate chain and host name verification enabled. Does NOT prove the server is the intended operator's.",
    "api": "Proves GET /v1/models returned HTTP 200 and a well-formed OpenAI-style model list within the size and time bounds. Does NOT prove any model can generate.",
    "model": "Proves the configured model id appears in the endpoint's advertised model list. Does NOT prove the model is loaded, can generate, supports tools or is the intended weights.",
    "freshness": "Proves the endpoint's HTTP Date is within the tolerated skew of this host's clock, so the observation time is trustworthy. Absent when the server sends no Date header.",
    "active_probe_tool_call": "Only when --active-probe --probe-tool-call is passed: proves that ONE fixed synthetic chat request declaring ONE fixed harmless tool (constant name and schema, no user data, no credentials, max_tokens 64) returned HTTP 200 and a well-formed tool call of exactly that tool with schema-valid constant arguments at that moment. Model-dependent: it proves nothing about other tools, argument quality, parallel calls, multi-turn tool use or correctness, and HTTP 200 alone proves nothing. Nothing is executed. Without the flag no tool request is sent.",
    "active_probe_stream": "Only when --active-probe --probe-stream is passed: proves that ONE fixed synthetic chat request with stream true (constant message, max_tokens 8, no tools, no credentials) returned HTTP 200, a text/event-stream content type and well-formed SSE data chunks with non-empty content that end with the [DONE] terminator within the size, event and time bounds. Model-dependent: it proves nothing about other prompts, sustained or long streams, backpressure, quality or correctness. Without the flag no streaming request is sent.",
    "active_probe": "Only when --active-probe is passed: proves that ONE fixed, non-sensitive synthetic chat request (max_tokens 8, no tools, no credentials) returned HTTP 200 and a well-formed non-empty completion from the configured model at that moment. Model-dependent: it proves nothing about other prompts, tool calling, quality or correctness, and HTTP 200 alone proves nothing. Without the flag no prompt, chat or tool request is sent.",
}

# Stable reason -> remediation. Fixed strings only: nothing derived from
# the endpoint, response or exception text is ever interpolated.
_REMEDIATION = {
    "endpoint_not_configured": "set OMES_INFERENCE_ENDPOINT (or pass --endpoint) to the base URL of the private endpoint, e.g. http://127.0.0.1:8000",
    "invalid_url": "provide a plain http(s)://host[:port][/prefix] URL",
    "scheme_not_allowed": "use an http:// or https:// URL; include the scheme explicitly",
    "userinfo_not_allowed": "remove credentials from the URL; OMES never sends or stores endpoint credentials",
    "query_not_allowed": "provide the base URL only, without a query string or fragment",
    "invalid_port": "use a TCP port between 1 and 65535",
    "invalid_host": "use a canonical DNS name or IP address",
    "non_canonical_ip_host": "write the IP address in canonical dotted-quad or IPv6 form",
    "invalid_path": "use a plain path prefix with letters, digits and . _ ~ - only",
    "invalid_model_id": "use the model id exactly as listed by the endpoint (letters, digits and . _ : / @ + -)",
    "invalid_scope": "use scope 'loopback' or 'private'",
    "ca_file_invalid": "point OMES_INFERENCE_CA_FILE / --ca-file at a readable PEM CA bundle",
    "public_address": "the endpoint resolves to a public address; only loopback/private endpoints are probed (cloud endpoints are governed by Hermes provider routing and the AI egress policy)",
    "metadata_address": "the endpoint resolves to a cloud instance-metadata address; this is never probed",
    "link_local_address": "link-local addresses are never probed",
    "multicast_address": "multicast addresses are never probed",
    "unspecified_address": "0.0.0.0/:: is not a valid destination",
    "reserved_address": "shared, reserved and embedded-IPv4 address ranges are not probed; use a loopback or RFC 1918/ULA address",
    "unparseable_address": "the resolver returned an unparseable address",
    "private_address_outside_scope": "the endpoint resolves to a private address but the scope is 'loopback'; widen the scope to 'private' if that is intended",
    "dns_resolution_failed": "verify the endpoint host name resolves from this host",
    "dns_timeout": "DNS resolution exceeded the deadline; verify the resolver or use an IP address",
    "connection_refused": "nothing is listening on that address and port; start the inference server or fix the port",
    "connect_failed": "verify the address, port and local firewall",
    "peer_mismatch": "the connected peer did not match the vetted address; investigate before retrying",
    "timeout": "the endpoint did not answer within the deadline; check server load/start-up and OMES_HEALTH_TIMEOUT",
    "tls_untrusted_certificate": "the certificate chain is not trusted; supply the operator CA via OMES_INFERENCE_CA_FILE (verification is never disabled)",
    "tls_hostname_mismatch": "the certificate does not match the endpoint host name; use the name the certificate was issued for",
    "tls_certificate_expired": "renew the endpoint's TLS certificate",
    "tls_handshake_failed": "the TLS handshake failed; check the scheme (http vs https), port and TLS configuration",
    "tls_error": "the TLS session failed; check the endpoint's TLS configuration",
    "auth_required": "the endpoint requires authentication; OMES sends no credentials, so its model list cannot be observed here (use Hermes provider readiness for authenticated endpoints)",
    "unsupported_api": "the endpoint does not serve GET /v1/models; it is not an OpenAI-compatible model-list API at this base path",
    "redirect_not_followed": "the endpoint answered with a redirect, which is never followed; point at the final base URL",
    "http_status": "the endpoint returned an unexpected HTTP status; check the server logs",
    "server_error": "the endpoint returned a server error; check the server logs",
    "unexpected_status": "the endpoint returned an unexpected success status",
    "response_too_large": "the model list exceeded the response size cap and was not parsed",
    "malformed_json": "the endpoint returned a body that is not valid JSON",
    "invalid_response": "the endpoint returned a response that is not a valid OpenAI-style model list",
    "connection_reset": "the endpoint closed the connection mid-response",
    "model_missing": "the configured model id is not advertised by the endpoint; load/serve it or correct the id",
    "model_not_configured": "set OMES_INFERENCE_MODEL (or pass --model) so model availability can be proven",
    "clock_skew_exceeded": "synchronise this host's and the endpoint's clocks (NTP)",
    "server_date_invalid": "the endpoint sent an unparseable Date header",
    "empty_completion": "the model returned no completion text for the fixed probe; check that the model is loaded and can generate (model-dependent)",
    "active_probe_invalid_response": "the endpoint answered the probe with a response that is not a valid OpenAI-style chat completion",
    "tool_call_missing": "the model did not return a tool call for the fixed tool probe; the model or serving configuration may not support tool calling (model-dependent; tool-call parsing may need enabling on the server)",
    "tool_call_wrong_name": "the model called a tool other than the one fixed tool declared by the probe",
    "tool_call_invalid": "the tool call was not well formed (shape, id, type or arguments did not match the fixed schema)",
    "stream_not_sse": "the endpoint did not answer the streaming probe with a text/event-stream response; streaming may not be supported or enabled",
    "stream_malformed": "the streaming response was not well-formed OpenAI-style SSE data chunks",
    "stream_empty": "the streaming response carried no data chunks",
    "stream_truncated": "the stream did not end with the [DONE] terminator",
    "stream_too_long": "the stream exceeded the event bound for the fixed probe and was not accepted",
    "plaintext_http_non_loopback": "traffic to this private address is unencrypted; serve the endpoint over https and supply its CA via OMES_INFERENCE_CA_FILE",
}

Clock = Callable[[], "datetime.datetime"]


def _utc_now() -> "datetime.datetime":
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(moment: "datetime.datetime") -> str:
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: Any) -> Optional["datetime.datetime"]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return parsed.replace(tzinfo=datetime.timezone.utc)


def _timeout_from_env(raw: Optional[str]) -> float:
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_TIMEOUT_SECONDS
    return min(max(value, MIN_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS)


def _authority(name: str) -> str:
    # OMES owns evaluating its own policy and clock; the serving operator
    # (not OMES, not Hermes) is authoritative for the endpoint's own state.
    return AUTHORITY_OMES_HOST if name in ("config", "policy", "freshness", "active_probe", *EXTRA_LAYERS) else AUTHORITY_EXTERNAL_PROVIDER


_SOURCE = {
    "config": "omes-endpoint-config",
    "policy": "omes-endpoint-policy",
    "network": "tcp-connect",
    "tls": "tls-handshake",
    "api": "http-get:/v1/models",
    "model": "http-get:/v1/models",
    "freshness": "http-date-header",
    "active_probe": "omes-endpoint-config",
    "active_probe_tool_call": "omes-endpoint-config",
    "active_probe_stream": "omes-endpoint-config",
}


def _layer(
    name: str,
    status: str,
    reason: Optional[str],
    checked_at: Optional[str],
    detail: str = "",
    signals: Optional[dict] = None,
    advise: bool = True,
) -> dict:
    remediation = None
    if advise and (status in (STATUS_FAIL, STATUS_UNKNOWN) or reason in ("endpoint_not_configured", "plaintext_http_non_loopback")):
        remediation = _REMEDIATION.get(reason or "")
    return layer_result(
        status,
        signals=signals,
        proves=_PROVES[name],
        remediation=remediation,
        detail=detail,
        authority=_authority(name),
        source=_SOURCE[name],
        reason=reason,
        checked_at=checked_at,
    )


def _skip(name: str, reason: str) -> dict:
    return _layer(name, STATUS_NOT_APPLICABLE, reason, None, detail="not evaluated", advise=False)


def _interpret(resp: "pinned_http.Response") -> tuple:
    """Maps an HTTP response to (status, reason, detail, model_ids|None)."""
    code = resp.status
    if code == 200:
        encoding = (resp.content_encoding or "identity").strip().lower()
        if encoding not in ("", "identity"):
            return STATUS_FAIL, "invalid_response", "HTTP 200 with a content encoding", None
        try:
            doc = json.loads(resp.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return STATUS_FAIL, "malformed_json", "HTTP 200 but the body is not valid JSON", None
        data = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(data, list) or len(data) > MAX_MODELS:
            return STATUS_FAIL, "invalid_response", "HTTP 200 but no valid model list", None
        ids = set()
        for entry in data:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                return STATUS_FAIL, "invalid_response", "HTTP 200 but a model entry is malformed", None
            ids.add(entry["id"])
        return STATUS_PASS, None, f"HTTP 200, {len(ids)} model id(s) advertised", ids
    if 300 <= code < 400:
        return STATUS_FAIL, "redirect_not_followed", f"HTTP {code}; redirects are never followed", None
    if code in (401, 403):
        return STATUS_UNKNOWN, "auth_required", f"HTTP {code}; OMES sends no credentials", None
    if code in (404, 405, 410, 501):
        return STATUS_FAIL, "unsupported_api", f"HTTP {code}", None
    if 500 <= code < 600:
        return STATUS_FAIL, "server_error", f"HTTP {code}", None
    if 200 <= code < 300:
        return STATUS_FAIL, "unexpected_status", f"HTTP {code}", None
    return STATUS_FAIL, "http_status", f"HTTP {code}", None


def _freshness_layer(resp: "pinned_http.Response", now: Clock, checked_at: str) -> dict:
    header = resp.date_header
    if not header:
        return _layer("freshness", STATUS_NOT_APPLICABLE, "server_date_unavailable", checked_at, "the endpoint sent no Date header")
    try:
        server = email.utils.parsedate_to_datetime(header)
    except (TypeError, ValueError, IndexError):
        return _layer("freshness", STATUS_UNKNOWN, "server_date_invalid", checked_at, "unparseable Date header")
    if server.tzinfo is None:
        server = server.replace(tzinfo=datetime.timezone.utc)
    skew = abs((server - now()).total_seconds())
    if skew > MAX_CLOCK_SKEW_SECONDS:
        return _layer("freshness", STATUS_UNKNOWN, "clock_skew_exceeded", checked_at, f"clock skew {int(skew)}s exceeds {MAX_CLOCK_SKEW_SECONDS}s")
    return _layer("freshness", STATUS_PASS, None, checked_at, f"clock skew {int(skew)}s within {MAX_CLOCK_SKEW_SECONDS}s")


def _overall(layers: dict) -> str:
    if layers["config"]["status"] == STATUS_NOT_APPLICABLE:
        return STATUS_NOT_APPLICABLE
    # The active probe only participates when it was requested (issue
    # #354): without --active-probe it is not_applicable and never blocks.
    statuses = [layer["status"] for layer in layers.values()]
    if STATUS_FAIL in statuses:
        return STATUS_FAIL
    if STATUS_UNKNOWN in statuses:
        return STATUS_UNKNOWN
    requested = layers["active_probe"].get("reason") != "active_probe_not_run"
    extras = tuple(name for name in EXTRA_LAYERS if name in layers)
    required = _REQUIRED_PASS + (("active_probe",) if requested else ()) + extras
    if all(layers[name]["status"] == STATUS_PASS for name in required):
        return STATUS_PASS
    return STATUS_UNKNOWN  # defensive: a required layer is neither pass nor failing


def _finish(layers: dict, display: Optional[str], scope: str, timeout: float, checked_at: str, active_probe: bool = False, prompt_sent: bool = False) -> dict:
    status = _overall(layers)
    order = LAYER_ORDER + tuple(name for name in EXTRA_LAYERS if name in layers)
    expires = _parse_iso(checked_at)
    result = {
        "target": "inference-endpoint",
        "evidence_version": EVIDENCE_VERSION,
        "endpoint": display,
        "status": status,
        "ready": status == STATUS_PASS,
        "checked_at": checked_at,
        "freshness": {
            "state": "fresh",
            "checked_at": checked_at,
            "max_age_seconds": MAX_AGE_SECONDS,
            "expires_at": _iso(expires + datetime.timedelta(seconds=MAX_AGE_SECONDS)) if expires else None,
        },
        "layers": {name: layers[name] for name in order},
        "limits": {
            "allowed_probes": [f"{method} {path}" for method, path in endpoint_guard.ALLOWED_PROBES],
            "scope": scope,
            "timeout_seconds": timeout,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "redirects_followed": False,
            "prompt_sent": prompt_sent,
            "credentials_sent": False,
        },
    }
    if active_probe:
        # Added only on opt-in so the default output shape is unchanged.
        result["limits"]["allowed_probes"].append(" ".join(ACTIVE_PROBE))
        result["limits"]["active_probe"] = {
            "requested": True,
            "max_tokens": ACTIVE_PROBE_MAX_TOKENS,
            "max_response_bytes": ACTIVE_PROBE_MAX_RESPONSE_BYTES,
        }
        if "active_probe_tool_call" in layers:
            result["limits"]["active_probe"]["tool_call"] = {"requested": True, "tool": TOOL_PROBE_NAME, "max_tokens": TOOL_PROBE_MAX_TOKENS}
        if "active_probe_stream" in layers:
            result["limits"]["active_probe"]["stream"] = {"requested": True, "max_tokens": STREAM_PROBE_MAX_TOKENS, "max_events": STREAM_PROBE_MAX_EVENTS}
    return result


def _active_body(model_id: str) -> bytes:
    """The fixed synthetic request. Contains no user data: only constants
    and the already-validated, non-secret model id."""
    return json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": ACTIVE_PROBE_PROMPT}],
            "max_tokens": ACTIVE_PROBE_MAX_TOKENS,
            "temperature": 0,
            "stream": False,
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _interpret_completion(resp: "pinned_http.Response") -> tuple:
    """Maps the probe response to (status, reason, detail, signals). Never
    returns the prompt or completion text: only a character count and an
    allowlisted finish reason leave this function."""
    if resp.status != 200:
        status, reason, detail, _ = _interpret(resp)
        if status == STATUS_PASS:  # defensive: only HTTP 200 can pass
            status, reason = STATUS_FAIL, "unexpected_status"
        return status, reason, detail, {}
    encoding = (resp.content_encoding or "identity").strip().lower()
    if encoding not in ("", "identity"):
        return STATUS_FAIL, "active_probe_invalid_response", "HTTP 200 with a content encoding", {}
    try:
        doc = json.loads(resp.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return STATUS_FAIL, "malformed_json", "HTTP 200 but the body is not valid JSON", {}
    choices = doc.get("choices") if isinstance(doc, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        return STATUS_FAIL, "active_probe_invalid_response", "HTTP 200 but no valid chat completion", {}
    if not content.strip():
        return STATUS_FAIL, "empty_completion", "HTTP 200 but the completion is empty", {"completion_chars": 0}
    finish = first.get("finish_reason")
    finish = finish if finish in _FINISH_REASONS else "other"
    chars = min(len(content), 1_000_000)
    return STATUS_PASS, None, f"HTTP 200, well-formed completion ({chars} char(s), finish_reason={finish}); proves this fixture only", {"completion_chars": chars, "finish_reason": finish}


def _tool_body(model_id: str) -> bytes:
    """Fixed tool-call fixture: one constant harmless tool, no user data."""
    return json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": TOOL_PROBE_PROMPT}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": TOOL_PROBE_NAME,
                        "description": "Fixed OMES health probe function. Echoes one constant value; nothing is executed.",
                        "parameters": {
                            "type": "object",
                            "properties": {"value": {"type": "string", "enum": [TOOL_PROBE_ARG_VALUE]}},
                            "required": ["value"],
                            "additionalProperties": False,
                        },
                    },
                }
            ],
            "tool_choice": "auto",
            "max_tokens": TOOL_PROBE_MAX_TOKENS,
            "temperature": 0,
            "stream": False,
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _stream_body(model_id: str) -> bytes:
    """Fixed streaming fixture: constant message, stream true, no tools."""
    return json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": STREAM_PROBE_PROMPT}],
            "max_tokens": STREAM_PROBE_MAX_TOKENS,
            "temperature": 0,
            "stream": True,
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _non_200(resp: "pinned_http.Response") -> tuple:
    status, reason, detail, _ = _interpret(resp)
    if status == STATUS_PASS:  # defensive: only HTTP 200 can pass
        status, reason = STATUS_FAIL, "unexpected_status"
    return status, reason, detail, {}


def _identity_encoding(resp: "pinned_http.Response") -> bool:
    return (resp.content_encoding or "identity").strip().lower() in ("", "identity")


def _interpret_tool_call(resp: "pinned_http.Response") -> tuple:
    """Maps the tool probe response to (status, reason, detail, signals).
    The tool arguments and any text never leave this function: only a call
    count and an allowlisted finish reason are returned."""
    if resp.status != 200:
        return _non_200(resp)
    if not _identity_encoding(resp):
        return STATUS_FAIL, "active_probe_invalid_response", "HTTP 200 with a content encoding", {}
    try:
        doc = json.loads(resp.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return STATUS_FAIL, "malformed_json", "HTTP 200 but the body is not valid JSON", {}
    choices = doc.get("choices") if isinstance(doc, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        return STATUS_FAIL, "active_probe_invalid_response", "HTTP 200 but no valid chat completion", {}
    calls = message.get("tool_calls")
    if calls is None or calls == []:
        return STATUS_FAIL, "tool_call_missing", "HTTP 200 but the response contains no tool call", {"tool_calls": 0}
    if not isinstance(calls, list) or len(calls) > TOOL_PROBE_MAX_CALLS:
        return STATUS_FAIL, "tool_call_invalid", "HTTP 200 but tool_calls is not a bounded list", {}
    for call in calls:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            return STATUS_FAIL, "tool_call_invalid", "a tool call is malformed", {}
        if function["name"] != TOOL_PROBE_NAME:
            return STATUS_FAIL, "tool_call_wrong_name", "the model called a tool other than the fixed probe tool", {}
        call_id = call.get("id")
        arguments = function.get("arguments")
        if call.get("type") != "function" or not isinstance(call_id, str) or not call_id or len(call_id) > TOOL_PROBE_MAX_ID_CHARS:
            return STATUS_FAIL, "tool_call_invalid", "a tool call has no valid id or type", {}
        if not isinstance(arguments, str) or len(arguments) > TOOL_PROBE_MAX_ARGUMENT_CHARS:
            return STATUS_FAIL, "tool_call_invalid", "tool call arguments are not a bounded JSON string", {}
        try:
            parsed = json.loads(arguments)
        except (ValueError, RecursionError):
            return STATUS_FAIL, "tool_call_invalid", "tool call arguments are not valid JSON", {}
        if not isinstance(parsed, dict) or set(parsed) != {"value"} or parsed["value"] != TOOL_PROBE_ARG_VALUE:
            return STATUS_FAIL, "tool_call_invalid", "tool call arguments do not match the fixed schema", {}
    finish = first.get("finish_reason")
    finish = finish if finish in _FINISH_REASONS else "other"
    return STATUS_PASS, None, f"HTTP 200, {len(calls)} well-formed call(s) of the fixed tool (finish_reason={finish}); proves this fixture only", {"tool_calls": len(calls), "finish_reason": finish}


def _interpret_stream(resp: "pinned_http.Response") -> tuple:
    """Maps the streaming probe response to (status, reason, detail,
    signals). Streamed text is only counted, never returned."""
    if resp.status != 200:
        return _non_200(resp)
    if not _identity_encoding(resp):
        return STATUS_FAIL, "stream_malformed", "HTTP 200 with a content encoding", {}
    media = (resp.content_type or "").split(";", 1)[0].strip().lower()
    if media != "text/event-stream":
        return STATUS_FAIL, "stream_not_sse", "HTTP 200 but the response is not text/event-stream", {}
    try:
        text = resp.body.decode("utf-8")
    except UnicodeDecodeError:
        return STATUS_FAIL, "stream_malformed", "the stream is not valid UTF-8", {}
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = text.split("\n\n")
    tail = parts.pop()  # "" when the last event is properly terminated
    chunks = 0
    chars = 0
    done = False
    finish = None
    events = 0
    for event in parts + ([tail] if tail else []):
        data_lines = []
        for line in event.split("\n"):
            if line.startswith(":") or not line:
                continue
            if not line.startswith("data:"):
                return STATUS_FAIL, "stream_malformed", "the stream has a non-data field", {}
            value = line[5:]
            data_lines.append(value[1:] if value.startswith(" ") else value)
        if not data_lines:
            continue  # comment-only keep-alive event
        events += 1
        if events > STREAM_PROBE_MAX_EVENTS:
            return STATUS_FAIL, "stream_too_long", "the stream exceeded the event bound", {}
        if len(data_lines) != 1 or done:
            return STATUS_FAIL, "stream_malformed", "unexpected data after the terminator or a multi-line event", {}
        payload = data_lines[0]
        if payload == STREAM_PROBE_DONE:
            done = True
            continue
        try:
            doc = json.loads(payload)
        except (ValueError, RecursionError):
            return STATUS_FAIL, "stream_malformed", "a stream chunk is not valid JSON", {}
        choices = doc.get("choices") if isinstance(doc, dict) else None
        first = choices[0] if isinstance(choices, list) and choices else None
        delta = first.get("delta") if isinstance(first, dict) else None
        if not isinstance(delta, dict):
            return STATUS_FAIL, "stream_malformed", "a stream chunk is not an OpenAI-style delta", {}
        content = delta.get("content")
        if content is not None and not isinstance(content, str):
            return STATUS_FAIL, "stream_malformed", "a stream chunk has non-text content", {}
        chunks += 1
        chars = min(chars + len(content or ""), 1_000_000)
        if first.get("finish_reason") in _FINISH_REASONS:
            finish = first["finish_reason"]
    if chunks == 0:
        return STATUS_FAIL, "stream_empty", "HTTP 200 but no data chunks", {"stream_chunks": 0}
    if tail or not done:
        return STATUS_FAIL, "stream_truncated", "the stream did not end with [DONE]", {"stream_chunks": chunks}
    if chars == 0:
        return STATUS_FAIL, "empty_completion", "HTTP 200 stream but the streamed completion is empty", {"stream_chunks": chunks, "completion_chars": 0}
    return STATUS_PASS, None, f"HTTP 200, {chunks} well-formed SSE chunk(s) ending with [DONE] ({chars} char(s)); proves this fixture only", {"stream_chunks": chunks, "completion_chars": chars, "finish_reason": finish or "other"}


def _run_post(name, body, interpret, accept, target, addresses, timeout, ssl_context, checked_at) -> dict:
    path = target.probe_path[: -len("/models")] + "/chat/completions"
    try:
        resp = pinned_http.request(
            method="POST",
            body=body,
            scheme=target.scheme,
            host=target.host,
            host_header=target.host_header,
            port=target.port,
            path=path,
            addresses=addresses,
            timeout=timeout,
            max_bytes=ACTIVE_PROBE_MAX_RESPONSE_BYTES,
            ssl_context=ssl_context,
            accept=accept,
        )
    except pinned_http.ProbeError as exc:
        # Never pass on any transport/TLS/timeout failure; the reason is a
        # fixed code, never exception text.
        return _layer(name, exc.status, exc.reason, checked_at, "the synthetic probe did not complete")
    status, reason, detail, signals = interpret(resp)
    return _layer(name, status, reason, checked_at, detail, signals=signals)


def _run_active_probe(target, model_id, addresses, timeout, ssl_context, checked_at) -> dict:
    return _run_post("active_probe", _active_body(model_id), _interpret_completion, "application/json", target, addresses, timeout, ssl_context, checked_at)


def probe(
    endpoint: Optional[str],
    model: Optional[str] = None,
    *,
    scope: str = endpoint_guard.SCOPE_PRIVATE,
    ca_file: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    resolver: Optional[endpoint_guard.Resolver] = None,
    now: Clock = _utc_now,
    active_probe: bool = False,
    probe_tool_call: bool = False,
    probe_stream: bool = False,
) -> dict:
    """Runs the read-only evidence probe and returns the result object.
    Never raises for an operator-input problem; every outcome is a layer
    status. `resolver` and `now` are injection points for tests."""
    checked_at = _iso(now())
    timeout = min(max(timeout, MIN_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS)
    display = endpoint_guard.redact_endpoint(endpoint) if endpoint else None
    layers: dict = {}
    sent = {"prompt": False}
    # The capability probes are only ever additions to the active probe.
    extras = {
        "active_probe_tool_call": bool(active_probe and probe_tool_call),
        "active_probe_stream": bool(active_probe and probe_stream),
    }

    def finish(skip_reason: str) -> dict:
        for name in LAYER_ORDER:
            if name != "active_probe":
                layers.setdefault(name, _skip(name, skip_reason))
        if "active_probe" not in layers:
            if active_probe:
                layers["active_probe"] = _layer("active_probe", STATUS_NOT_APPLICABLE, "skipped_prior_failure", None, "requested but not run: an earlier required layer did not pass", advise=False)
            else:
                layers["active_probe"] = _layer("active_probe", STATUS_NOT_APPLICABLE, "active_probe_not_run", None, "opt-in; not requested (pass --active-probe to run)", advise=False)
        for name in EXTRA_LAYERS:
            if extras[name] and name not in layers:
                layers[name] = _layer(name, STATUS_NOT_APPLICABLE, "skipped_prior_failure", None, "requested but not run: an earlier required layer did not pass", advise=False)
        return _finish(layers, display, scope if scope in endpoint_guard.SCOPES else "invalid", timeout, checked_at, active_probe, sent["prompt"])

    # ---- config -------------------------------------------------------
    if not endpoint:
        layers["config"] = _layer("config", STATUS_NOT_APPLICABLE, "endpoint_not_configured", checked_at, "no endpoint configured; nothing is guessed")
        return finish("endpoint_not_configured")

    def config_fail(reason: str) -> dict:
        layers["config"] = _layer("config", STATUS_FAIL, reason, checked_at, "endpoint configuration rejected")
        return finish("skipped_prior_failure")

    try:
        target = endpoint_guard.parse_endpoint(endpoint)
    except endpoint_guard.EndpointError as exc:
        return config_fail(exc.reason)
    if scope not in endpoint_guard.SCOPES:
        return config_fail("invalid_scope")
    model_id: Optional[str] = None
    if model:
        model_id = endpoint_guard.validate_model_id(model)
        if model_id is None:
            return config_fail("invalid_model_id")
    ssl_context = None
    if target.scheme == "https":
        try:
            ssl_context = pinned_http.make_ssl_context(ca_file or None)
        except (ssl.SSLError, OSError):
            return config_fail("ca_file_invalid")
    layers["config"] = _layer("config", STATUS_PASS, None, checked_at, f"endpoint {target.display}", signals={"enabled": True})

    # ---- policy (resolve once, vet every address) ----------------------
    try:
        addresses = endpoint_guard.resolve_and_vet(target, scope, resolver, timeout)
    except endpoint_guard.ResolutionError as exc:
        status = STATUS_UNKNOWN if exc.reason == "dns_timeout" else STATUS_FAIL
        layers["policy"] = _layer("policy", status, exc.reason, checked_at, "destination could not be resolved")
        return finish("skipped_prior_failure")
    except endpoint_guard.EndpointError as exc:
        layers["policy"] = _layer("policy", STATUS_FAIL, exc.reason, checked_at, "destination is outside the allowed scope; no request was sent")
        return finish("skipped_prior_failure")
    classes = {endpoint_guard.classify_ip(a)[1] for a in addresses}
    layers["policy"] = _layer("policy", STATUS_PASS, None, checked_at, f"{len(addresses)} address(es) vetted ({', '.join(sorted(classes))})")

    # ---- network / tls / api (one bounded request) ----------------------
    try:
        resp = pinned_http.get(
            scheme=target.scheme,
            host=target.host,
            host_header=target.host_header,
            port=target.port,
            path=target.probe_path,
            addresses=addresses,
            timeout=timeout,
            max_bytes=MAX_RESPONSE_BYTES,
            ssl_context=ssl_context,
        )
    except pinned_http.ProbeError as exc:
        failed = exc.layer
        layers["network"] = (
            _layer("network", exc.status, exc.reason, checked_at, "TCP connection not established", signals={"reachable": False})
            if failed == pinned_http.LAYER_NETWORK
            else _layer("network", STATUS_PASS, None, checked_at, "TCP connection established", signals={"reachable": True})
        )
        if failed != pinned_http.LAYER_NETWORK:
            if failed == pinned_http.LAYER_TLS:
                layers["tls"] = _layer("tls", exc.status, exc.reason, checked_at, "TLS session not established")
            elif target.scheme == "https":
                layers["tls"] = _layer("tls", STATUS_PASS, None, checked_at, "TLS verified")
            else:
                layers["tls"] = _plaintext_layer(classes, checked_at)
        if failed == pinned_http.LAYER_API:
            layers["api"] = _layer("api", exc.status, exc.reason, checked_at, "no valid response")
        return finish("skipped_prior_failure")

    layers["network"] = _layer("network", STATUS_PASS, None, checked_at, "TCP connection established", signals={"reachable": True})
    if target.scheme == "https":
        layers["tls"] = _layer("tls", STATUS_PASS, None, checked_at, f"{resp.tls_version or 'TLS'} with certificate and host name verified")
    else:
        layers["tls"] = _plaintext_layer(classes, checked_at)

    status, reason, detail, model_ids = _interpret(resp)
    layers["api"] = _layer("api", status, reason, checked_at, detail)
    layers["freshness"] = _freshness_layer(resp, now, checked_at)
    if status != STATUS_PASS or model_ids is None:
        return finish("skipped_prior_failure")

    # ---- model ------------------------------------------------------------
    if model_id is None:
        layers["model"] = _layer("model", STATUS_UNKNOWN, "model_not_configured", checked_at, "no model id configured, so availability cannot be proven")
    elif model_id in model_ids:
        layers["model"] = _layer("model", STATUS_PASS, None, checked_at, f"model '{model_id}' is advertised")
        if active_probe and layers["freshness"]["status"] != STATUS_UNKNOWN:
            # ---- opt-in active synthetic probe (issue #354) ---------------
            sent["prompt"] = True
            layers["active_probe"] = _run_active_probe(target, model_id, addresses, timeout, ssl_context, checked_at)
            # ---- opt-in capability probes: only after the plain probe passed
            if layers["active_probe"]["status"] == STATUS_PASS:
                if extras["active_probe_tool_call"]:
                    layers["active_probe_tool_call"] = _run_post("active_probe_tool_call", _tool_body(model_id), _interpret_tool_call, "application/json", target, addresses, timeout, ssl_context, checked_at)
                if extras["active_probe_stream"]:
                    layers["active_probe_stream"] = _run_post("active_probe_stream", _stream_body(model_id), _interpret_stream, "text/event-stream", target, addresses, timeout, ssl_context, checked_at)
    else:
        layers["model"] = _layer("model", STATUS_FAIL, "model_missing", checked_at, f"model '{model_id}' is not advertised ({len(model_ids)} other id(s))")
    return finish("skipped_prior_failure")


def _plaintext_layer(classes: set, checked_at: str) -> dict:
    loopback_only = classes == {endpoint_guard.CLASS_LOOPBACK}
    reason = "plaintext_http_loopback" if loopback_only else "plaintext_http_non_loopback"
    detail = "plain http over loopback" if loopback_only else "plain http to a non-loopback private address: traffic is not encrypted; prefer https"
    return _layer("tls", STATUS_NOT_APPLICABLE, reason, checked_at, detail)


def revalidate(result: dict, now: Optional[Clock] = None) -> dict:
    """Re-evaluates a previously produced result's freshness at read time
    and returns a copy. Evidence that is older than its max age, dated in
    the future beyond the tolerated skew, or carries an unparseable
    timestamp is downgraded to status `unknown` / ready=false: stale
    evidence is never trusted as current. A result that was not ready is
    returned unchanged (it cannot get better with age)."""
    clock = now or _utc_now
    out = copy.deepcopy(result)
    fresh = out.get("freshness") if isinstance(out.get("freshness"), dict) else {}
    max_age = fresh.get("max_age_seconds", MAX_AGE_SECONDS)
    if not isinstance(max_age, (int, float)) or isinstance(max_age, bool) or max_age <= 0:
        max_age = MAX_AGE_SECONDS
    checked = _parse_iso(out.get("checked_at"))
    state = "fresh"
    if checked is None:
        state = "unparseable_timestamp"
    else:
        age = (clock() - checked).total_seconds()
        if age < -MAX_CLOCK_SKEW_SECONDS:
            state = "future_timestamp"
        elif age > max_age:
            state = "stale"
    out["freshness"] = {**fresh, "state": state}
    if state != "fresh":
        out["status"] = STATUS_UNKNOWN if out.get("status") == STATUS_PASS else out.get("status")
        out["ready"] = False
    return out


def run(
    argv: Optional[list] = None,
    *,
    env: Optional[dict] = None,
    resolver: Optional[endpoint_guard.Resolver] = None,
    now: Clock = _utc_now,
) -> dict:
    environ = os.environ if env is None else env
    parser = argparse.ArgumentParser(prog="inference-endpoint-health", add_help=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--scope", choices=endpoint_guard.SCOPES, default=None)
    parser.add_argument("--ca-file", default=None)
    # Deliberately flag-only: no environment variable can enable the probe.
    parser.add_argument("--active-probe", action="store_true")
    # Issue #354: extra opt-in capability probes, only valid with --active-probe.
    parser.add_argument("--probe-tool-call", action="store_true")
    parser.add_argument("--probe-stream", action="store_true")
    args = parser.parse_args(argv)
    if (args.probe_tool_call or args.probe_stream) and not args.active_probe:
        parser.error("--probe-tool-call and --probe-stream require --active-probe")

    return probe(
        args.endpoint if args.endpoint is not None else environ.get("OMES_INFERENCE_ENDPOINT", ""),
        args.model if args.model is not None else environ.get("OMES_INFERENCE_MODEL", ""),
        scope=args.scope or environ.get("OMES_INFERENCE_ENDPOINT_SCOPE", "") or endpoint_guard.SCOPE_PRIVATE,
        ca_file=args.ca_file if args.ca_file is not None else environ.get("OMES_INFERENCE_CA_FILE", ""),
        timeout=_timeout_from_env(environ.get("OMES_HEALTH_TIMEOUT")),
        resolver=resolver,
        now=now,
        active_probe=args.active_probe,
        probe_tool_call=args.probe_tool_call,
        probe_stream=args.probe_stream,
    )


def main(argv: Optional[list] = None) -> int:
    result = run(argv)
    print(json.dumps(result))
    return EXIT_READY if result["ready"] else EXIT_NOT_READY


if __name__ == "__main__":
    sys.exit(main())
