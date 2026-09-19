"""lib/omes/py/domains - provider-neutral domain/DNS abstraction (issue #98).

Stdlib only (ADR-0012). This package implements the OMES-side pieces of
the boundary defined by `contracts/domains/v1/*.schema.json` and
docs/domain-providers.md:

- `routing`: TLD/operation -> `RegistrarCapability` lookup, with an
  explicit `manual_fallback` path when no capability matches.
- `states`: the domain-order state machine (pending/action_required/
  failed/succeeded/active/expired/renewal_due/cancelled) as transition
  data, mirroring `lib/omes/py/jobs/store.py`'s transition-table style.
- `fake_provider`: an in-memory `RegistrarAdapter`/`DnsAdapter`
  implementation used by tests (never live network calls) that can model
  both a Cloudflare-like async registration flow and an SRS-X-like
  document-required flow.

It does not implement a live Cloudflare or SRS-X client - those adapters,
and any HTTP/webhook transport, are issues #99/#100 and, ultimately,
awcms-one (see docs/domain-providers.md "What remains in awcms-one").
"""
