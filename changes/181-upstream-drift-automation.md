---
issue: 181
type: chore
summary: automate upstream drift review and deprecation tracking (ADR-0026)
---

### Summary of changes

1. **Architectural Decision Record (ADR-0026)**:
   - Authored `docs/adr/0026-upstream-drift-automation.md` evaluating automated upstream drift review across 11 architectural criteria.
   - Established deterministic, read-only inspection comparing public upstream release metadata and candidate changes against `architecture/capabilities.json`.
   - Registered ADR-0026 in `docs/adr/README.md`.

2. **Core Upstream Drift Engine (`lib/omes/py/architecture/drift.py`)**:
   - Implemented pure-Python drift evaluation comparing public releases and `main` commits for Hermes (`NousResearch/hermes-agent`), Omarchy (`omacom/omarchy`), Graphify (`graphify` / PyPI `graphifyy`), and Coolify (`coollabsio/coolify`).
   - Classifies findings across standard taxonomies:
     - `NO_IMPACT`: Baseline matches latest upstream release.
     - `NEW_DELEGATE_CANDIDATE`: Upstream release provides capability allowing OMES to delegate duplicated/adapted logic under ADR-0017.
     - `PORT_ADAPT_REVIEW`: Upstream changes require OMES architectural review.
     - `BREAKING_CHANGE`: Upstream altered public APIs or CLI semantics.
     - `SECURITY_OR_LICENSE_REVIEW`: Upstream modified SPDX license, license terms, or package provenance.
     - `BASELINE_UPDATE_AVAILABLE`: Newer stable release available above pinned baseline.
   - Enforces read-only safety: never auto-ports, auto-merges, or mutates code or hosts.
   - Distinguishes released, supported baselines from candidate changes observed on `main` (never promotes candidates to `released_supported`).
   - Fail-closed error handling: reports `BLOCKED` or `WARN` on API/network failures rather than assuming no change.
   - Deduplicated GitHub issue management (`reconcile_github_issue`): searches for existing issue with label `area:upstream-drift` and updates it, preventing weekly duplicate notification spam.

3. **CLI Inspection Tool & Release Gate (`scripts/upstream-drift.py`)**:
   - Added command-line interface supporting `--json`, `--check`, `--offline`, `--fixtures`, and `--update-issue`.
   - Exits 0 when clean, 1 when actionable drift is detected (under `--check`), and 2 when upstream inspection is blocked.
   - Updated `docs/business/release-gates.md` adding an explicit upstream drift review gate for alpha/beta/public releases.
   - Updated `docs/architecture.md` (§16.3) documenting upstream drift review workflows.

4. **Continuous Automation Workflow (`.github/workflows/upstream-drift.yml`)**:
   - Added weekly scheduled GitHub Actions workflow running Mondays at 06:00 UTC and on manual `workflow_dispatch`.
   - SHA-pinned all GitHub Actions per supply-chain policies.

5. **Unit and Integration Tests (`tests/py/architecture/test_drift.py`)**:
   - Added test coverage for no-change baseline, new released versions, candidate tracking, blocked/failed API responses, license modifications, duplicated capability detection, GitHub issue deduplication, and CLI execution modes.
