---
issue: 202
type: docs
summary: record the AWCMS-based OMES Control Center release close-out, with shipped-versus-deferred evidence (epic #195)
---

### Summary of changes

1. **New close-out record (`docs/control-center-release-closeout.md`)**:
   - Evidence table for epic #195's six implementation children (#196–#201), each with its upstream `ahliweb/awcms` pull request and merge commit, plus the two OMES-side dependencies #192 (`18129dd`) and #183 (`0824f98`) that are merged on `main`.
   - Verification gates recorded PASS/BLOCKED as read back from `ahliweb/awcms#824`, including the 52-gate quality run, the DB-backed integration suites, the Playwright smoke run, and the 10 green GitHub checks. Gates that cannot be reproduced from this repository are marked BLOCKED with the reason.
   - Ownership-boundary verification: OMES/AWCMS/Hermes authorities unchanged and default-deny, with the specific checks for arbitrary shell, browser-to-host privileged paths, raw SSH secret storage, Hermes private-state coupling, and approval gating.
   - Cross-repository hygiene: `ahliweb/awcms-one` has no open issues and the migrated backlog stays closed; the `apps/cms` subtree lag that leaves #199–#201 unsynced there is recorded.
   - Deployment/environment, enrollment and operator flow, permission model, operational and recovery guidance, privacy/threat-model impact, and the agent/contributor rules needed to maintain the integration.
   - A "next release" note: `VERSION` stays `0.3.0` in this change; `scripts/release.sh` runs on `main` after the open pull requests land.

2. **Canonical documents corrected to match the implementation**:
   - `docs/control-center-and-integrations.md` — status banner no longer implies no web GUI exists anywhere; new §11.1 (AWCMS-side delivery evidence) and §11.2 (deferred work); the #91 delivery row and §1.1 prototype status corrected; the known v1 worker-contract defect recorded in §4.1.
   - `docs/control-center-foundation.md` — corrects the untrue claim that the web GUI, tenant/RLS tables and RBAC enforcement are implemented in `awcms-one`; the canonical repository is `ahliweb/awcms`, with `awcms-one` as a subtree-based integration deployment.
   - `docs/ui-ux-design-system.md` — records which screens shipped upstream and that the prototype on `main` is the v1 design reference.
   - `docs/README.md` — index entries updated and the close-out added.
   - `AGENTS.md` — Control Center maintenance rules (implementation repository, contract-first changes, allowlist changes) and a pointer to the close-out.

3. **Deferred work recorded with exact wording**, never as shipped: the prototype redesign v2 (#211, PR #212), the v1 worker contract fix (#221, PR #222), and the AI data-privacy chain (#213–#218, PRs #219/#223/#224/#225/#226/#227) are each written as `Not implemented yet (tracked in #N)` with the naming pull request.
