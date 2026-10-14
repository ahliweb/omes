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
- The default - and in phase 1 the ONLY - probe is a single metadata
  `GET <base>/v1/models`: no prompt, no tool call, no credential. An
  active synthetic chat/tool probe is Not implemented yet (tracked in
  #292); the `active_probe` layer says so explicitly. A passing result
  therefore proves "reachable, policy-compliant, TLS-verified endpoint
  that advertises the configured model id", NOT that the model can
  generate, call tools or is correct.
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
    "active_probe": "No prompt, chat or tool request is ever sent by this command. An opt-in synthetic capability probe is Not implemented yet (tracked in #292).",
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
    return AUTHORITY_OMES_HOST if name in ("config", "policy", "freshness", "active_probe") else AUTHORITY_EXTERNAL_PROVIDER


_SOURCE = {
    "config": "omes-endpoint-config",
    "policy": "omes-endpoint-policy",
    "network": "tcp-connect",
    "tls": "tls-handshake",
    "api": "http-get:/v1/models",
    "model": "http-get:/v1/models",
    "freshness": "http-date-header",
    "active_probe": "omes-endpoint-config",
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
    statuses = [layer["status"] for name, layer in layers.items() if name != "active_probe"]
    if STATUS_FAIL in statuses:
        return STATUS_FAIL
    if STATUS_UNKNOWN in statuses:
        return STATUS_UNKNOWN
    if all(layers[name]["status"] == STATUS_PASS for name in _REQUIRED_PASS):
        return STATUS_PASS
    return STATUS_UNKNOWN  # defensive: a required layer is neither pass nor failing


def _finish(layers: dict, display: Optional[str], scope: str, timeout: float, checked_at: str) -> dict:
    status = _overall(layers)
    expires = _parse_iso(checked_at)
    return {
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
        "layers": {name: layers[name] for name in LAYER_ORDER},
        "limits": {
            "allowed_probes": [f"{method} {path}" for method, path in endpoint_guard.ALLOWED_PROBES],
            "scope": scope,
            "timeout_seconds": timeout,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "redirects_followed": False,
            "prompt_sent": False,
            "credentials_sent": False,
        },
    }


def probe(
    endpoint: Optional[str],
    model: Optional[str] = None,
    *,
    scope: str = endpoint_guard.SCOPE_PRIVATE,
    ca_file: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    resolver: Optional[endpoint_guard.Resolver] = None,
    now: Clock = _utc_now,
) -> dict:
    """Runs the read-only evidence probe and returns the result object.
    Never raises for an operator-input problem; every outcome is a layer
    status. `resolver` and `now` are injection points for tests."""
    checked_at = _iso(now())
    timeout = min(max(timeout, MIN_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS)
    display = endpoint_guard.redact_endpoint(endpoint) if endpoint else None
    layers: dict = {}

    def finish(skip_reason: str) -> dict:
        for name in LAYER_ORDER:
            layers.setdefault(name, _skip(name, skip_reason))
        layers["active_probe"] = _layer("active_probe", STATUS_NOT_APPLICABLE, "active_probe_not_implemented", None, "Not implemented yet (tracked in #292)")
        return _finish(layers, display, scope if scope in endpoint_guard.SCOPES else "invalid", timeout, checked_at)

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
    args = parser.parse_args(argv)

    return probe(
        args.endpoint if args.endpoint is not None else environ.get("OMES_INFERENCE_ENDPOINT", ""),
        args.model if args.model is not None else environ.get("OMES_INFERENCE_MODEL", ""),
        scope=args.scope or environ.get("OMES_INFERENCE_ENDPOINT_SCOPE", "") or endpoint_guard.SCOPE_PRIVATE,
        ca_file=args.ca_file if args.ca_file is not None else environ.get("OMES_INFERENCE_CA_FILE", ""),
        timeout=_timeout_from_env(environ.get("OMES_HEALTH_TIMEOUT")),
        resolver=resolver,
        now=now,
    )


def main(argv: Optional[list] = None) -> int:
    result = run(argv)
    print(json.dumps(result))
    return EXIT_READY if result["ready"] else EXIT_NOT_READY


if __name__ == "__main__":
    sys.exit(main())
