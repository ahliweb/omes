#!/usr/bin/env python3
"""lib/omes/py/privacy/restricted_posture.py - restricted/local-only
inference deployment posture verification (issue #215, ADR-0029, docs/
ai-data-privacy-and-model-security.md section 10).

This module does NOT reimplement the classification/egress decision
matrix. It derives one bounded, contract-shaped value - the request's
`destination` (`local_only` / `private_endpoint` / a fail-closed mapping
to `cloud_sanitized`) - from Hermes' currently configured model endpoint,
then hands that off to the existing deterministic evaluator in
lib/omes/py/privacy/egress_policy.py (#214) to reach the actual
allow/deny/approval_required decision and stable reason code. The
decision matrix itself (what RESTRICTED + each destination means) lives
in exactly one place: egress_policy.py.

Hard boundaries (do not weaken):

- Metadata-only. The bash caller (modules/hermes-restricted/module.sh)
  reads only two non-secret `hermes config get` keys, verified against
  https://hermes-agent.nousresearch.com/docs/user-guide/configuration:
  `model` (the active "provider/model" selection - a separate, narrower
  allowlist than lib/omes/py/provenance/versions.py's
  ALLOWED_HERMES_CONFIG_KEYS, documented in that module) and
  `providers.<id>.base_url` (a "Custom OpenAI-compatible endpoint" for the
  provider id parsed out of `model`, with `<id>` validated against a
  strict pattern before being used as a `hermes config get` argv value).
  Neither key can hold a credential. This module never sees
  `$HERMES_HOME/.env` or a credential value, and performs no network I/O
  of its own - the endpoint URL is parsed as a string (urllib.parse,
  ipaddress), never connected to.
- A missing, empty, or unparsable endpoint value fails closed: it is
  treated as NOT local, per the "unresolvable" bucket, which resolves to
  `cloud_sanitized` -> denied for RESTRICTED. A restricted posture must
  never be assumed to be safe merely because Hermes' endpoint
  configuration could not be read.
- A bare hostname that is not "localhost" and not an IP literal cannot be
  classified as local/private/public without a DNS lookup, which this
  read-only preflight module deliberately does not perform (module_check
  must stay read-only and side-effect-free, and a DNS answer is not
  stable evidence of where traffic will actually be routed at apply
  time). Such a value is also treated as unresolvable and fails closed.
- The only destination values this module ever passes to
  `egress_policy.evaluate()` are `local_only`, `private_endpoint`, or
  `cloud_sanitized` - never `deny` (reserved for an explicit caller
  request) and never a value outside egress_policy.DESTINATIONS.
- `contains_authentication_material` is always sent as `False`: this is a
  posture/configuration check, not an actual data transmission, so there
  is no content to assert about. The RESTRICTED classification and the
  destination value alone already drive the fail-closed decision for an
  unverified/cloud-routed endpoint.
"""
from __future__ import annotations

import ipaddress
import json
import os
import sys
from typing import Any, Optional
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from egress_policy import evaluate  # noqa: E402

EXIT_OK_ALLOW = 0
EXIT_ERROR = 1
EXIT_NOT_ALLOWED = 2

POLICY_VERSION = "v1"

#: Endpoint-locality classifications this module can produce.
ENDPOINT_CLASSES = frozenset({
    "local_only",
    "private_endpoint",
    "public",
    "unset",
    "unparsable",
    "unresolvable_hostname",
})

#: Maps an endpoint-locality classification to the bounded egress-policy
#: `destination` value. Anything that is not affirmatively local or
#: private (unset, unparsable, an unresolved hostname, or a public IP
#: literal) maps to `cloud_sanitized` - the closest existing destination
#: class to "this would leave the host toward the public internet" - so
#: the RESTRICTED decision matrix denies it via the already-published
#: AI_EGRESS_DENY_RESTRICTED_CLOUD_SANITIZED reason code instead of a new,
#: parallel meaning invented here.
_DESTINATION_FOR_CLASS = {
    "local_only": "local_only",
    "private_endpoint": "private_endpoint",
}


