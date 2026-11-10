---
issue: 352
type: ci
---
Added the blocking `check-worker-compat-manifest` job, wired in `scripts/ci-local.sh` and the `lint.yml` workflow like `check-control-center-data`: it runs `scripts/generate-worker-compat-manifest.py --check`, which regenerates the unreleased worker compatibility manifest from repository facts, validates it against its schema, recomputes the contract digest, verifies deterministic output and confirms the reference consumer rejects an unreleased manifest. The check is offline and stdlib only. Documented in `docs/ci.md` and `docs/worker-compatibility-manifest.md`. Signing and publishing the manifest as a release asset: Not implemented yet (tracked in #352).
