---
issue: 101
type: added
---
Add GitHub App installation/repository-mapping/webhook-envelope/provider-observation/disconnect/catalog-addon contracts, a stdlib HMAC-SHA256 webhook signature verifier with replay protection (`lib/omes/py/domains/github.py`), and a named minimum-permission profile (`lib/omes/py/domains/profiles/github.py`), with fake webhook tests for installation, access denial, duplicate delivery, revoked app, branch mismatch, and deployment status changes.