def classify_endpoint(raw: Optional[str]) -> str:
    """Classifies a Hermes model endpoint value (typically `model.base_url`)
    by network locality, without any network I/O. Returns one of
    ENDPOINT_CLASSES.
    """
    if not isinstance(raw, str) or not raw.strip():
        return "unset"

    value = raw.strip()
    candidate = value if "://" in value else f"//{value}"
    try:
        parsed = urlsplit(candidate)
        host = parsed.hostname
    except ValueError:
        return "unparsable"

    if not host:
        return "unparsable"

    host_l = host.lower()
    if host_l == "localhost" or host_l.endswith(".localhost"):
        return "local_only"

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # A bare hostname (not "localhost", not an IP literal) cannot be
        # classified without a DNS lookup - this preflight module never
        # performs one (see module docstring).
        return "unresolvable_hostname"

    if ip.is_loopback:
        return "local_only"
    if ip.is_private or ip.is_link_local:
        return "private_endpoint"
    return "public"


def resolve_destination(endpoint_class: str) -> str:
    return _DESTINATION_FOR_CLASS.get(endpoint_class, "cloud_sanitized")


def evaluate_restricted_posture(config: dict[str, Any]) -> dict[str, Any]:
    """Evaluates whether Hermes' currently configured model endpoint
    satisfies the RESTRICTED/local-only posture.

    `config` (all optional, all metadata-only):
      base_url: str|None                    - providers.<id>.base_url
                                               config value (<id> parsed
                                               from `model`, validated by
                                               the bash caller)
      model: str|None                       - the raw `model` config
                                               value ("provider/model";
                                               evidence only)
      private_endpoint_approved: bool       - operator has explicitly
                                               reviewed and approved a
                                               private_endpoint destination
                                               for this restricted posture
                                               (see docs/ai-data-privacy-
                                               and-model-security.md
                                               section 6: RESTRICTED +
                                               private_endpoint always
                                               requires explicit policy;
                                               OMES never defaults this on)
      provider_id: str|None                 - opaque, non-secret profile
                                               identifier for provider_posture

    Returns a bounded evidence object (docs section 11's allowed evidence
    shape): endpoint_classification, destination, decision, reason_codes,
    and `pass` (decision == "allow"). Never includes the raw base_url,
    provider, or model_name value in the returned decision fields beyond
    echoing the *classification bucket* - callers that want the raw
    values for local logging already have them from the config input.
    """
    base_url = config.get("base_url")
    endpoint_class = classify_endpoint(base_url)
    destination = resolve_destination(endpoint_class)

    approved_private_endpoint = (
        destination == "private_endpoint" and config.get("private_endpoint_approved") is True
    )
    posture_status = "approved" if (destination == "local_only" or approved_private_endpoint) else "not_approved"

    provider_id = config.get("provider_id")
    provider_posture: dict[str, Any] = {"status": posture_status}
    if isinstance(provider_id, str) and provider_id:
        provider_posture["provider_id"] = provider_id

    request = {
        "policy_version": POLICY_VERSION,
        "classification": "RESTRICTED",
        "destination": destination,
        "purpose": "restricted_local_inference",
        "provider_posture": provider_posture,
        "contains_authentication_material": False,
    }
    decision = evaluate(request)

    return {
        "endpoint_classification": endpoint_class,
        "destination": decision["destination"],
        "decision": decision["decision"],
        "reason_codes": decision["reason_codes"],
        "pass": decision["decision"] == "allow",
    }


def main(argv: list[str]) -> int:
    try:
        raw = sys.stdin.read()
        config = json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": f"invalid input JSON: {exc}"}))
        return EXIT_ERROR

    if not isinstance(config, dict):
        print(json.dumps({"ok": False, "error": "input JSON must be an object"}))
        return EXIT_ERROR

    try:
        result = evaluate_restricted_posture(config)
    except Exception as exc:  # noqa: BLE001 - top-level CLI boundary
        print(json.dumps({"ok": False, "error": f"internal error: {exc}"}))
        return EXIT_ERROR

    print(json.dumps({"ok": True, **result}))
    return EXIT_OK_ALLOW if result["pass"] else EXIT_NOT_ALLOWED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
