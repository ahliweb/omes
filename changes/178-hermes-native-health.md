---
issue: 178
type: refactor
summary: consume native Hermes health endpoints and doctor diagnostics while retaining OMES host aggregation
---

### Summary of changes

1. **Native Hermes Health Adapter**:
   - Implemented `lib/omes/py/health/hermes_adapter.py` providing bounded, read-only delegation to `hermes --version`, `hermes doctor`, `hermes gateway status`, and `hermes profile list`.
   - Adheres to ADR-0012 (standard library only) and ADR-0017 (Upstream-First Delegation).
   - Guarantees fail-closed error handling: Hermes timeouts and diagnostic failures report as `fail` and are never guessed healthy.
2. **Two-Layer Health Attribution**:
   - Extended `lib/omes/py/health/model.py` and `contracts/control-center/v1/health-readiness.response.schema.json` with `authority` (`hermes`, `omes-host`, `external-provider`), `source`, `compatibility_fallback`, and `removal_trigger`.
   - `lib/omes/py/health/hermes.py` delegates runtime evaluation to `hermes_adapter` while preserving OMES-owned checks for systemd units, resource quotas, and unauthenticated listener exposure.
   - Updated `lib/omes/py/agent/health.py` and `compose_health.py` to record proper layer authorities.
3. **Architecture and Capabilities**:
   - Authored `docs/adr/0022-consume-hermes-native-health.md` documenting the 11-criteria architectural evaluation.
   - Updated `architecture/capabilities.json` for `hermes.agent.health`: set `duplication_allowed: false`, `adr_reference: ADR-0022`, `removal_trigger: null`.
4. **Testing**:
   - Added unit test suite `tests/py/health/test_hermes_adapter.py` covering doctor pass/fail/timeout, profile discovery, gateway status, and secret redaction.
   - Updated `tests/py/health/test_hermes.py` with explicit source and authority assertion tests.
