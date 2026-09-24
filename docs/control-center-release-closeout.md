# OMES Control Center — release close-out (epic #195, issue #202)

> Status: evidence record, compiled 2026-09-25. This document states what
> actually shipped and what did not. It is **not** a plan and must not be read
> as a claim that anything listed as deferred exists.
>
> Owning issues: [#202](https://github.com/ahliweb/omes/issues/202) (close-out),
> parent [#195](https://github.com/ahliweb/omes/issues/195) (epic). All six
> implementation children of #195 (§2), both OMES-side dependencies (§2), the
> Control Center prototype redesign v2 (#211), the v1 worker contract fix
> (#221), and the AI data-privacy boundary chain (#213–#218) are merged on
> `main` as of commit `ce44b0a`. This pull request closes both #202 and #195.

## 1. What this close-out covers

Epic #195 tracks the AWCMS-based OMES web Control Center. The owning issues
live in `ahliweb/omes`; the reusable web implementation (domain module, API
handlers, permissions/RLS, and all system-admin `/admin/*` screens) lands
canonically in `ahliweb/awcms`. `ahliweb/awcms-one` is an
integration/reference deployment whose `apps/cms` directory is a `git subtree`
of `ahliweb/awcms`.

Two boundary facts follow from that split and are unchanged by this release:

- No OMES web GUI code exists in this repository. This repository ships the
  versioned contracts, the job runner, and the outbound pull worker.
- The AWCMS Control Center is a *consumer* of those contracts. It never
  executes host commands itself.

## 2. Implementation children — shipped, with evidence

All six implementation children of #195 are closed and merged upstream in
`ahliweb/awcms`. Each OMES issue carries an evidence comment naming the
upstream pull request and merge commit; each upstream pull request links back
to its owning OMES issue.

| OMES issue | Scope | Upstream PR | Merge commit | State |
|---|---|---|---|---|
| [#196](https://github.com/ahliweb/omes/issues/196) | `omes_control` module schema, FORCE RLS, permissions, descriptor | `ahliweb/awcms#814` | `34c7ea69` | Merged |
| [#197](https://github.com/ahliweb/omes/issues/197) | Pinned OMES v1 contract consumption, fail-closed validator, drift gate | `ahliweb/awcms#816` | `a82d8e60` | Merged |
| [#198](https://github.com/ahliweb/omes/issues/198) | Owner/operator API for servers, deployments, jobs, health, backups, audit | `ahliweb/awcms#815` | `64506d65` | Merged |
| [#199](https://github.com/ahliweb/omes/issues/199) | Worker enrollment, poll, result, heartbeat ingestion | `ahliweb/awcms#823` | `a43f7268` | Merged |
| [#200](https://github.com/ahliweb/omes/issues/200) | Overview, Servers, Deployments, Operations, Jobs admin screens | `ahliweb/awcms#822` | `6bb6d491` | Merged |
| [#201](https://github.com/ahliweb/omes/issues/201) | Health, Backup/recovery, Audit admin screens; module promoted to `active` | `ahliweb/awcms#824` | `52e8f8b4` | Merged |

The two OMES-side dependencies are also merged into this repository's `main`:

| OMES issue | Scope | OMES commit |
|---|---|---|
| [#192](https://github.com/ahliweb/omes/issues/192) | Outbound pull-worker transport, enrollment, job bridge, heartbeat | `18129dd` |
| [#183](https://github.com/ahliweb/omes/issues/183) | Hermes delegated-task orchestration visualization (observability projection only) | `0824f98` |

`#192` and `#183` are therefore **not** blocked and **not** pending. No claim
elsewhere in this repository should describe them as unlanded.

## 3. Verification gates — recorded result

### 3.1 AWCMS side (`ahliweb/awcms#824`, the final implementation PR)

Results below are transcribed from that pull request's own test plan and from
its GitHub check run list; they were produced in the AWCMS repository, not
here.

| Gate | Command | Result |
|---|---|---|
| Full quality gate (52 gates: lint, docs, contracts, module/access/DB/API gates, i18n, typecheck, unit tests, build) | `DATABASE_URL="" bun run check` | PASS — 6232 pass, 0 fail, 1005 skip across 7237 collected tests |
| Integration tests (harness suite, real PostgreSQL with FORCE RLS) | `bun test tests/integration/ --timeout 60000` | PASS — 685 pass, 0 fail across 81 files |
| DB-gated legacy suite (CI's explicit 17-file list) | `bun test <17 files>` | PASS — 148 pass, 0 fail |
| Browser/E2E smoke | `bun run test:e2e` (Playwright) | PASS — 37 passed |
| Typecheck | `tsc --noEmit` | PASS |
| Changeset policy | `bun run changesets:policy:check` | PASS |
| GitHub required checks | 10 checks incl. CodeQL, GitGuardian, Repo hygiene (Bun-only + no secrets), Integration tests (RLS + DB role separation), E2E smoke, Minimum-supported versions | PASS — all 10 green |

Tenant/RLS, API/contract parity, i18n/accessibility/contrast, dependency and
secret scanning are covered inside the gates above rather than as separate
commands; see `ahliweb/awcms#824` for the per-gate breakdown.

Not re-verified in that session, and recorded as such by the PR author: the
`minimum-supported` Bun 1.3.0 floor job was not re-run locally. It did run and
pass as a GitHub check.

### 3.2 OMES side (this repository, this change)

See the Verification section of the pull request that introduces this
document. This change is documentation-only; per `AGENTS.md` §6 the required
checks are the link checker, contract and architecture checks, `git diff
--check`, change-fragment validation, and a stale-claim review.

### 3.3 Gates that are BLOCKED

- Profile/storefront gates: **not applicable**. `apps/storefront` is not the
  OMES management plane and this release changed nothing in it.
- Re-running the AWCMS gates from this repository: **BLOCKED**. They require
  the `ahliweb/awcms` working tree, Bun, and a PostgreSQL container. The
  results in §3.1 are read back from the upstream pull request, not reproduced
  here.

## 4. Ownership boundary — verified unchanged and default-deny

| Concern | Authority | Evidence that it is unchanged |
|---|---|---|
| Host compatibility, preflight, lifecycle, hardening, health, backup/restore/rollback, provenance | OMES | The Control Center submits an `operation-request` limited to the allowlist in `contracts/control-center/v1/operation-request.schema.json` (`status, preflight, start, stop, restart, update, backup, rollback`). #198 asserts that enum byte-for-byte in a test; `install`, `configure` and `restore` are excluded from the safe-operation allowlist. |
| Tenants, identity, RBAC/ABAC/RLS, approvals, read models, reporting | AWCMS/Control Center | #196 creates eight tenant-scoped tables with `ENABLE` and `FORCE ROW LEVEL SECURITY` and seeds 13 granular default-deny permissions. No permission is granted implicitly. |
| Agent reasoning, delegation, sessions, memory, skills, model/provider routing | Hermes | #183 ingests read-only `hermes.observer.v1` events into an observability projection validated against `contracts/control-center/v1/hermes-orchestration-event.schema.json`. No OMES delegation engine, no second Hermes runtime. |

Default-deny is preserved end to end: the browser's decided permission is
carried as data in `operation-request.permission`, and the OMES job runner
independently re-derives its own allowlist and approval decision rather than
trusting that field (see [control-center-foundation.md](control-center-foundation.md) §3).

### 4.1 Specific safety claims, each checked

- **No arbitrary shell.** The pull worker executes polled jobs through
  `lib/omes/py/jobs/store.py` and `lib/omes/py/jobs/runner.py` with fixed
  argv. No shell invocation path was added by this release.
- **No browser-to-host privileged path.** The transport is outbound-only:
  managed nodes keep zero listening ports for Control Center traffic
  (ADR-0027, #192). A browser reaches AWCMS; AWCMS never reaches the host
  directly.
- **No raw SSH secret storage.** Worker credentials are established by
  challenge-based enrollment and stored on the host under
  `<state-dir>/worker/credentials.json` (mode `0600`), not in the Control
  Center database. Every contract rejects a raw-secret-shaped value via
  `scan_for_raw_secrets()` in `lib/omes/py/jobs/schema.py`.
- **No Hermes private-state coupling.** `scripts/check-architecture.py`
  forbids direct access to `messages.db` and `.hermes/`. The orchestration
  projection consumes the supported observer event contract and bans
  chain-of-thought, raw prompts, full transcripts, shell commands and
  credentials.
- **Destructive operations stay approval-gated.** `stop`, `rollback` and
  backup restore route through the single canonical AWCMS
  `workflow-approval` engine (workflow key `omes_control.destructive_operation`).
  With no active published definition the request is refused
  `409 APPROVAL_WORKFLOW_NOT_CONFIGURED` and nothing is persisted. No second
  approval authority was created.

## 5. Cross-repository backlog hygiene

- `ahliweb/awcms-one` has **zero open issues** as of 2026-09-24
  (`gh issue list --repo ahliweb/awcms-one --state open`). The migrated
  backlog — `awcms-one#146` and `#151`–`#158` — is closed and retitled
  `[MOVED to ahliweb/omes]`, retained only as audit references.
- Future OMES Control Center issues stay in `ahliweb/omes`.
- Every upstream implementation pull request in §2 links back to its owning
  OMES issue, and each OMES issue carries the reciprocal merge-commit
  evidence comment.

### 5.1 Known integration lag in `awcms-one`

`ahliweb/awcms-one` last synced its `apps/cms` subtree in `awcms-one#216`
(`chore(cms): sync AWCMS subtree from 8c64528d to 2d29a446`, merged
2026-09-24T06:02:41Z). `2d29a446` is an ancestor of `awcms` `main` but
predates `a43f7268`, `6bb6d491` and `52e8f8b4`.

Therefore the reference deployment currently carries #196, #197 and #198 only.
**#199, #200 and #201 are merged upstream in `ahliweb/awcms` but are not yet
present in `awcms-one/apps/cms`.** Syncing them is a `git subtree pull
--prefix=apps/cms awcms main` in `awcms-one`, merged with a merge commit
(never squash or rebase), and is owned by that repository.

## 6. Work that shipped after being tracked as deferred

Each item below was recorded as deferred in an earlier version of this
document. All of it is now merged to `main` and available today.

| Capability | State |
|---|---|
| Control Center prototype redesign v2 driven by v1 contract fixtures | **Shipped.** Commit `0820e6e` (PR [`ahliweb/omes#212`](https://github.com/ahliweb/omes/pull/212), closing [#211](https://github.com/ahliweb/omes/issues/211)). `ui/control-center/index.html` on `main` is now the v2, fixture-generated prototype; see [ui-ux-design-system.md](ui-ux-design-system.md). |
| `worker-result.request` requiring a `job_id` the v1 contract never provides, and `worker-poll.response.job` having no shape | **Shipped.** Commit `62c3b01` (PR [`ahliweb/omes#222`](https://github.com/ahliweb/omes/pull/222), closing [#221](https://github.com/ahliweb/omes/issues/221)). `job_id` was removed from `worker-result.request`; `worker-poll.response.job` now has a closed schema. |
| AI data-privacy boundary and local/cloud model policy | **Shipped.** Commit `ce44b0a` (PR [`ahliweb/omes#231`](https://github.com/ahliweb/omes/pull/231), closing [#213](https://github.com/ahliweb/omes/issues/213)). See [ai-data-privacy-and-model-security.md](ai-data-privacy-and-model-security.md) and [ADR-0029](adr/0029-ai-data-boundary-and-private-inference.md). |
| Machine-readable AI data-classification and egress policy | **Shipped.** Commit `ce44b0a` (PR #231, closing [#214](https://github.com/ahliweb/omes/issues/214)). `contracts/ai-egress/v1/` and `lib/omes/py/privacy/egress_policy.py`. |
| AI privacy posture and egress evidence without raw prompt capture | **Shipped.** Commit `ce44b0a` (PR #231, closing [#216](https://github.com/ahliweb/omes/issues/216)). `lib/omes/py/privacy/posture_evidence.py`, `omes health ai-privacy`. |
| Control Center projection of AI privacy posture and policy decisions | **Shipped**, OMES side only. Commit `ce44b0a` (PR #231, closing [#217](https://github.com/ahliweb/omes/issues/217)). `lib/omes/py/privacy/posture_projection.py` and the `ai-privacy-posture-view`/`ai-egress-approval` contracts. AWCMS-side consumption remains genuinely deferred — see below. |
| Restricted local-only inference deployment posture for Hermes | **Shipped.** Commit `ce44b0a` (PR #231, closing [#215](https://github.com/ahliweb/omes/issues/215)). `modules/hermes-restricted`, `lib/omes/py/privacy/restricted_posture.py`. |
| AI privacy boundary regression and exfiltration-resistance coverage | **Shipped.** Commit `ce44b0a` (PR #231, closing [#218](https://github.com/ahliweb/omes/issues/218)). |

The work was originally proposed as a stacked chain of pull requests
(`#219` ← `#223` ← `#224` ← `#225` ← `#226` ← `#227`, each based on the
previous branch, not on `main`). Some of those PRs (`#223`, `#225`, `#227`)
merged into their intermediate base branches; none of them merged into
`main` directly. PR #231, based on `main`, carries an identical set of
commits and is the pull request that actually landed on `main` as `ce44b0a`;
`#219`, `#224` and `#226` were closed unmerged once #231 superseded the
stack.

## 6.1 Genuinely still deferred

| Capability | State |
|---|---|
| AWCMS-side screen/API/database consumption of the AI privacy posture and egress-approval contracts | Not implemented yet. No OMES issue owns this AWCMS-side work; see [control-center-contracts.md](control-center-contracts.md) §2.10. |
| Live Cloudflare, SRS-X and GitHub provider clients | Not implemented yet (tracked in [#99](https://github.com/ahliweb/omes/issues/99), [#100](https://github.com/ahliweb/omes/issues/100), [#101](https://github.com/ahliweb/omes/issues/101)); only contracts, capability profiles as data, and fake-provider tests exist here. See [control-center-and-integrations.md](control-center-and-integrations.md) §11. |
| A dedicated enrollment-token management screen (`enrollments.manage`) | Not implemented yet (no owning OMES issue exists; #201 deliberately left it without a navigation entry). Servers renders read-only enrollment/trust evidence today. |
| `#199`, `#200`, `#201` present in `awcms-one/apps/cms` | Not yet synced; see §5.1. Owned by `ahliweb/awcms-one`, not by an OMES issue. |

## 7. Operating the integration

### 7.1 Deployment and environment

The AWCMS Control Center is deployed as part of an AWCMS installation; OMES
does not install or supervise it. On the OMES side a managed node needs only:

- an installed OMES with its state directory writable by the worker's user;
- outbound HTTPS egress to the Control Center API — no inbound port;
- the worker enrolled against that Control Center.

There is no OMES-side listener, reverse proxy, or exposed API to configure for
this integration.

### 7.2 Enrollment and operator flow

```text
operator obtains a single-use enrollment secret from the Control Center
  → on the node: omes worker enroll
  → challenge-based mutual authentication
  → credentials written to <state-dir>/worker/credentials.json (0600)
  → omes worker poll   — outbound; receives allowlisted jobs only
  → job runs via fixed-argv runner, never a shell
  → omes worker        — posts sanitized results
  → omes worker heartbeat — liveness; a stale heartbeat reads as
                            stale/offline, never as healthy
```

Operator-initiated mutations follow the Control Center path instead:

```text
operator selects an allowlisted operation in /admin/omes/*
  → AWCMS authorizes (default-deny permission, tenant-scoped, FORCE RLS)
  → destructive operations enter the workflow-approval engine
  → approved request becomes an idempotent OMES job with a correlation ID
  → OMES preflights, executes one allowlisted operation, verifies read-back
  → result and audit evidence are projected back into the Control Center
```

See [docs/jobs.md](jobs.md) for the job contract and
[docs/control-center-contracts.md](control-center-contracts.md) for the wire
shapes.

### 7.3 Permissions

Thirteen granular, default-deny permissions are seeded by the AWCMS migration
`sql/155`. The screens delivered by #200 and #201 use, among them,
`omes_control.servers.read`, `omes_control.backups.read`,
`omes_control.backups.restore` and `omes_control.audit.read`. There is no
separate `health.read`: the Health screen is gated on `servers.read`, matching
its endpoint's own guard. `omes_control.enrollments.manage` is seeded but has
no navigation entry (§6).

### 7.4 Operational and recovery guidance

- **Stale evidence is never success.** Health rows carry an explicit `stale`
  flag rendered *alongside* the overall status, never replacing it. A missing
  heartbeat reads as stale or offline.
- **Backup restore is always destructive** and always approval-gated. The
  Control Center screen submits the request and links the resulting workflow
  instance into `/admin/approvals`; it is not a second approval surface.
- **The Control Center is not the recovery path.** If it is unavailable,
  recovery runs locally on the host with the OMES CLI — see
  [docs/disaster-recovery.md](disaster-recovery.md) and
  [docs/rollback.md](rollback.md). Nothing in this release made local recovery
  depend on the web control plane.
- **Reconciliation, not optimism.** A timeout or an accepted asynchronous
  request is not success; OMES verifies host state before reporting a job
  succeeded.

### 7.5 Privacy and threat-model impact

The design-stage threat model in
[control-center-threat-model.md](control-center-threat-model.md) remains the
authoritative analysis for this boundary; this release did not add a trust
boundary beyond the ones it already covers. Concretely:

- the network-facing boundary is the AWCMS Control Center, which was already
  modelled;
- the node-side boundary is outbound-only, which removes rather than adds
  inbound exposure;
- personal data handling is unchanged — no registrant PII or `.id` document
  workflow is part of this release.

Orchestration visibility (#183) is the one place where agent-derived text
reaches a screen. It is sanitized at the contract: goals and summaries are
bounded to 512 characters and HTML-escaped, and chain-of-thought, raw prompts,
full transcripts, shell commands and credentials are prohibited.

The broader AI data-egress policy (#213–#218) **shipped** in commit `ce44b0a`
(PR #231) — see [ai-data-privacy-and-model-security.md](ai-data-privacy-and-model-security.md),
[docs/threat-model.md](threat-model.md), and [ADR-0029](adr/0029-ai-data-boundary-and-private-inference.md)
for the classification scheme, egress policy, and threat coverage. This
repository now has a machine-readable data-classification and model-egress
policy (`contracts/ai-egress/v1/`, `lib/omes/py/privacy/egress_policy.py`).

### 7.6 Maintaining the integration — agent and contributor rules

Anyone changing this integration must, in addition to `AGENTS.md`:

1. Keep the owning issue in `ahliweb/omes` even when the code lands in
   `ahliweb/awcms`, and link the upstream PR back to the OMES issue.
2. Change a contract in `contracts/control-center/v1/` here first, then
   re-vendor it upstream. AWCMS pins these contracts by SHA-256 and its
   `contracts:omes:sync:check` gate reports `tampered`, `missing` and
   `unpinned` separately — editing the vendored copy is drift, not a fix.
3. Never add an operation to the safe-operation allowlist without also
   updating `operation-request.schema.json`, its fixtures, and the upstream
   byte-for-byte enum test.
4. Never introduce a second approval authority, a second Hermes runtime, or a
   parallel admin GUI framework. Reuse the AWCMS admin shell primitives.
5. Sync `awcms-one/apps/cms` with `git subtree pull --prefix=apps/cms awcms
   main` and a merge commit — never squash or rebase, and never treat the
   subtree as the canonical source.
6. Record PASS/FAIL/BLOCKED honestly. A gate that could not run is BLOCKED
   with the exact command, not omitted.

## 8. Release and changelog metadata

This change adds `changes/202-control-center-closeout.md`. No version bump and
no tag are part of it.

`VERSION` on `main` is still `0.3.0` as of this close-out; released tags are
`v0.1.0`, `v0.2.0`, `v0.3.0`. This pull request does not bump `VERSION`, does
not run `scripts/release.sh`, and does not create a tag.

Versioning is a post-merge step, per
[ADR-0010](adr/0010-versioning-and-change-fragments.md):
`scripts/release.sh` consumes the accumulated `changes/*.md` fragments and
must run on a clean, green `main` in a separate release pull request, after
this close-out (#202) merges. The fragments already accumulated on `main`
since `v0.3.0` — including the Hermes/Graphify/content upstream-delegation
refactors, release provenance and SBOM work, Ubuntu 26.04 and Linux Mint 22.3
compatibility, the Control Center UI/UX design system (#200), the pull-worker
transport (#192), the Hermes orchestration visualization (#183), the
prototype redesign v2 (#211), the v1 worker contract fix (#221), the AI
data-privacy boundary chain (#213–#218), and this close-out (#202) — include
new user-visible capabilities rather than only fixes, so `scripts/release.sh
0.4.0` is the expected next release. That command is run by the separate
release pull request, not by this one.

## 9. Related documents

- [control-center-and-integrations.md](control-center-and-integrations.md) — Control Center boundary and delivery status
- [control-center-contracts.md](control-center-contracts.md) — versioned wire contracts
- [control-center-foundation.md](control-center-foundation.md) — what OMES provides versus the web implementation
- [control-center-threat-model.md](control-center-threat-model.md) — design-stage threat model
- [ui-ux-design-system.md](ui-ux-design-system.md) — UI/UX baseline and screen architecture
- [jobs.md](jobs.md) — idempotent audited job contract
- [disaster-recovery.md](disaster-recovery.md), [rollback.md](rollback.md) — local recovery paths
- [ADR-0011](adr/0011-control-center-and-provider-boundaries.md), [ADR-0017](adr/0017-upstream-first-ownership-and-boundary-enforcement.md), [ADR-0023](adr/0023-control-center-ui-ux-design-system.md), [ADR-0027](adr/0027-control-center-pull-worker-transport.md), [ADR-0028](adr/0028-hermes-orchestration-visualization.md)
