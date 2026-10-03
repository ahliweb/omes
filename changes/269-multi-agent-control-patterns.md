---
issue: 269
type: added
---
Added the phase-0 decision record for adopting Octop-inspired multi-agent control patterns without a second agent runtime: ADR-0032 and docs/multi-agent-control-patterns.md map every requested capability to its single authority (Hermes for coordinator/worker delegation, agent-run durability and inbound ACP via `hermes acp`; OMES for host-job reconciliation, the correlation envelope contract and workload isolation; AWCMS for approvals, budget policy and Mission Control), registered the existing upstream `hermes.agent.delegation` and `hermes.agent.acp_server` capabilities, extended linter invariant R9 so OMES can never own ACP, coordinator or subagent capabilities, added threat-model delta MA-01 to MA-11, and decomposed the remaining work into issues #270 to #277; no runtime behavior changes.
