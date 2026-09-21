# ADR-0027 — Control Center Secure Pull-Worker Transport

- **Status:** Accepted
- **Date:** 2026-09-21
- **Decision maker:** @ahliweb
- **Related:** ADR-0012 (Control Center job contracts), ADR-0017 (Upstream-first ownership and boundary enforcement), ADR-0023 (Control Center UI/UX design system), ADR-0024 (Content workflow boundary), [docs/control-center-and-integrations.md](../control-center-and-integrations.md), [docs/jobs.md](../jobs.md), [docs/security.md](../security.md), [Issue #192](https://github.com/ahliweb/omes/issues/192), Epic [#195](https://github.com/ahliweb/omes/issues/195)
- **Supersedes / Amends:** Operationalizes the host execution and communication boundary for the AWCMS-one web GUI by establishing an outbound-only pull-worker transport.

---

## Context

The OMES Control Center (governed by ADR-0023 and AWCMS) provides an operator-facing control plane for fleet inventory, deployment status, and job management. However, bridging the web control plane to distributed OMES target hosts presents critical security and operational risks:
1. Running an inbound privileged daemon (HTTP/gRPC/WebSocket listener) on OMES hosts introduces network exposure, requires managing public ingress, and risks remote code execution.
2. Relying on inbound SSH with stored credentials in the web database creates high-value credential targets, complicates firewall/NAT traversal, and risks arbitrary shell escapes.
3. Web panels often couple directly to host filesystems or internal Hermes databases, violating authority boundaries.

Under ADR-0017 and the OMES Security Architecture ([docs/security.md](../security.md)):
- OMES hosts must not expose public privileged listeners by default.
- Inbound arbitrary shell execution is strictly prohibited.
- Host mutations must execute only via allowlisted, schema-validated OMES operations (`jobs.runner`).
- Web control planes must communicate with hosts using scoped identities, short-lived challenges, and zero raw credential storage.

Issue [#192](https://github.com/ahliweb/omes/issues/192) establishes the communication transport between AWCMS Control Center and OMES target hosts.

---

## Evaluation of Options (11-Criteria Matrix)

We evaluated two primary architectural options:
- **Option A (Inbound Privileged Listener / Inbound SSH Execution):** Control Center connects directly to OMES hosts via inbound HTTPS API listeners or SSH tunnels to trigger jobs and stream output.
- **Option B (Outbound-Only Pull-Worker Architecture - Recommended):** OMES host runs a least-privilege background pull-worker that initiates outbound HTTPS connections to the Control Center to fetch queued jobs, dispatch them locally via allowlisted `jobs.runner`, and report sanitized status and heartbeats.

| Criterion | Option A (Inbound Listener / SSH) | Option B (Outbound Pull-Worker) |
|---|---|---|
| **1. Advantages & Disadvantages** | Pro: Real-time immediate job push. Con: Inbound firewall holes required, public attack surface, complex NAT traversal, credential storage risk. | Pro: Zero inbound ports, works seamlessly behind NAT/firewalls, host controls execution rate, no stored root credentials. Con: Polling introduces slight dispatch latency (configurable debounced polling / long-polling). |
| **2. Security** | Critical Risk: Exposes privileged listening port to host network; SSH keys in web DB create single point of failure for fleet compromise. | Outstanding: Outbound TLS only; challenge-based enrollment with local key generation; strict request signing/nonce verification; zero arbitrary shell escape. |
| **3. Performance** | Direct socket push. | Highly efficient: Lightweight JSON poll requests with backoff and heartbeat telemetry; low CPU/bandwidth consumption. |
| **4. Maintainability** | Complex: Managing dynamic port bindings, mTLS certificates, firewall rules, and SSH key rotations per host. | High: Standardized REST/JSON contracts validated with JSON Schema; isolated worker module (`lib/omes/py/jobs/worker.py`). |
| **5. Scalability** | Degrades across multi-cloud, hybrid, and dynamic IP environments. | Seamless: Target hosts behind private VPCs, NAT gateways, or mobile connections poll out reliably without network reconfiguration. |
| **6. Accessibility** | Requires network operator intervention to configure firewall port forwarding for every new host. | Simple self-service: Single registration challenge token allows host to enroll and begin pulling jobs immediately. |
| **7. SEO Impact** | Neutral. | Neutral. |
| **8. UI/UX Implications** | Connection errors when hosts change IP addresses or sleep. | Resilient: Control Center accurately displays worker heartbeat and online/offline status based on reported telemetry. |
| **9. Compatibility** | Requires dedicated ingress configuration on Ubuntu Server and Linux Mint. | 100% compatible across all Tier-1 operating systems and cloud environments without special firewall rules. |
| **10. Operational Complexity** | High: Network firewalls, ingress controllers, VPNs, and SSH bastion hosts required. | Minimal: Standard HTTPS outbound traffic allowed by default in all corporate and cloud network policies. |
| **11. Long-Term Implications** | Fragile security perimeter; vulnerable to supply-chain and perimeter penetration. | Enduring zero-trust architecture aligned with NIST SP 800-207 and CIS hardening benchmarks. |

---

## Decision

We adopt **Option B**:
1. **Pull-Worker Transport Architecture:**
   - OMES runs an outbound-only pull-worker service (`lib/omes/py/jobs/worker.py` and CLI `omes worker`).
   - The worker establishes HTTPS connections exclusively to the configured Control Center endpoint.
   - The worker does not listen on any network port.
2. **Challenge-Based Enrollment:**
   - Control Center generates a short-lived, single-use enrollment token for a server record.
   - Host runs `omes worker enroll --token <token> --endpoint <url>`.
   - Host generates local worker keypair, exchanges enrollment token with Control Center, and stores credentials securely under mode `0600` (`$OMES_STATE_DIR/worker/credentials.json`).
   - Private key material never leaves the host.
3. **Execution Safety Boundaries:**
   - Worker polls `/api/v1/worker/poll`.
   - Every received job is validated against `contracts/control-center/v1/operation-request.schema.json`.
   - Target scope (`tenant_id`, `server_id`), contract version, and idempotency key are strictly verified.
   - Unknown operations or unverified capabilities are rejected with typed error responses (`unsupported_capability`).
   - Execution is strictly delegated to `jobs.runner.run_job()`, invoking fixed `bin/omes` arguments without shell wrappers.
4. **Sanitized Evidence Reporting:**
   - Results are posted to `/api/v1/worker/result`.
   - All output is scanned and redacted: zero secrets, passwords, or raw environment variables are reported.
   - Post-mutation read-back verification (`status --json`) confirms host state before reporting success.
5. **Heartbeat & Telemetry:**
   - Worker transmits periodic heartbeats (`contracts/control-center/v1/worker-heartbeat.request.schema.json`) detailing OMES version, supported contract version, capability digest, and platform telemetry.
