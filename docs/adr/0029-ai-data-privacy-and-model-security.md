# ADR-0029: AI data privacy boundary and local/cloud inference policy

- **Status:** Accepted
- **Date:** 2026-09-24
- **Decision maker:** @ahliweb
- **Issue:** [#213](https://github.com/ahliweb/omes/issues/213)
- **Related:** ADR-0011, ADR-0013, ADR-0017, ADR-0018, ADR-0019, ADR-0027, ADR-0028

## Context

Hermes can use local/self-hosted and cloud model providers. OMES must protect host/deployment
boundaries without duplicating Hermes reasoning, agent runtime, sessions, or provider routing.
Sensitive-data workloads therefore need a policy that separates **where data may flow** from
**which model/provider Hermes selects**.

A simplistic "local model = safe / cloud model = unsafe" rule is insufficient. Local
inference still has host, supply-chain, telemetry, logging, backup, admin, and model risks.
Cloud inference may be usable for lower-classified or properly minimized/sanitized context,
but provider assertions such as "not used for training" do not prove zero retention, no human
access, no subprocessors, or no cross-border processing.

## Decision

Adopt a classification-driven AI trust boundary with two supported patterns:

1. **Local-only/private inference** for `RESTRICTED` workloads.
2. **Local sensitive-data plane + cloud reasoning/control plane** where cloud AI receives only
   policy-approved minimized/sanitized context.

Define `PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, and `RESTRICTED` as the minimum classes.
Unknown/missing classification or destination fails closed.

Hermes remains authoritative for model/provider routing. OMES may implement host-side policy,
hardening, verification, evidence, recovery, and allowlisted execution boundaries, but must
not become a second LLM router or read private Hermes databases.

Model output is always untrusted input. Deterministic OMES/Control Center authorization and
existing allowlisted job/pull-worker contracts remain authoritative. No model response may
directly become arbitrary shell/API execution.

Embeddings, RAG corpus/chunks, outputs, tool results, caches, logs, and backups are data
processing and inherit classification unless a reviewed transformation explicitly changes it.

## Consequences

### Positive

- Preserves upstream Hermes ownership while giving OMES a precise host/privacy enforcement
  boundary.
- Supports high-sensitivity local inference without forcing every workload off cloud AI.
- Makes egress decisions deterministic, auditable, and testable.
- Avoids storing raw restricted prompts as compliance evidence.
- Fits existing default-deny, fixed-operation, root/user separation, and reconciliation
  patterns.

### Costs and risks

- Local-only inference may require GPU/CPU capacity, model provenance controls, patching,
  monitoring, and network isolation.
- Sanitization/tokenization requires careful threat modeling; weak transformations can still
  leak identities or business secrets.
- Provider due diligence remains necessary for any approved cloud path.
- Host policy cannot eliminate risks inside upstream Hermes or the model/provider.
- Data-residency and legal determinations remain organization-specific.

## Rejected alternatives

1. **Send all prompts to cloud if the provider says they are not used for training.**
   Rejected because training, retention, human access, subprocessors, and data transfer are
   separate properties.
2. **Run everything locally and treat that as sufficient security.**
   Rejected because local execution alone does not address host, supply-chain, telemetry,
   privilege, logging, backup, or physical risks and can impose unnecessary cost.
3. **Implement model/provider routing in OMES.**
   Rejected because Hermes owns this capability under ADR-0017.
4. **Log/hash every raw prompt for auditability.**
   Rejected because audit evidence must not become a new sensitive-data store; low-entropy
   hashes can still leak information.
5. **Allow model output to choose and execute arbitrary operations.**
   Rejected because model output is untrusted and conflicts with the existing allowlisted
   execution boundary.

## Implementation

The decision is documented now. Runtime controls are **not implemented yet**:

- #214 classification/egress contract
- #215 restricted local-only deployment posture
- #216 privacy posture/evidence
- #217 Control Center projection
- #218 regression/exfiltration tests

Canonical policy: [AI Data Privacy and Model Security](../ai-data-privacy-and-model-security.md).

## Standards and guidance

The design is informed by ISO/IEC 27001-family controls, ISO/IEC 27701:2025,
ISO/IEC 42001:2023, ISO/IEC 23894:2023, ISO 22301, ISO/IEC 20000-1, ISO/IEC 15408,
NIST AI RMF/GAI Profile, NIST SP 800-207, OWASP Top 10 for LLM/GenAI 2025, and relevant
Indonesian data-protection/electronic-system/AI-ethics rules. This ADR does not claim
certification or legal compliance.
