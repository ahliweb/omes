---
issue: 347
type: docs
---
`docs/compatibility-matrix.md` gains section 9, a repository-derived dependency and runtime status matrix (Hermes baseline, supported OS tiers, Python, Bash/systemd, Bun UI tooling, Playwright, container images) with the source file for each value, `UNVERIFIED` markers where the repo pins nothing, a holdback register, and a documented weekly review process that states which inputs are automated today (manual-only upstream drift report, Dependabot for GitHub Actions) and which parts are not implemented yet (tracked in #347).
