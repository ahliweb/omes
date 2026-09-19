"""lib/omes/py/domains/profiles/srsx.py - SRS-X (.id) registrar
capability profile as DATA (issue #100).

Every field below is derived from SRS-X's own knowledge base (fetched
2026-09-19; see docs/domain-providers.md section 3 for the same
citations in prose):

- https://kb.srs-x.com/en/api/domain — lists Register/Express
  Register/Premium Express Register, Renew, Check (availability),
  Upload Document Link, Transfer + Transfer Lock/Protection, ID
  protection, nameserver updates, EPP code management, and contact
  modification as available domain operations.
- https://kb.srs-x.com/en/api/domain/register-domain — registration
  behavior depends on an `autoactive` flag: with auto-provisioning the
  domain activates immediately (result code `1000`); without it, "Domain
  is still waiting for the complete document" (result code `1001`,
  which also covers a hard failure — the two are NOT distinguishable by
  result code alone, only by the accompanying message/subsequent status
  check).
- https://kb.srs-x.com/en/api/domain/renew-domain — renewal takes
  `domain`, `api_id`, `periode` (years); authentication uses a username
  plus a SHA256-hashed password; the response is synchronous
  (`1000`/`exDate` or `1001`), no pending state described.
- https://kb.srs-x.com/en/api/domain/upload-document-link — generates a
  domain-specific upload URL "available for 10 minutes only"; the
  document type is only described as ".ID required prerequisites
  documents", with no enumerated list of accepted document types on that
  page.
- https://www.iana.org/domains/root/db/.id — `.id` is PANDI's
  ccTLD for Indonesia; PANDI (via its registrars/resellers, including
  SRS-X) sets the actual registrant-document policy per second-level
  zone (e.g. `co.id`, `or.id`). This repository does not encode PANDI's
  document-requirement rules per second-level zone; that policy detail
  is exactly the kind of provider-account-specific configuration issue
  #100 says an operator must validate before production (see
  docs/domain-providers.md section 3.6).
"""
from __future__ import annotations

from typing import Any

# Illustrative subset only - PANDI/.id policy (which second-level zones
# require which registrant documents) is not fully encoded here; an
# operator must confirm current SRS-X/PANDI requirements before
# production use (see module docstring and docs/domain-providers.md
# section 3.6).
REGISTRAR_TLDS: tuple[str, ...] = ("id", "co.id", "or.id")

REGISTRAR_CAPABILITY: dict[str, Any] = {
    "capability_id": "cap-srsx-registrar-v1",
    "provider": "srsx",
    "extension_pattern": "^(" + "|".join(t.replace(".", r"\.") for t in REGISTRAR_TLDS) + ")$",
    "account_scope": "acct-srsx-reseller",
    "supported_operations": ["search", "availability", "pricing", "registration", "read_sync", "renewal"],
    "manual_fallback": False,
    "notes": (
        "Transfer, contact update, and DNS/DNSSEC are NOT included here: SRS-X's "
        "knowledge base documents transfer/contact/nameserver operations exist, "
        "but this repository has not verified their request/response contracts "
        "against a live or sandbox account, per AGENTS.md #100's instruction not "
        "to claim automation until verified. Route them to manual_fallback."
    ),
}

# Result codes exactly as documented by
# kb.srs-x.com/en/api/domain/register-domain and .../renew-domain.
API_RESULT_CODE_SUCCESS = 1000
API_RESULT_CODE_FAILED_OR_PENDING = 1001

DOCUMENT_UPLOAD_LINK_TTL_SECONDS = 600  # "available for 10 minutes only"

REQUIRED_CREDENTIAL_FIELDS: tuple[str, ...] = ("reseller_id", "api_username", "password_reference", "endpoint", "authorized_egress_ip")
