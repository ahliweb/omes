# ADR-0036 — Keep OMES and OMES-Web as separate repositories with canonical, versioned integration

- **Status:** Proposed (Option A selected by product owner; acceptance requires code/contracts review)
- **Date:** 2026-10-24
- **Decision owner:** @ahliweb
- **Issue:** [OMES #350](https://github.com/ahliweb/omes/issues/350)
- **Related:** OMES ADR-0017, ADR-0023, ADR-0027, OMES [#352](https://github.com/ahliweb/omes/issues/352), OMES-Web [#16](https://github.com/ahliweb/omes-web/issues/16), [#17](https://github.com/ahliweb/omes-web/issues/17), [#18](https://github.com/ahliweb/omes-web/issues/18), [#19](https://github.com/ahliweb/omes-web/issues/19), [#20](https://github.com/ahliweb/omes-web/issues/20)
- **Decision applies to:** ahliweb/omes, ahliweb/omes-web, ahliweb/awcms, ahliweb/awcms-one and ahliweb/awcms-astro

## 1. Audited current state (source-of-truth boundaries)

1. Native **OMES** main currently declares `VERSION=0.6.0` and ships CLI/host preflight, Bash/Python/systemd services, signed outbound-only HTTPS pull-worker, versioned `contracts/control-center/v1`, Hermes delegation and a **design-only** `ui/control-center/index.html` prototype. The released [v0.6.0](https://github.com/ahliweb/omes/releases/tag/v0.6.0) exists. OMES is **not** an Astro/Bun monorepo and must not gain the AWCMS database or its own duplicate tenant/admin runtime.
2. **AWCMS** main implements the canonical `src/modules/omes-control`, tenant/RBAC/ABAC/RLS, API, worker endpoints and **15 route files** under `src/pages/admin/omes/`. Upstream CMS is the product's system of record and sole SYSTEM-admin implementation; not a code copy to maintain in OMES.
3. **OMES-Web** main is an independently deployed **Bun/Astro `landing` product plus AWCMS git subtree at `apps/cms`**. It already references `omes.ahlikoding.com` and `omes-cms.ahlikoding.com` but has template-derived governance/compose identifiers, root `package.json.repository.url=.../GANTI-ORG/omes.git`, pinned Bun 1.4.2 and static marketing copy/install command hardcoded to OMES v0.4.0. Production status cannot be inferred from GitHub alone.
4. OMES-Web subtree sync PR [#14](https://github.com/ahliweb/omes-web/pull/14) merged upstream AWCMS `8e9bce0c` on 2026-09-28; upstream has progressed. Safe upgrade requires the product repo's own migration/testing plan.
5. **AWCMS-One** is an upstream-derived *template*, not a shared runtime dependency or system-of-record for OMES-Web. **AWCMS-Astro** offers independent public/USER-admin Astro patterns, not the OMES SYSTEM Control Center.

## 2. Options assessed

| Criterion | A — Separate repos + verified contracts | B — Move only OMES public site into native OMES | C — Merge OMES-Web monorepo entirely into OMES |
|---|---|---|---|
| Correctness and ownership | **High:** AWCMS owns admin; OMES owns hosts; product deploy owns site | Medium: front/backend deploy split needs migration | Low: layers blurred, false single authority |
| Security/tenant privacy | **High:** FORCE RLS + outbound signed HTTPS, least privilege | Medium if CMS separately retained | High risk of mixing root host work with browser/DB secrets |
| Maintainability | **High:** upstream-first fixes, distinct release cycles | Medium: mixed language/CI in OMES | Low: entire AWCMS subtree in OMES, duplicate trees |
| Compatibility/version risk | **Manageable:** explicit manifests/canary gates | Medium/high: website move, CDN/SEO changes | **High:** histories, package/DB/CLI version coupling |
| Cost/operational complexity | **Moderate:** two builds + periodic sync | Medium: fewer repos but mixed pipelines | High: large CI, brittle release graph |
| Performance, SEO/accessibility | Retains Astro `landing` static build | Requires site migration/proxy regression | Requires wider build/route rewrites |
| Reliability/rollback | **Independent:** host vs CMS vs site rollback | More coupled site/native release | Blast radius extends host CLI and CMS |
| Technical debt | Reduce by eliminating duplicate docs/release metadata | Can shift duplication elsewhere | Adds coupled stacks without replacing canonical AWCMS |

**Chosen Option A.** An actual repository merge may only be reconsidered after a new ADR with measured benefits, full-history/subtree integrity migration, live-traffic plan, contract conformance, restore drill and rollback. Do not merge because there are two repos.

## 3. Canonical ownership matrix (normative target)

| Capability | Owner | OMES-Web role | Forbidden duplicate |
|---|---|---|---|
| Host installation, hardening, health, backup/restore, rollback, signed pull-worker | **OMES** | Present status through supported Control Center API | New host executor, arbitrary SSH or inbound privileged WS in website |
| Agent reasoning, sessions, models, skills, delegation and native channels | **Hermes Agent** | Policy-governed projection only | Independent Hermes/AI runtime, replicated memory DB |
| Multi-tenant identity, RBAC/ABAC/FORCE RLS, operator API, admin Astro `/admin/omes/*` | **AWCMS** | Consume via reviewed full-history `apps/cms` git subtree | `omes-web` fork of canonical admin or new `omes` web backend |
| Published release manifest/installer/worker contract | **OMES release authority** | Pin verified published tag metadata at SSG build; no duplicate hard-coded version | Site-local competing `OMES_VERSION` source or unsafe floating `main` installer |
| OMES public site, product themes, domain routing, deployed CMS DB/identity, static illustration | **OMES-Web** | Own it completely | Website shipping in native CLI to avoid its own deploy |
| Generic template neutral seeds/build profiles | **AWCMS-One** | Adopt released improvements selectively and validate derived app | Live dependency on `awcms-one/main` |
| Generic public/USER-admin Astro patterns | **AWCMS-Astro** | Optional pattern reference, never required runtime | SYSTEM Control Center implemented here |

## 4. Contract/version handshakes

1. OMES tagged release publishes **verifiable, non-secret metadata**: tag/version, release published timestamp, trusted installer URI + checksum/provenance where actually available, worker protocol/schema version and digest, minimum/maximum supported CMS `omes_control` contract range or explicit tested combinations. Do not invent support for unverified releases or tag assets; manifest format begins in [OMES #352](https://github.com/ahliweb/omes/issues/352).
2. AWCMS consumes pinned published **contract schemas and test fixtures** without importing OMES host internals. `omes_control` endpoints enforce signed enrollment/poll/result/heartbeat, nonce/replay checks, per-tenant server IDs, idempotency/least privilege and fail closed on unknown schema/major version. Compatibility test tracked in [AWCMS compatibility issue](https://github.com/ahliweb/awcms/issues/1185).
3. OMES-Web deploy preflight reads **pinned** OMES release metadata and the `apps/cms` embedded AWCMS version; validates server schema, supported worker release, tenant role/RLS database migrations and independent CMS/image versions using synthetic fixtures. Gate tracked [#20](https://github.com/ahliweb/omes-web/issues/20). Client bundle must not contain OMES worker credentials, API keys or host secrets.
4. Released **OMES v0.6.0** and an observed `omes-web v0.1.0` are different version streams; do not conflate. `omes-web` must not default to the unreviewed newest `omes/main`. Pin exact version/tag/digest, compare and intentionally promote, with staging/rollback.
5. Incompatible worker/API: reject execution, show **unsupported/unknown** with correlation and actionable upgrade instructions; do not pretend healthy or silently downgrade. Signed worker **initiates outbound HTTPS only**, no privileged inbound WebSocket or SSH route.
6. SSE/WS for future **Bun AWCMS browser status** may be admitted upstream only; not a replacement for signed host polling or provider broadcast delivery.

## 5. Source synchronization and deployment responsibilities

- Changes to canonical `omes_control`, migrations, OpenAPI, DTO or UI must land in **AWCMS** upstream first. OMES-Web updates `apps/cms` through **`git subtree pull --prefix=apps/cms awcms main` using a MERGE COMMIT, never squash/rebase**, with main-only refspec, `--no-tags` and no imported upstream tags. Review all migration/RLS/authorization/security/code conflicts, and run full Bun/DB gates.
- Native OMES host runtime releases and OMES-Web frontend/CMS deployments are **independent**; no requirement to merge repository histories. Product `landing` build is static-first and Bun-only, with no extra Node/AI worker, and controls are inherited from AWCMS. Keep `toko/berita` template profile validation in the product CI where defined.
- Default product deployment source remains OMES-Web `main` **only after staging/canary checks**, with pinned artefact digests and distinct owner and worker DB roles. Do not rewrite/rename existing `awcms-one-production` Compose project or persistent volume labels without a separate stateful migration+restore decision.
- Public website release chip, installation instructions and CTA consume reviewed **released** OMES metadata; actual control-plane pages are only linked to their CMS origin. OMES-Web homepage's Control Center diagrams remain explicitly **synthetic illustrations**.

## 6. Anti-duplication, privacy and UX checklist

- Functional SYSTEM admin UI renders in **AWCMS's existing Astro admin shell**, with existing theme/tokens, tenant/role guards, audited typed actions; native OMES HTML prototype is **reference-only**. OMES-Web public marketing design illustrates capability with "sample data" labels, not a second admin login implementation.
- User journeys: register/verify worker → observe host heartbeat → request typed job → approval when required → outbound host poll → reconcile evidence → audit; observe Hermes delegated tasks without replacing Hermes; diagnose stale/disconnected/unknown without claiming success.
- Do not leak raw host keys, private prompts, session transcripts, worker credential material, shell command arguments, PII or cross-tenant identities. Verify Origin/session/CSP and avoid privileged browser-to-host communication.
- UX owned in [OMES-Web #18](https://github.com/ahliweb/omes-web/issues/18), upstream AI design work in [OMES #343](https://github.com/ahliweb/omes/issues/343)/[#344](https://github.com/ahliweb/omes/issues/344), 3D future [#263](https://github.com/ahliweb/omes/issues/263) and revised [OMES #348](https://github.com/ahliweb/omes/issues/348). No duplicate issue for already planned functionality.

## 7. Rollout, acceptance and reversal

0. **Document/ADR review** with product owner, AWCMS owner, security and operations; keep this ADR **Proposed** until accepted and checks prove the contract.
1. Fix misleading release copy from v0.4.0 and ensure pin from official release [OMES-Web #16](https://github.com/ahliweb/omes-web/issues/16), update root product governance and repository URLs [#19](https://github.com/ahliweb/omes-web/issues/19).
2. Inventory all upstream changes since OMES-Web's sync point, perform tested full-history CMS subtree sync **merge only** [#17](https://github.com/ahliweb/omes-web/issues/17). Validate PostgreSQL migrations, RLS, least privilege, all profiles and auth.
3. Release typed OMES contracts/fixtures [OMES #352](https://github.com/ahliweb/omes/issues/352); AWCMS verify consumer [#1185](https://github.com/ahliweb/awcms/issues/1185), OMES-Web deploy compatibility gate [#20](https://github.com/ahliweb/omes-web/issues/20).
4. Document/test admin UI/UX current vs planned [#18](https://github.com/ahliweb/omes-web/issues/18), preserve a11y/SEO/session confidentiality, run local CI and DB-backed tests where relevant, stage new versions and promote with rollback.
5. Confirm independent rollback: OMES worker release, OMES-Web CMS API/database and storefront have separate rollback paths with DB migrations forward-only. If any contract/regression gate fails, hold deployment instead of merging repos.

**Decision success criteria:** zero duplicate implementation of Control Center/host runner/agent runtime, reproducible cross-release compatibility proof, accurate storefront published OMES version, verified Git subtree ancestry, safe independent deployment and recovery, and all docs/issues linked to actual repository owner.
