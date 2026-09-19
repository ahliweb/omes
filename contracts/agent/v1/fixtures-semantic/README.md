# Semantic-only fixtures

Manifests here are **schema-valid** but rejected by the semantic checks in
`lib/omes/py/agent/manifest.py` / `compose.py` (Docker socket mounts, host
network mode, a `compose` backend without a `spec.compose` object, ...).
`scripts/check-contracts.py` deliberately does not scan this directory —
it validates JSON Schema only. `tests/py/agent/test_manifest.py` asserts
every `invalid-*.json` here fails `manifest.validate()`.
