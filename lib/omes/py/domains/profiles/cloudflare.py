"""lib/omes/py/domains/profiles/cloudflare.py - Cloudflare Registrar and
DNS capability profiles as DATA (issue #99).

Every field below is derived from what Cloudflare's own documentation
states, not invented. Sources (fetched 2026-09-19, see
docs/domain-providers.md section 2 for the same citations in prose):

- https://developers.cloudflare.com/registrar/registrar-api/ — the
  Registrar API is explicitly in **beta** and states: "Only a subset of
  supported Cloudflare Registrar extensions are available through the
  API beta" and "Some extensions supported in the dashboard are not yet
  available for programmatic registration." A domain outside that subset
  returns `extension_not_supported_via_api`. The documented API
  operations are **search, availability checking, and registration
  only** — renewals, transfers, and contact updates are explicitly not
  yet available via the API.
- https://developers.cloudflare.com/registrar/get-started/register-domain/
  — registration terms run up to 10 years; auto-renew defaults on;
  Internationalized Domain Names (IDNs) are NOT supported; a domain
  registered through Cloudflare Registrar is **locked to Cloudflare
  DNS** ("will not be able to change to another DNS provider's
  nameservers while using Cloudflare Registrar"); DNSSEC is offered as a
  next step after registration.

Because Cloudflare has not published the exact API-supported extension
list (the page contains only a placeholder for it), `REGISTRAR_TLDS`
below is a deliberately small, conservative starting set of widely
documented gTLDs. **This list is illustrative, not authoritative** — an
operator must verify current API support (the live
`extension_not_supported_via_api` response, or Cloudflare's published
list once it exists) before relying on it, and any TLD not present here
already correctly falls through to `manual_fallback` via
`lib/omes/py/domains/routing.py`.
"""
from __future__ import annotations

from typing import Any

# Conservative, illustrative subset only - see module docstring.
REGISTRAR_TLDS: tuple[str, ...] = ("com", "net", "org")

# Cloudflare's own documentation explicitly does not offer renewal,
# transfer, or contact-update through the Registrar API today - these
# are intentionally excluded so routing.py's capability match fails
# (-> manual_fallback) for them, per AGENTS.md #99: "do not claim
# automated renewal, transfer, or contact update until the provider API
# capability is verified".
REGISTRAR_CAPABILITY: dict[str, Any] = {
    "capability_id": "cap-cloudflare-registrar-v1",
    "provider": "cloudflare",
    "extension_pattern": "^(" + "|".join(REGISTRAR_TLDS) + ")$",
    "account_scope": "acct-cloudflare-scoped-token",
    "supported_operations": ["search", "availability", "pricing", "registration", "read_sync"],
    "manual_fallback": False,
    "notes": (
        "API beta; extension list is illustrative and must be verified "
        "against extension_not_supported_via_api before production use. "
        "IDNs are not supported (register-domain guide)."
    ),
}

# Every domain registered through Cloudflare Registrar is locked to
# Cloudflare DNS - this capability therefore always applies to the same
# TLD set as the registrar capability, since a non-Cloudflare-registered
# domain would need a different DNS adapter entirely.
DNS_CAPABILITY: dict[str, Any] = {
    "capability_id": "cap-cloudflare-dns-v1",
    "provider": "cloudflare",
    "extension_pattern": "^(" + "|".join(REGISTRAR_TLDS) + ")$",
    "account_scope": "acct-cloudflare-scoped-token",
    "supported_operations": ["dns_records", "dnssec"],
    "manual_fallback": False,
    "notes": "Only applies to domains registered through Cloudflare Registrar (mandatory Cloudflare nameservers).",
}

# Required scopes for the scoped API token this profile expects
# (issue #99: "scoped-token secret reference"). These are OMES's own
# naming for the permissions it needs, not a literal Cloudflare API
# permission-group name - preflight.py checks that a credential
# reference declares (at minimum) these scopes before treating a token
# as usable.
REQUIRED_TOKEN_SCOPES: tuple[str, ...] = ("registrar:read", "registrar:write", "dns:edit")

MAX_TERM_YEARS = 10  # register-domain guide: "up to 10 years for most TLDs"
SUPPORTS_IDN = False  # register-domain guide: IDNs are not supported
DNS_LOCKED_TO_PROVIDER = True  # cannot switch to another DNS provider while using Cloudflare Registrar
