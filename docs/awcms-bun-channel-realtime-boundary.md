# Bun-hosted AWCMS channel delivery and OMES host boundary (design clarification)

> Docs-only extension to [OMES ADR-0027](adr/0027-control-center-pull-worker-transport.md), [AGENTS.md](../AGENTS.md), [Issue #345](https://github.com/ahliweb/omes/issues/345). Date: 24 October 2026. This is **not** a WebSocket server or a new Hermes messaging feature.

## Runtime and authority matrix

| Domain | System of record / execution owner | Runtime / transport |
| --- | --- | --- |
| CRM campaign segments, approvals, notification policy, tenant consent, channel account + conversation state | AWCMS and derived AWCMS-One commerce adapter | The AWCMS repository's pinned Bun for all **AWCMS-owned** HTTP, outbox, scheduler and delivery workers; PostgreSQL/authorized durable job queue |
| Email/Mailketing | AWCMS `email` provider port/outbox | Existing `bun run email:dispatch` worker → provider API/HTTPS; vendor delivery result remains authoritative when verified |
| WhatsApp Meta, planned StarSender | AWCMS `whatsapp_delivery` and upstream multi-sender admission | Existing `bun run whatsapp:dispatch` worker → provider API/HTTPS; no second Node.js or OMES bot transport |
| Web Push and future customer marketing triggers | AWCMS `push_delivery` and downstream customer integration | Existing `bun run push:dispatch` → push-service/FCM HTTPS; browser Service Worker is consumer code, not Node server |
| Telegram staff and planned customer multi-bot, approved social publishing | AWCMS provider-specific modules/contracts | Bun-owned scheduled/outbox adapter → officially supported vendor HTTPS; no conversion of Hermes Telegram into AWCMS marketing engine |
| Realtime CRM progress/inbox/admin observation | AWCMS Control Center, upstream realtime [design #1168](https://github.com/ahliweb/awcms/issues/1168) | Future **Bun SSE first** for one-way UI events; **Bun native WebSocket** only with accepted duplex ADR; HTTPS polling allowed |
| Managed host provisioning, health, recovery and deployment | **OMES** | Native **Python/Bash/systemd** host tools; outbound-only signed HTTPS **pull-worker**, as ADR-0027 already requires |
| Agent planning, messages, native channels, sessions and skills | **Hermes** | Hermes-native runtime and supported API/CLI. OMES does not reimplement it |

**Meaning of Bun-only:** the user requires **AWCMS-owned broadcast/email/WhatsApp/notification/CRM web and real-time servers** to execute Bun rather than Node.js, npm/yarn/pnpm Node daemons, Python send workers or alternate runtime sidecars. It is **not a request to rewrite OMES Python agent/host workers or Hermes agent itself into Bun**. An AWCMS Control Center Bun frontend may display OMES state but must not become the host's process supervisor.

## Security boundaries for future Bun real-time displays

- OMES target hosts **never** open inbound HTTP/gRPC/WebSocket privileged listeners for the Control Center: maintain ADR-0027's **outbound HTTPS signed enroll/poll/result/heartbeat**. No direct SSH, shell command, raw profile token, Hermes database access or WebSocket route into managed hosts.
- The **AWCMS Bun** server may offer a separate authorized **dashboard data stream** after its own ADR/contract approval. Sources are sanitized, versioned, signed/verified OMES heartbeat/progress and AWCMS durable job state. Do not forward raw host telemetry, PII, transcripts, prompts, secrets, tokens or arbitrary agent tool actions as uncontrolled WebSocket events.
- Tenant and host capability scopes must be checked server-side before subscription. Require Origin allowlist, short-lived audience authorization, role/tenant restrictions, revocation, per-connection caps, backpressure, heartbeats, and protected sequence/cursor-based replay. No client-selected arbitrary host/session IDs across tenants.
- Display `fresh`, `stale`, `unknown`, `disconnected` appropriately; never claim delivered, completed or healthy solely because a socket event arrived.
- AWCMS SSE/WS is **not a durable command delivery bus**: OMES worker jobs remain existing signed pull contracts; Hermes handles messaging on supported native mechanisms; no duplicate orchestration or new privileged inbound listener.
- If multiple Control Center instances broadcast event projections, use an approved pub/sub or durable cursor reconciliation mechanism; in-memory Bun topics by themselves do not provide cross-instance delivery guarantees.
- Validate SDK/schema compatibility with upstream AWCMS Bun version on deploy, tenant data classification and privacy, HTTPS reverse proxy WebSocket upgrade, disconnect/reconnect, false status, leaked secrets and host network isolation.

## Relevant planning and verification

- Upstream requirements: [AWCMS #1167](https://github.com/ahliweb/awcms/issues/1167) Bun worker standard; [#1168](https://github.com/ahliweb/awcms/issues/1168) SSE/WebSocket ADR.
- Downstream template: [AWCMS-One #467](https://github.com/ahliweb/awcms-one/issues/467) Bun CRM standard; [#458](https://github.com/ahliweb/awcms-one/issues/458) campaigns, [#459](https://github.com/ahliweb/awcms-one/issues/459) Hermes suggestions, [#460](https://github.com/ahliweb/awcms-one/issues/460) Telegram, [#461](https://github.com/ahliweb/awcms-one/issues/461) Mailketing, [#430–#432](https://github.com/ahliweb/awcms-one/issues/430) Web Push.
- OMES [#343](https://github.com/ahliweb/omes/issues/343) typed agent-management contracts; [#344](https://github.com/ahliweb/omes/issues/344) Control Center AI UI, [#263](https://github.com/ahliweb/omes/issues/263) nonredundant Mission Control.
- Docs-only acceptance: preserve the host's Python and HTTPS worker contracts; do not invent agent control API, no runtime change/deployment. Follow OMES docs/security/architecture checks on PR.

Official Bun design inputs: https://bun.com/docs/runtime/http/websockets and https://bun.com/guides/http/sse. WebSocket/SSE gateway must not be marked shipped before upstream implementation.
