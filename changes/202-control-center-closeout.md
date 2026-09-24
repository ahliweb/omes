---
issue: 202
type: docs
summary: record the AWCMS-based OMES Control Center release close-out, with shipped-versus-deferred evidence (epic #195)
---

### Summary of changes

1. **Close-out record (`docs/control-center-release-closeout.md`)**:
   - Evidence table for epic #195's six implementation children (#196–#201), each with its upstream `ahliweb/awcms` pull request and merge commit, plus the two OMES-side dependencies #192 (`18129dd`) and #183 (`0824f98`) that are merged on `main`.
   - Verification gates recorded PASS/BLOCKED as read back from `ahliweb/awcms#824`, including the 52-gate quality run, the DB-backed integration suites, the Playwright smoke run, and the 10 green GitHub checks. Gates that cannot be reproduced from this repository are marked BLOCKED with the reason.
   - Ownership-boundary verification: OMES/AWCMS/Hermes authorities unchanged and default-deny, with the specific checks for arbitrary shell, browser-to-host privileged paths, raw SSH secret storage, Hermes private-state coupling, and approval gating.
   - Cross-repository hygiene: `ahliweb/awcms-one` has no open issues and the migrated backlog stays closed; the `apps/cms` subtree lag that leaves #199–#201 unsynced there is recorded.
   - Deployment/environment, enrollment and operator flow, permission model, operational and recovery guidance, privacy/threat-model impact, and the agent/contributor rules needed to maintain the integration.
   - A "next release" note: `VERSION` stays `0.3.0` in this change; a separate release pull request runs `scripts/release.sh 0.4.0` on `main` after this close-out merges.
   - §6/§6.1 rewritten: the prototype redesign v2 (#211, commit `0820e6e`, PR #212), the v1 worker contract fix (#221, commit `62c3b01`, PR #222), and the AI data-privacy chain (#213–#218, commit `ce44b0a`, PR #231) — previously recorded here as deferred — are now recorded as shipped, with merge-commit and PR evidence. AWCMS-side consumption of the AI privacy posture/egress-approval contracts, live Cloudflare/SRS-X/GitHub provider clients (#99–#101), a dedicated enrollment-token screen, and the `awcms-one` subtree sync lag remain genuinely deferred.

2. **Canonical documents corrected to match the now-merged implementation**:
   - `docs/control-center-and-integrations.md` — §1.1 prototype status, §4.1 worker-contract status, the #217 delivery row, and §11.2's deferred-work list all updated from "not implemented yet (tracked in #N)" to shipped, with commit/PR evidence; the AI privacy AWCMS-side consumption gap is kept accurately deferred.
   - `docs/ui-ux-design-system.md` — records the v2, fixture-generated prototype (#211/#212 shipped) rather than the retired v1 prototype.
   - `docs/control-center-contracts.md` §2.10 — corrects a self-referential "not implemented yet (tracked in #217)" claim for AWCMS-side consumption (#217 itself is closed); records that the AWCMS-side consumption gap has no owning OMES issue.
   - `docs/ai-data-privacy-and-model-security.md`, `docs/security.md`, `docs/architecture.md`, `docs/threat-model.md`, `docs/adr/0029-ai-data-boundary-and-private-inference.md`, `docs/testing.md`, `docs/cli.md` — every "Not implemented yet (tracked in #21{1,3,4,5,6,7,8})" / "tracked in #221" claim made stale by commits `0820e6e`, `62c3b01`, and `ce44b0a` is corrected to shipped, with commit/PR evidence. Genuinely still-deferred items (RAG/embedding classification propagation, model/runtime provenance evidence, AWCMS-side AI-privacy consumption, automated backup-scope enforcement) are kept deferred and, where no issue tracks them, say so explicitly instead of citing a now-closed issue.
   - `docs/control-center-foundation.md`, `docs/README.md`, `AGENTS.md` — unchanged in substance from the prior version of this close-out; still accurate.

3. **Nothing in this change bumps `VERSION`, runs `scripts/release.sh`, or creates a tag.**

- Every deferred item this release surfaced now has an owning issue, so none is left untracked (AGENTS.md §4.5): #232 (AWCMS-side consumption of the AI-privacy contracts), #233 (enrollment-token management screen), #234 (AI-privacy evidence retention/rotation), #235 (backup-scope enforcement for Restricted data), #236 (model/runtime artifact provenance evidence), #237 (provider-assurance evidence fields). RAG/embedding classification propagation stays deliberately untracked: #218's regression canary fails the build the day such a pipeline lands without coverage.
