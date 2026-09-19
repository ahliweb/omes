---
issue: 99
type: added
---
Add the Cloudflare Registrar/DNS capability profile as data (`lib/omes/py/domains/profiles/cloudflare.py`, sourced from Cloudflare's own API-beta and registration documentation), a preflight contract for scoped API tokens, a registration polling contract that never treats a timeout as success, and a DNS drift-report contract, with fake-provider tests for async polling, duplicate jobs, price changes, unsupported TLDs, DNS drift, and secret redaction.
