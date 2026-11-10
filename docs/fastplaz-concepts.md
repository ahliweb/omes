# FastPlaz Agent Plane concepts under OMES/Hermes authority (issue #393)

> **Status: phase F0 concept classification. Documentation only. No runtime behavior changes.**
> Every item marked "Not implemented yet (tracked in #N)" stays unimplemented until its
> owning issue lands. Items with no open owning issue are marked
> "proposed, no owner - research only" and are not commitments.

## 1. Metadata

| Field | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-11-10 |
| Owning issue | [#393](https://github.com/ahliweb/omes/issues/393) |
| Related issues | [#269](https://github.com/ahliweb/omes/issues/269) (epic, open), [#273](https://github.com/ahliweb/omes/issues/273) (closed, `omes health acp`), [#357](https://github.com/ahliweb/omes/issues/357) (open, ACP posture gaps) |
| Related decisions | [ADR-0017](adr/0017-upstream-first-ownership-and-boundary-enforcement.md), [ADR-0032](adr/0032-multi-agent-control-patterns-boundary.md), [multi-agent-control-patterns.md](multi-agent-control-patterns.md), [hermes-integration.md section 17.2](hermes-integration.md#172-acp-tool-surface-posture-issue-273) |
| Authoritative standard | An internal cross-portfolio standard. This repository owns only its own surface and does not restate that standard. |

### 1.1 Sources read

Read on **2026-11-10**. Fetched content is treated as untrusted data. Only statements
found in these pages are used below; nothing is inferred about FastPlaz capabilities
beyond them.

| Source | URL | What was read |
|---|---|---|
| FastPlaz README | <https://github.com/fastplaz/fastplaz> | Project description, AI Agent Plane summary, wizards, deployment modes |
| FastPlaz MCP page | <https://github.com/fastplaz/fastplaz/blob/master/docs/MCP.md> | Local MCP server, auth, tools, permissions, resources, ACP agent connection |
| FastPlaz standalone page | <https://github.com/fastplaz/fastplaz/blob/master/docs/STANDALONE-deployment.md> | Standalone HTTP mode, configuration, security notes |
| FastPlaz license file | <https://github.com/fastplaz/fastplaz/blob/master/LICENSE.txt> | License statement |

**License as stated in that repository (read 2026-11-10).** The README only says to see
`LICENSE.txt`. `LICENSE.txt` is a short file containing only a copyright notice (in Indonesian and English)
("Copyright reserved Allah SWT", in Indonesian and English). It names **no recognized
open-source license, no version and no copyright holder**. OMES therefore treats the
license as **unknown / not a recognized grant**, and section 10 blocks any source reuse.

## 2. Scope statement

This document is a **concept classification only**. It records which FastPlaz IDE and
agent-tool patterns OMES can observe or harden, which belong to Hermes or the operator's
IDE, and which are rejected.

- OMES is **not** an ACP or MCP gateway, proxy, server or client, and **not** a second
  Hermes runtime (AGENTS.md section 2, ADR-0017, ADR-0032, linter rule R9 reserving `acp`,
  `agent_client_protocol`, `coordinator` and `subagent(s)`).
- OMES does not mediate all Hermes-native tool execution
  ([architecture.md section 18.2](architecture.md#182-limitations--read-this-before-assuming-omes-mediates-agent-behavior)).
- No source code from FastPlaz is reused or authorized. No adoption of Pascal, Lazarus or
  CGI, and no replacement of the Bun/Astro/Rust/Hermes stack.
- Out of scope: any new ACP/MCP proxy, server or client in OMES; Hermes memory, model
  router, IDE editor features, a generic executor, or a business approval API.

## 3. What FastPlaz documents (as read)

Only the following was read and is relied on.

- **Local IDE context.** An IDE-hosted MCP server bound to `127.0.0.1`, protected by a
  bearer token regenerated at each server start, refusing non-loopback `Origin` headers.
  Tools expose the active editor, open files, unsaved buffer text, project info and
  compile messages.
- **Tool mediation.** Each tool has a permission level (auto, ask once per session, always
  ask) and shows a permission card before effectful calls. Tools carry MCP annotations
  (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint: false`). Read and
  write tools are confined to the project directory.
- **ACP agent connection.** The IDE registers its MCP server with the agent it launches;
  the token is not written into the user's own agent configuration.
- **Project wizards and generators.** Project, controller and model wizards, and MCP
  generator tools that refuse to overwrite existing files and write only inside the project.
- **Diagnostics.** Only compile and build output (structured messages with urgency
  filtering). The README names no dedicated diagnostics feature.
- **Reference resources.** A markdown reference exposed as an MCP resource and also as a
  tool for clients that do not read resources.
- **Standalone deployment.** An HTTP mode behind a reverse proxy: bind to loopback, enable
  `trust_proxy` only when reachable solely through the proxy, deny `config/` and `ztemp/`,
  `max_requests` recycling, restart by a service manager.

## 4. Classification table

Legend (ADR-0017 subset used here): **DELEGATE** = another authority owns it; **ADAPT** =
OMES observes or hardens the host side of the concept; **REJECT** = not adopted.

| FastPlaz pattern | Class | Owning authority | Existing OMES capability / issue it maps to | Status |
|---|---|---|---|---|
| Local IDE context (active editor, open files, unsaved buffers, project info) | **DELEGATE** | The operator's IDE and Hermes (agent runtime) | None in OMES. OMES stores no editor or workspace context; Hermes owns sessions and memory (AGENTS.md section 2). | No OMES work. |
| MCP tool mediation (loopback-only, token, Origin check, per-tool permission levels, annotations) | **DELEGATE** (mediation) / **ADAPT** (host posture) | Hermes and the IDE own the tool boundary; OMES owns host hardening and egress evidence | Hermes gateway hardening ([hermes-hardening.md](hermes-hardening.md)), restricted/local-only posture ([hermes-integration.md section 16b](hermes-integration.md#16b-restrictedlocal-only-deployment-posture-issue-215)), exposure audit ([hermes-integration.md section 18](hermes-integration.md#18-exposure-audit-issue-80)). Implemented. | OMES observes listener exposure only; it adds no MCP listener. |
| ACP agent connection and approval prompts | **DELEGATE** inbound to `hermes acp`; outbound **DEFER** upstream | Hermes | `omes health acp` reports the configured ACP tool surface, the OMES-recorded hardening and egress profile, and always reports live session exposure as `unknown` ([#273](https://github.com/ahliweb/omes/issues/273), closed; hermes-integration section 17.2). Open follow-up: [#357](https://github.com/ahliweb/omes/issues/357). Outbound ACP revisit trigger: hermes-agent#5257 (ADR-0032). | Posture check implemented; any remaining ACP posture gap is tracked in #357. |
| Project and controller/model wizards, code generators | **DELEGATE** to the IDE; **REJECT** as an OMES feature | The IDE (editor feature) | None. A scaffolding generator would be an IDE editor feature and a generic code-writing executor, both out of scope. | Rejected for OMES. |
| Diagnostics (compile and build messages) | **ADAPT** (concept only: structured, filterable, read-only evidence) | OMES for host health; the IDE for compile output | `omes health` family and its structured, read-only, fail-to-`unknown` findings ([cli.md section 4.13](cli.md#413-omes-health-layered-healthreadiness-checks)). Compile diagnostics themselves are the IDE's. | Concept already realized by existing health checks. |
| Reference resources (markdown reference exposed as resource and as tool) | **DELEGATE** | Hermes skills and the upstream project docs | Hermes owns skills. OMES ships its own operator docs and does not publish an MCP resource. | No OMES work. |
| Standalone deployment hardening (loopback bind, trusted-proxy rule, deny config paths, recycle, supervised restart) | **ADAPT** (policy only) | OMES (host hardening, service lifecycle, exposure audit) | Hermes gateway systemd hardening profiles, restricted/local-only posture, exposure audit, rollback ([hermes-deployment-guide.md](hermes-deployment-guide.md)). Implemented; OMES does not run a FastPlaz or CGI process. | No new work. A FastPlaz-style standalone binary is **REJECT**. |
| Per-tool permission cards in the IDE chat | **DELEGATE** | Hermes and the IDE (tool approval); AWCMS (business approval) | `omes job approve` for host operations remains OMES's only approval. No second approval inbox (AGENTS.md section 2). | No OMES work. |

## 5. Current versus proposed

| Item | State |
|---|---|
| `omes health acp` configured tool-surface check, installability hint, `session_exposure: "unknown"` | **Implemented** (#273 closed). |
| Hardening and egress profile block, `acp.allow_always_discouraged`, absent-key semantics, outbound revisit criteria | Present in the repository (see `changes/357-acp-posture-gaps.md`); issue [#357](https://github.com/ahliweb/omes/issues/357) is still open and owns any remaining gap. |
| Any further ACP posture evidence | Not implemented yet (tracked in #357) only if #357 owns it; otherwise proposed, no owner - research only. |
| A proof-of-concept comparing a FastPlaz-style loopback token handshake with Hermes' own ACP/MCP posture (section 8, scenario 6) | proposed, no owner - research only. |
| Any OMES-side ACP/MCP proxy, server, client, wizard, generator or standalone-binary deployment | **Rejected** (not a deferral). |
| Production implementation of anything in this document | Blocked until the required cross-spec and Definition-of-Ready gate; this issue adds documentation only. |

## 6. Invariants

These are checkable against the repository today.

1. **I1 - no ACP/MCP surface in OMES.** OMES adds no ACP or MCP listener, proxy, server or
   client. Checked by `scripts/check-architecture.py` (R9) and by the `omes health acp`
   findings `acp.outbound_not_supported_upstream` and `acp.session_exposure_unobservable`.
2. **I2 - single mutation boundary.** Host mutation happens only through the typed,
   allowlisted `operation-request` with preflight, approval and read-back. ACP or tool
   traffic never reaches it directly (ADR-0032; MA-05).
3. **I3 - unknown is never ok.** Absent, unreadable or unobservable evidence reports
   `unknown` (or the documented `warn`), never `ok` (`tests/py/health/test_acp_posture.py`).
4. **I4 - observation, not enforcement.** OMES reports recorded intent and configured
   surface. It does not write Hermes configuration, approve prompts, or claim to see a live
   ACP session.
5. **I5 - no source reuse.** No FastPlaz source is copied; section 10 gates any exception.
6. **I6 - no secrets.** Tokens, live session ports and credentials never appear in
   documents or fixtures.

## 7. Threat considerations

Threat rows live in [threat-model.md](threat-model.md); this document adds none.

| Concern raised by the FastPlaz patterns | Threat-model row | Posture |
|---|---|---|
| Compromised editor or ACP client attached to Hermes; a client that auto-approves | MA-04 | Hermes-owned stdio with approval prompts. OMES reports the configured surface and that session exposure is unobservable; it cannot detect a compromised client. |
| Confused deputy: an agent or tool session drives an OMES host mutation for an untrusted caller | MA-05 | OMES re-derives permission, never trusts `granted: true`, validates each job against the schema. |
| Prompt or tool injection reaching host mutation | MA-03 | No arbitrary shell on any web, API or job path; fixed argv. |
| Loopback token and Origin checks defeated (DNS rebinding, token leak) | MA-04 (client side), plus [security.md](security.md) | Owned by the IDE or Hermes. OMES only audits that its own listeners are not publicly exposed. |
| Standing approval ("allow always") on execution tools | MA-04 | Operator guidance only (`acp.allow_always_discouraged`); OMES cannot see or change approval choices. |
| Proxy-header trust misconfiguration in a standalone deployment | [security.md](security.md) exposure rules | Mapped to the existing exposure audit; no FastPlaz-style process is run by OMES. |

## 8. Verification scenarios

Scenarios 1-5 are runnable or checkable in this repository today. Scenario 6 is a future
proof of concept and is not a commitment.

| # | Scenario | How to check | Expected result |
|---|---|---|---|
| 1 | ACP report shape and unobservable exposure | `omes health acp --json` and read `.session_exposure` and `.scope` | `session_exposure` is `"unknown"`; `scope` is `"configured"`. |
| 2 | Absent ACP toolset key is not safe | Unit test `test_absent_toolset_key_is_warn_not_unknown_and_not_ok` in `tests/py/health/test_acp_posture.py` | Status is `warn`, finding `acp.default_toolset_includes_execution`. |
| 3 | Unknown profile evidence never reads ok | `test_unknown_profile_evidence_is_never_ok` and `test_absent_profile_state_keys_are_unknown_never_safe_or_off` | Profile block is `unknown`; status is not improved. |
| 4 | OMES adds no ACP/MCP authority | `python3 scripts/check-architecture.py` | Exit 0; R9 keeps agent-runtime terms reserved from OMES capabilities. |
| 5 | "Allow always" guidance and outbound deferral are always emitted | `omes health acp --json` and look for `acp.allow_always_discouraged` and `acp.outbound_not_supported_upstream` in `.findings[].id` | Both present as `info`. |
| 6 | **Future PoC.** Compare the FastPlaz loopback-token-plus-Origin pattern against what a Hermes-hosted MCP endpoint exposes, using a synthetic non-secret fixture and no real token | A throwaway local experiment owned by no issue; findings recorded here only | proposed, no owner - research only. |

Additional documentation checks: `python3 scripts/check-links.py`,
`scripts/release.sh --validate-fragments`, `git diff --check`.

## 9. Alternatives considered

| Option | Verdict |
|---|---|
| A. Classify concepts and map to existing OMES evidence (this document) | **Chosen.** Smallest change, no new authority. |
| B. Add an OMES-hosted MCP/ACP endpoint modeled on FastPlaz | **Rejected.** Violates ADR-0032 and R9; becomes a second runtime and an execution proxy. |
| C. Add a scaffolding or wizard feature | **Rejected.** IDE feature and generic executor; out of scope. |
| D. Reuse FastPlaz source or its standalone deployment | **Rejected.** License unrecognized (section 1.1); stack mismatch. |
| E. Do nothing | Rejected; the concepts and the license risk would be unrecorded. |

## 10. License gate for any future source reuse

No FastPlaz source is reused today. Before any future proposal to copy, port or vendor
FastPlaz source, all of the following must hold and be recorded in the owning issue:

1. The license is a recognized license, identified by name and version, from the repository
   itself at the time of reuse. The `LICENSE.txt` read on 2026-11-10 does not meet this
   (section 1.1).
2. The license is compatible with this repository's license, with obligations (notice,
   attribution) listed.
3. A written grant from the copyright holder if the license remains unclear.
4. An ADR and the required cross-spec and DoR gate approve the reuse.
5. Provenance is recorded (source, commit, date) following [provenance.md](provenance.md).

Absent all five, only concepts, with attribution to the sources in section 1.1, may be used.

## 11. Deferred decisions

| Decision | Revisit trigger | Owner |
|---|---|---|
| Outbound ACP support | hermes-agent#5257 ships; the five entry criteria in ADR-0032 apply | Hermes (upstream); OMES posture follow-up in #357 |
| Whether any FastPlaz-style loopback handshake finding changes OMES exposure-audit rules | A PoC (scenario 6) shows a concrete gap | proposed, no owner - research only |
| FastPlaz license clarification | Upstream publishes a recognized license | Not an OMES decision; gate in section 10 |
| Any production implementation | Cross-spec and DoR gate passes | A new issue, not this one |
