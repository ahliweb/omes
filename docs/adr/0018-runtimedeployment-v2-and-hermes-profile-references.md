# ADR-0018: RuntimeDeployment v2 Contract and Hermes Profile References

- Status: Accepted
- Date: 2026-09-21
- Related issues: [#174](https://github.com/ahliweb/omes/issues/174), [#175](https://github.com/ahliweb/omes/issues/175), [#176](https://github.com/ahliweb/omes/issues/176), [#177](https://github.com/ahliweb/omes/issues/177)

## Context

In OMES `AgentDeployment` v1 (`omes.ahliweb.com/v1`), host placement concerns were conflated with agent runtime intelligence concerns. Specifically, v1 manifests included:
- `spec.role`: persona, prompt instructions, and bot behavior;
- `spec.capabilities`: tool allowlists and MCP skills;
- `spec.deny`: tool execution restrictions and approval rules;
- `spec.storage`: internal session, memory, and skill persistence mechanisms;
- `spec.secrets`: secret reference lists linked to host environment files.

In upstream Hermes Agent (`v2026.9.14`), agent intelligence, tools, skills, persona instructions, memory backend, and API credentials are natively encapsulated within Hermes Profiles (`hermes profile list`, `hermes profile create`, `hermes profile show`).

By modeling agent runtime roles and tool capabilities in the OMES manifest, OMES introduced capability duplication and conflicting authorities, violating ADR-0017 (`DELEGATE -> PORT -> ADAPT -> DEFER -> REJECT`). Furthermore, storing secret names and storage semantics inside OMES manifests created unnecessary coupling between host lifecycle management and agent configuration.

## Evaluation of Architectural Options

### Option 1: Continue extending v1 manifest with optional Hermes flags
- **Pros**: Zero migration required for existing v1 manifests.
- **Cons**: Perpetuates boundary confusion; leaves role/capability/secret smuggling unaddressed; prevents clean upstream delegation; increases tech debt.
- **Security**: Weak; secrets and runtime tool permissions remain duplicated across OMES manifests and Hermes configurations.
- **Maintenance**: Poor; requires maintaining dual configurations that can drift out of sync.

### Option 2: Immediate breaking removal of v1 manifests
- **Pros**: Clean transition immediately.
- **Cons**: High blast radius; breaks deployed agents and existing workflows before operators have migrated manifests.
- **Operational Complexity**: High; unacceptable disruption for running host agents.

### Option 3: Introduce versioned RuntimeDeployment v2 contract with multi-version loading and automated migration tooling (Chosen)
- **Pros**:
  - Clear separation of concerns: OMES strictly owns host placement and resource quotas; Hermes strictly owns agent runtime profiles and behavior.
  - Strict JSON Schema validation prevents smuggling runtime roles, capabilities, storage semantics, or secrets into v2.
  - Multi-version manifest loader supports v1 and v2 deployments side-by-side during the transition window.
  - Preflight verification validates `profileRef` against `hermes profile list` before host mutation.
  - Automated migration engine (`omes agent migrate`) produces audit reports and safely converts manifests with in-place backups or preview options.
- **Security**: Enforces least privilege; OMES never handles agent API keys or internal database schemas; security hardening and cgroup limits remain strictly enforced at host level.
- **Performance**: Zero runtime overhead; plan compilation is pure and fast; profile resolution happens via read-only Hermes CLI invocation at preflight.
- **Maintainability**: Establishes crisp module contracts aligned with upstream Hermes design.
- **Scalability**: Decouples host service scaling from agent persona definitions; multiple deployments can reference shared or distinct Hermes profiles.
- **Portability**: Preserves cross-platform compatibility across Ubuntu 24.04/26.04 and Linux Mint 22.x without host-specific runtime quirks.
- **Testability**: Fully testable through unit tests, schema fixtures, and hermes CLI shims.
- **Operational Complexity**: Low; operators can preview migrations (`--dry-run`) or migrate individual agents incrementally.
- **Upgrade Agility**: Allows upstream Hermes to evolve its profile and bot capabilities without requiring changes to OMES deployment schemas.
- **Long-term Implications**: Paves the way for issue #175 (lifecycle delegation) and issue #176 (hermes doctor diagnostics).

## Decision

1. **New Versioned Contract (`RuntimeDeployment` v2)**:
   - Schema defined at `contracts/agent/v2/runtime-deployment.schema.json` under `apiVersion: omes.ahliweb.com/v2` and `kind: RuntimeDeployment`.
   - Replaces `spec.profile` with `runtime.profileRef`.
   - Groups host placement under `placement` (`backend`, `serviceScope`, `restartPolicy`).
   - Retains cgroup resource limits under `resources` (`memory`, `cpu`, `pids`).
   - Retains host security policies under `security` (`hardeningProfile`, `exposurePolicy`, `isolationClass`).
   - Groups failure recovery under `recovery` (`policy`).
   - Strictly prohibits `role`, `capabilities`, `deny`, `storage`, and `secrets` via `additionalProperties: false`.

2. **Upstream Profile Resolution**:
   - During `check` and `apply`, OMES resolves `runtime.profileRef` via `hermes profile list`.
   - If the referenced profile does not exist, preflight fails closed before making any host mutations.

3. **Multi-Version Runtime Coexistence**:
   - `lib/omes/py/agent/manifest.py` dynamically validates manifests against their declared `apiVersion` (v1 or v2).
   - `omes agent doctor` reports status for both v1 and v2 deployments seamlessly.

4. **Safe Migration Tooling**:
   - Implemented `lib/omes/py/agent/migration.py` and CLI subcommand `omes agent migrate <name>`.
   - Classifies each v1 field into `MIGRATE`, `DELEGATE`, `DROP`, or `BLOCKED`.
   - Supports `--dry-run`, `--output <path>`, and in-place migration with automatic `.json.bak` backup.

## Consequences

- The contract boundary between OMES host management and Hermes agent configuration is cleanly separated.
- Follow-up issues (#175 for native lifecycle, #176 for doctor diagnostics, and #177 for state management) can build directly on the v2 profile reference model.
- Existing v1 manifests continue to function without immediate breakage, while new deployments adopt v2.
