---
issue: 192
type: feat
summary: add a secure pull-worker transport for the AWCMS-one web GUI (ADR-0027)
---

### Summary of changes

1. **Architectural Decision Record (ADR-0027)**:
   - Authored `docs/adr/0027-control-center-pull-worker-transport.md` evaluating the pull-worker transport against 11 architectural criteria.
   - Established outbound-only TLS polling and heartbeat telemetry, challenge-based enrollment, fixed-argv dispatch via `lib/omes/py/jobs/store.py` and `runner.py`, and sanitized evidence reporting.
   - Registered ADR-0027 in `docs/adr/README.md`.

2. **Strict Versioned Wire Contracts (`contracts/control-center/v1/`)**:
   - Authored 8 JSON Schemas under Draft 2020-12 fail-closed rules:
     - `worker-enrollment.request.schema.json`
     - `worker-enrollment.response.schema.json`
     - `worker-poll.request.schema.json`
     - `worker-poll.response.schema.json`
     - `worker-heartbeat.request.schema.json`
     - `worker-heartbeat.response.schema.json`
     - `worker-result.request.schema.json`
     - `worker-result.response.schema.json`
   - Added valid and invalid test fixtures with exact `.reason.txt` expected error annotations under `contracts/control-center/v1/fixtures/`.
   - Verified 100/100 contracts pass `scripts/check-contracts.py`.

3. **Pure-Python Pull-Worker Client (`lib/omes/py/jobs/worker.py`)**:
   - Implemented challenge-based enrollment with Ed25519 key generation and secure credential storage (`<state-dir>/worker/credentials.json`, mode 0600).
   - Implemented poll-and-dispatch loop validating server/tenant scope, verifying allowlisted capabilities, executing jobs via `store.submit` and `runner.run`, and preventing arbitrary command execution.
   - Implemented telemetry heartbeat reporting host load, memory, disk, and uptime.
   - Enforced fail-closed secret redaction on all outbound result payloads.

4. **CLI Extension Command (`bin/omes worker` / `lib/omes/cmd/worker.sh`)**:
   - Implemented `omes worker <enroll|poll|heartbeat|status>` CLI wrapper.
   - Documented `omes worker` in `docs/cli.md` (section 4.17).

5. **Test Coverage**:
   - Unit tests in `tests/py/jobs/test_worker.py`: enrollment success/rejection, heartbeat telemetry, idle polling, status job execution with read-back, and cross-server scope rejection.
   - Integration tests in `tests/integration/worker.bats`: CLI usage, help flags, unenrolled failure states.

6. **Documentation**:
   - Updated `docs/control-center-and-integrations.md`, `docs/control-center-contracts.md`, and `docs/jobs.md`.
