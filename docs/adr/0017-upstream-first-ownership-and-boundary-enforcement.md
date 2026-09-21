# ADR-0017: Upstream-first ownership and architecture boundary enforcement

- Status: Accepted
- Date: 2026-09-21
- Related issues: [#171](https://github.com/ahliweb/omes/issues/171), [#174](https://github.com/ahliweb/omes/issues/174), [#175](https://github.com/ahliweb/omes/issues/175), [#176](https://github.com/ahliweb/omes/issues/176), [#177](https://github.com/ahliweb/omes/issues/177), [#178](https://github.com/ahliweb/omes/issues/178), [#179](https://github.com/ahliweb/omes/issues/179), [#180](https://github.com/ahliweb/omes/issues/180), [#181](https://github.com/ahliweb/omes/issues/181)

## Context

As OMES evolved alongside upstream projects—specifically Hermes Agent (`v2026.9.14`), Omarchy (`v4.0.4`), Graphify (`0.9.64`), and AWCMS Control Center—semantic duplication and unauthorized runtime coupling accumulated. In several instances, OMES modeled concepts or implemented mechanisms already natively owned by upstream projects (e.g., agent unit generation, internal Hermes backup structures, and domain content pipelines).

Without an explicit, machine-checkable architectural contract and decision hierarchy, repository components risk blurring trust boundaries, creating maintenance drift, and maintaining duplicate implementations.

## Architectural Precedence

For every feature or capability considered in OMES, the following decision hierarchy is strictly enforced:

```text
DELEGATE -> PORT -> ADAPT -> DEFER -> REJECT
```

1. **DELEGATE**: If the supported upstream project (e.g. Hermes Agent, Graphify, Cloudflare) already owns the capability and provides a supported CLI or API interface, OMES delegates to it directly and prohibits reimplementation.
2. **PORT**: If an upstream component (such as an Omarchy configuration pattern or shell utility) is portable across distributions, reuse it directly with only packaging/integration adaptations.
3. **ADAPT**: If only the upstream concept/policy is applicable, adapt it natively to OMES supported target platforms (Ubuntu 24.04/26.04 and Linux Mint 22.x).
4. **DEFER**: If a capability is desirable but not yet justified, lacks stable upstream interfaces, or is an unverified candidate on upstream `main`, defer it.
5. **REJECT**: If a capability contradicts OMES scope, host security model, minimal privileges, or platform boundaries, reject it explicitly.

## Evaluation of Options

### Option 1: Ad-hoc documentation conventions without CI enforcement
- **Pros**: Low initial implementation effort.
- **Cons**: Prone to architectural regression; lacks machine verification; leads to silent duplicate implementations.
- **Security**: Weak; does not prevent unauthorized coupling or privilege leakage.
- **Maintenance**: Poor; requires constant manual vigilance during code review.

### Option 2: Full rewrite and forced immediate deprecation of all overlapping code
- **Pros**: Immediate clean state.
- **Cons**: High blast radius; breaks existing installations and workflows before downstream migration issues (#174–#181) land.
- **Security**: Risky; introduces unstable churn across all subsystems concurrently.
- **Operational Complexity**: Unacceptable for production stability.

### Option 3: Machine-checkable capability registry, boundary guard, and staged migration contracts (Chosen)
- **Pros**:
  - Provides a single machine-readable source of truth (`architecture/capabilities.json`) validated by JSON Schema.
  - CI automated enforcement prevents forbidden layer imports and runtime coupling.
  - Strict rules require any temporary duplication to declare an ADR reference and an explicit removal trigger tied to follow-up issues.
  - Distinguishes verified release baselines (`released_supported`) from upstream development branches (`upstream_main_candidate`).
- **Security**: Enforces default-deny on cross-boundary dependencies and prevents host tampering with private agent runtime state.
- **Performance**: Zero runtime overhead; boundary checks execute purely at CI/test time via AST inspection.
- **Maintainability**: Clear upstream ownership and explicit tracking of migration blockers.
- **Scalability**: Seamlessly scales as new upstream components, providers, or agent backends are integrated.
- **Portability**: Preserves target OS boundaries across Ubuntu and Linux Mint without Arch-specific coupling.
- **Testability**: 100% testable via unit tests, AST parsers, and schema fixture checks.
- **Operational Complexity**: Minimal; developers receive clear linting errors on architectural violations.
- **Upgrade Agility**: Keeps OMES synchronized with upstream releases without breaking changes.
- **Long-term Implications**: Establishes durable boundaries for OMES as the host deployment and assurance engine, Hermes as the agent runtime, Omarchy as desktop patterns, and AWCMS as business control plane.

## Decision

1. **Machine-Readable Registry**: Establish `architecture/capabilities.json` governed by `contracts/architecture/v1/capabilities.schema.json`.
2. **Boundary Enforcement**:
   - OMES core modules (`agent`, `jobs`, `health`, `provenance`, `architecture`) must never import commercial or domain workflow modules (`content`, `domains`).
   - OMES must not directly query or manipulate private Hermes runtime databases (e.g. `messages.db` or `.hermes/` private sqlite databases); communication must occur through supported CLI or API contracts.
   - Upstream features observed only on `main` cannot be marked as `released_supported`.
   - Temporary duplication requires `duplication_allowed: true`, a valid `adr_reference`, and a non-empty `removal_trigger`.
3. **Automated CI Guard**: Run `scripts/check-architecture.py` across `tests/run.sh`, `scripts/lint.sh`, and GitHub Actions lint workflows.

## Consequences

- The capability boundaries of OMES, Hermes, Omarchy, Graphify, and AWCMS are codified and verifiable.
- Follow-up issues ([#174](https://github.com/ahliweb/omes/issues/174)–[#181](https://github.com/ahliweb/omes/issues/181)) own specific migration implementations without cross-contamination.
- Developers and agents cannot introduce unauthorized coupling or bypass the decision tree.
