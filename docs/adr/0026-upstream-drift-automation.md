# ADR-0026 — Automated Upstream Drift Review and Deprecation Tracking

- **Status:** Accepted
- **Date:** 2026-09-21
- **Decision maker:** @ahliweb
- **Related:** ADR-0017 (Upstream-first ownership and boundary enforcement), ADR-0020 (Hermes backup and import boundaries), ADR-0025 (Graphify upstream delegation), [docs/architecture.md](../architecture.md), [docs/business/release-gates.md](../business/release-gates.md), [Issue #181](https://github.com/ahliweb/omes/issues/181)
- **Supersedes / Amends:** Operationalizes ADR-0017's upstream-first precedence (`DELEGATE -> PORT -> ADAPT -> DEFER -> REJECT`) with continuous, automated drift detection and deprecation tracking across upstream projects.

---

## Context

Under ADR-0017, OMES establishes an explicit upstream-first precedence hierarchy:
```text
DELEGATE -> PORT -> ADAPT -> DEFER -> REJECT
```
All capabilities and module boundaries are cataloged in `architecture/capabilities.json`. However, upstream projects evolve rapidly:
1. **Hermes Agent** (`NousResearch/hermes-agent`) frequently releases enhancements (gateway services, multi-profile isolation, Bot Mode, delegation patterns, subagent hooks).
2. **Graphify** (`Graphify-Labs/graphify`, PyPI `graphifyy`) recently shipped native Hermes skill support (`graphify install --platform hermes`), native MCP extras, and moved from baseline `0.9.64` to `0.9.65`.
3. **Omarchy** (`omacom/omarchy`) updates themes, shell enhancements, and update channels.
4. **Cloudflare, SRS-X, Coolify, and AWCMS** evolve provider APIs and management planes.

Manual periodic reviews alone are prone to omission and latency. When an upstream project releases a capability that OMES previously duplicated or adapted, OMES must recognize the opportunity to **DELEGATE** or **PORT**, deprecating temporary OMES wrapper code according to documented removal triggers.

Crucially, this upstream drift tracking process must:
- Be strictly **read-only** by default;
- Never automatically change capability dispositions, compatibility tiers, or installer baselines;
- Never automatically merge, push, or mutate host files or production state;
- Distinguish between released, supported baselines and candidate changes observed only on `main`;
- Detect license and package provenance modifications;
- Safely report `BLOCKED` / `WARN` on API or network failures rather than silently assuming no changes;
- Deduplicate notifications into a single tracked review issue rather than creating notification spam.

---

## Evaluation of Options (11-Criteria Matrix)

We evaluated two architectural approaches:
- **Option A (Ad-Hoc Manual Review & Static Baselines):** Maintain current practice of updating baselines only when maintainers notice upstream releases or when issues are manually opened.
- **Option B (Deterministic, Read-Only Automated Drift Engine with Deduplicated Evidence Tracking - Recommended):** Implement a scheduled, deterministic drift audit script (`scripts/upstream-drift.py` and `lib/omes/py/architecture/drift.py`) comparing public upstream release metadata and candidate changes against `architecture/capabilities.json`, classifying findings with standard taxonomies, and managing a single deduplicated tracking issue.

| Criterion | Option A (Manual Review) | Option B (Automated Drift Engine) |
|---|---|---|
| **1. Advantages & Disadvantages** | Pro: No new tooling. Con: High risk of silent drift, delayed upstream feature adoption, stale wrapper code remains indefinitely. | Pro: Proactive drift detection, transparent classification, automatic deduplication, explicit removal trigger surfacing. Con: Requires maintaining upstream metadata parsers. |
| **2. Security** | Relies on manual maintainer inspection of upstream security advisories and license changes. | High: Strictly read-only; no credentials required for audit; detects license changes and package provenance/URL mutations; safe fail-closed error handling (`BLOCKED`/`WARN`). |
| **3. Performance** | Manual overhead and developer cognitive load. | Negligible: Lightweight metadata queries against public GitHub/PyPI APIs with local caching and offline/fixture test support. |
| **4. Maintainability** | High maintenance cost over time as wrappers diverge from upstream. | High: Accelerates deprecation of redundant code by surfacing upstream delegation opportunities early; maintains clean capability boundaries. |
| **5. Scalability** | Fails to scale across multiple upstreams (Hermes, Omarchy, Graphify, Coolify, Cloudflare, SRS-X). | Scales cleanly: New upstreams are registered in `capabilities.json` and scanned systematically without code changes. |
| **6. Accessibility** | Review findings buried in chat, commit logs, or ad-hoc notes. | Structured findings available via `--json`, readable CLI summary tables, and clear GitHub tracking issue formatting. |
| **7. SEO Impact** | Neutral. | Neutral. |
| **8. UI/UX Implications** | Inconsistent operator awareness of new upstream capabilities. | Predictable maintainer workflow: deterministic CLI commands (`scripts/upstream-drift.py --check`) integrated into release gates and CI. |
| **9. Compatibility** | No effect on existing runtime compatibility. | Zero runtime footprint: offline-safe, decoupled from host installation and agent runtime. |
| **10. Operational Complexity** | High cognitive overhead tracking multiple upstream release feeds. | Low: A single GitHub Actions workflow runs weekly or on dispatch, updating one deduplicated issue only when actionable drift exists. |
| **11. Long-Term Implications** | OMES risks becoming a stagnant set of obsolete wrappers around fast-moving upstream tools. | Keeps OMES lean, current, and tightly aligned with upstream Hermes and ecosystem standards. |

---

## Decision

We adopt **Option B**:
1. **Core Engine (`lib/omes/py/architecture/drift.py`):**
   - Implements pure-Python upstream metadata comparison and finding classification.
   - Classification taxonomy:
     - `NO_IMPACT`: Observed upstream release matches supported baseline exactly.
     - `NEW_DELEGATE_CANDIDATE`: Upstream introduced or candidate-tagged a capability that could replace an OMES implementation.
     - `PORT_ADAPT_REVIEW`: Upstream changed architecture or portable patterns requiring OMES review.
     - `BREAKING_CHANGE`: Upstream altered public APIs, CLI flags, or schema definitions used by OMES.
     - `SECURITY_OR_LICENSE_REVIEW`: Upstream modified SPDX license, license file hash, or installer provenance.
     - `BASELINE_UPDATE_AVAILABLE`: A newer stable release exists above the pinned supported baseline.
2. **Read-Only Safety & Non-Mutation Rules:**
   - The engine never auto-ports, auto-merges, auto-upgrades, or mutates repository code or production systems.
   - Upstream scripts and installers are never executed during discovery (no `curl | bash`).
   - Observations on `main` / `master` are strictly categorized as `upstream_main_candidate` and are never promoted to `released_supported` without explicit maintainer verification.
3. **Resilience & Fail-Closed Safety:**
   - When upstream APIs (GitHub API rate limits, PyPI outages, network failure) fail or return ambiguous responses, the engine reports `BLOCKED` or `WARN`. It never assumes "no drift" on failure.
4. **Deduplicated Maintainer Notification:**
   - When executed with issue update capability, the tool manages at most **one** open tracking issue labeled `area:upstream-drift` titled `[upstream-drift] Upstream capability drift and deprecation review`.
   - Existing open issues are updated with fresh diffs rather than creating weekly duplicate spam.
   - If all drift is resolved (`NO_IMPACT`), the tool notes resolution without noise.
5. **Release Gate Enforcement:**
   - Incorporate `scripts/upstream-drift.py --check` into release gates (`docs/business/release-gates.md`) ensuring all upstream drift is triaged prior to release.
