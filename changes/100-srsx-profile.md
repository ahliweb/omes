---
issue: 100
type: added
---
Add the SRS-X `.id` registrar capability profile as data (`lib/omes/py/domains/profiles/srsx.py`, sourced from SRS-X's own API documentation), a reseller config/preflight contract, a document-required lifecycle contract kept separate from the raw (ambiguous) API result code, a short-lived document-upload-reference contract, and safe-retry classification, with fake-provider tests for registration, renewal, API failure, duplicate requests, document URL expiry, rejection, and credential redaction.
