# ADR-0022: Consume Hermes-Native Health Endpoints and Retain OMES Host Aggregation

- Status: Accepted
- Date: 2026-09-21
- Related issues: [#178](https://github.com/ahliweb/omes/issues/178), [#171](https://github.com/ahliweb/omes/issues/171), [#174](https://github.com/ahliweb/omes/issues/174), [#175](https://github.com/ahliweb/omes/issues/175), [#177](https://github.com/ahliweb/omes/issues/177), [#79](https://github.com/ahliweb/omes/issues/79)

## Context

Prior to this decision, OMES independently evaluated Hermes runtime, provider, and channel health through ad-hoc subprocess probes and direct inspections of Hermes configuration/environment files (`.env`, internal file layouts) in `lib/omes/py/health/hermes.py`. While effective as an initial bootstrap, this violated core architectural principles:
1. **Upstream Ownership (ADR-0017)**: Upstream Hermes Agent (`v2026.9.14`) provides first-class, authoritative runtime self-diagnostics via `hermes doctor`, profile/gateway lifecycle commands (`hermes gateway status`, `hermes profile list`), and runtime-owned model/provider error classification. OMES should not reimplement Hermes runtime evaluation.
2. **Secret and Data Coupling**: Directly inspecting internal Hermes `.env` or configuration paths risks secret leakage and couples OMES to internal file layouts.
3. **Ambiguous Signal Attribution**: Health reports did not distinguish whether a pass/fail signal originated from Hermes runtime diagnostics, OMES host-level checks (systemd units, resource quotas, container status), or external providers (Ollama, Telegram).

## Evaluation of Architectural Options

### Option 1: Complete Delegation to Hermes Doctor (OMES does no health checking)
- **Pros**: Zero duplicated logic; all health reports come from upstream.
- **Cons**: Severe security and reliability regression. Hermes doctor cannot evaluate host-level infrastructure concerns: systemd unit state, cgroup memory/disk thresholds, unauthenticated network exposure (ports bound to `0.0.0.0` vs loopback), provenance tampering, or OMES configuration drift. If Hermes fails to start entirely, doctor cannot report host causes.

### Option 2: Full OMES Independent Inspection (OMES continues inspecting internal Hermes files)
- **Pros**: Full control within OMES codebase.
- **Cons**: High maintenance; brittle coupling to internal Hermes layouts; duplicates runtime validation; violates the mandatory Upstream-First hierarchy (`DELEGATE -> PORT -> ADAPT -> DEFER -> REJECT`).

### Option 3: Two-Layer Architecture with Source Attribution and Bounded Fallback (Chosen)
- **Pros**:
  - **Crisp Authority Separation**:
    - **Hermes Runtime Authority** (`authority: "hermes"`): Authoritative for agent runtime semantics (`hermes doctor`, `hermes gateway status`, `hermes profile list`).
    - **OMES Host Authority** (`authority: "omes-host"`): Authoritative for infrastructure semantics (systemd unit presence/state, container lifecycle via compose, resource limits for disk/memory, loopback network exposure via `exposure.py`, provenance, drift).
    - **External Provider Authority** (`authority: "external-provider"`): Authoritative for provider reachability (Ollama model status, Telegram token validity).
  - **Versioned Adapter**: `lib/omes/py/health/hermes_adapter.py` encapsulates read-only, fixed-argv CLI commands with bounded timeouts, capped error output, and zero shell execution.
  - **Source Attribution & Fail-Closed**: Every layer explicitly reports `authority`, `source`, `compatibility_fallback`, and optional `removal_trigger`. Hermes timeouts or errors report as `fail` (never guessed healthy).
- **11-Criteria Evaluation**:
  1. **Advantages and Disadvantages**: Eliminates duplicated runtime logic while keeping OMES's vital host hardening and resource gates intact.
  2. **Security**: Strongest security posture: no raw secrets read or logged; fixed argv execution; unauthenticated network listener checks remain strictly owned by OMES.
  3. **Performance**: Bounded execution timeouts (default 10s); eliminates redundant file scanning and unnecessary network probes.
  4. **Maintainability**: Low maintenance; delegates internal Hermes diagnostics to upstream `hermes doctor` across updates.
  5. **Scalability**: Seamlessly handles standalone and multiplexed profile topologies via `hermes profile list`.
  6. **Accessibility**: Machine-readable JSON output conforms strictly to `health-readiness.response.schema.json`.
  7. **SEO & Web Impact**: Provides reliable telemetry for Control Center dashboards without false green states.
  8. **UI/UX Implications**: Control Center operators clearly see who owns each signal (`authority: hermes` vs `authority: omes-host`).
  9. **Compatibility**: Fully backward compatible with `lib/omes/py/health/model.py` and `build_result` consumers.
  10. **Operational Complexity**: Low; single diagnostic entry point (`omes health` / `omes agent health`).
  11. **Long-term Technical Implications & Upstream-First (ADR-0017)**: Satisfies `removal_trigger` for capability `hermes.agent.health` in `architecture/capabilities.json`.

## Decision

1. **Versioned Hermes Health Adapter**:
   - Implemented in `lib/omes/py/health/hermes_adapter.py`.
   - Executes fixed read-only commands (`hermes --version`, `hermes doctor`, `hermes gateway status`, `hermes profile list`).
   - Forbids shell execution; enforces strict timeout bounding and capped error strings.
2. **Data Model and Source Attribution**:
   - `lib/omes/py/health/model.py` and `contracts/control-center/v1/health-readiness.response.schema.json` declare optional `authority` (`"hermes" | "omes-host" | "external-provider"`), `source`, `compatibility_fallback` (boolean), and `removal_trigger`.
3. **Layer Integration**:
   - `lib/omes/py/health/hermes.py` delegates `check_runtime` and gateway reachability to `hermes_adapter`.
   - Host infrastructure checks (`check_host`, systemd unit active/enabled) remain authored by `omes-host`.
   - Container checks in `lib/omes/py/agent/compose_health.py` and systemd agent checks in `lib/omes/py/agent/health.py` emit explicit authority and source attribution.
4. **Capability Map**:
   - `architecture/capabilities.json` updates `hermes.agent.health`: `duplication_allowed: false`, `adr_reference: ADR-0022`, `removal_trigger: null`.
