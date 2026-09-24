# ADR-0029 — AI data boundary and private-inference architecture

- **Status:** Accepted
- **Date:** 2026-09-24
- **Decision maker:** @ahliweb
- **Related:** [Issue #213](https://github.com/ahliweb/omes/issues/213), [ADR-0017](0017-upstream-first-ownership-and-boundary-enforcement.md), [docs/ai-data-privacy-and-model-security.md](../ai-data-privacy-and-model-security.md), [docs/security.md](../security.md), [docs/threat-model.md](../threat-model.md)
- **Implementation:** #213–#218 are all implemented; closed as of commit `ce44b0a` (PR [`ahliweb/omes#231`](https://github.com/ahliweb/omes/pull/231)). #214 (machine-readable data classification and egress policy): see [contracts/ai-egress/v1](../../contracts/ai-egress/v1/) and [lib/omes/py/privacy/egress_policy.py](../../lib/omes/py/privacy/egress_policy.py). #215 (Restricted local-only inference deployment posture): see `modules/hermes-restricted/module.sh` and [lib/omes/py/privacy/restricted_posture.py](../../lib/omes/py/privacy/restricted_posture.py). #216 (posture/egress evidence): see [lib/omes/py/privacy/posture_evidence.py](../../lib/omes/py/privacy/posture_evidence.py) and `omes health ai-privacy`. #217 (Control Center projection, OMES side): see [lib/omes/py/privacy/posture_projection.py](../../lib/omes/py/privacy/posture_projection.py) — AWCMS-side consumption remains not implemented, with no owning OMES issue. #218 (regression/exfiltration-resistance coverage): see `tests/py/privacy/test_privacy_boundary_regression.py`.

## Context

OMES integrates Hermes Agent, which supports both external model providers and local/self-hosted
OpenAI-compatible endpoints. Sensitive workloads need a clear rule for when model context may leave
the operator-controlled environment.

A naive choice between “all local” and “all cloud” is insufficient:

- local inference reduces provider disclosure but still has host, dependency, model, tool, logging,
  backup, privilege, and network risks;
- cloud models may provide stronger capabilities but can create privacy, retention, subprocessor,
  transfer, and disclosure risks when raw sensitive data is included;
- a generic OMES LLM router would duplicate Hermes, violating ADR-0017;
- allowing the model itself to decide whether data is safe to export makes the untrusted model a
  security authority.

## Options considered

### Option A — require all AI inference to be local

**Pros:** simple egress boundary; strong data-sovereignty posture.

**Cons:** GPU/capacity cost, model-quality trade-offs, supply-chain/operations burden, and unnecessary
restriction for public/non-sensitive tasks.

### Option B — allow cloud models whenever a provider states data is not used for training

**Pros:** simple operations; broad model capability.

**Cons:** training use is only one privacy property. Retention, human access, subprocessors,
geographic processing, transfer terms, logging, and breach response remain separate. This does not
provide a sufficient security boundary.

### Option C — OMES implements a full AI gateway/provider router

**Pros:** central interception point.

**Cons:** duplicates Hermes model/provider ownership, increases protocol/credential attack surface,
and violates upstream-first architecture unless Hermes lacks a required supported interface and a
separate ADR explicitly justifies temporary duplication.

### Option D — classify data, keep Restricted workloads local/private, and permit only approved
minimized/sanitized context to cloud models

**Pros:** preserves strong privacy boundaries for high-risk data while retaining cloud-model
capability for appropriate tasks; fits zero-trust/data-minimization principles; reuses Hermes
provider routing.

**Cons:** requires explicit classification, application-specific sanitization, evidence, policy
governance, and more test coverage.

## Decision

Adopt **Option D**, with local-only/private inference as the mandatory default for **RESTRICTED**
data.

1. OMES defines a four-level initial classification: `PUBLIC`, `INTERNAL`, `CONFIDENTIAL`,
   and `RESTRICTED`.
2. Missing/unknown classification fails closed for model egress.
3. Restricted data defaults to local/private inference; cloud egress is denied.
4. Confidential cloud use is deny-by-default and requires an explicit policy plus evidence of
   application-owned minimization/sanitization/tokenization appropriate to the domain.
5. Secrets, credentials, private keys and authentication material are never cloud-model prompt
   material.
6. RAG chunks, embeddings, vector retrieval, reranking and assembled context inherit source-data
   classification.
7. The AI model is never the policy or authorization authority.
8. Model output cannot directly create arbitrary host/API execution; existing typed, allowlisted
   OMES jobs remain the mutation boundary.
9. Audit/evidence stores bounded policy/posture metadata, never raw Restricted prompts,
   chain-of-thought, transcripts or credentials.
10. Hermes remains authoritative for model/provider routing and local model endpoints.
11. OMES owns host lifecycle/hardening/evidence and may implement a Restricted local-only service
    posture without implementing a second model router.
12. AWCMS/Control Center may project policy/evidence and own tenant/business approvals, but may not
    become a prompt/secret archive.
13. Provider assurance treats training use, retention, human access, subprocessors, residency,
    transfers and deletion as separate facts.
14. No claim of legal compliance, ISO certification, or Common Criteria certification follows
    merely from implementing this ADR.

## Security and privacy rationale

This decision aligns with:

- NIST AI RMF and the Generative AI Profile's risk-management approach;
- NIST SP 800-207 resource-centric zero-trust and least privilege;
- OWASP GenAI risks including prompt injection and sensitive information disclosure;
- ISO/IEC 42001 and ISO/IEC 23894 AI governance/risk management;
- ISO/IEC 27001-family security controls and ISO/IEC 27701 privacy governance;
- Indonesian UU 27/2022 PDP requirements around specific personal data, high-risk processing,
  safeguards and transfers;
- PP 71/2019 PSTE and SE Menkominfo 9/2023 AI ethics as relevant Indonesian governance context.

## Consequences

### Positive

- Restricted workloads have an explicit, reviewable no-cloud default.
- Cloud reasoning remains usable for non-sensitive or properly minimized tasks.
- OMES does not duplicate Hermes provider routing.
- Policy decisions become deterministic and testable.
- Control Center can report privacy posture without receiving protected content.

### Negative / cost

- Applications must classify data and own domain-specific sanitization.
- Local-only workloads require hardware/runtime compatibility and supply-chain operations.
- Provider assurance requires ongoing review.
- Network isolation and local model services may need host-specific tests.

### Residual risk

- A compromised host/operator can bypass policy outside OMES.
- Local models can be malicious, vulnerable, or supply-chain compromised.
- Sanitization may be insufficient for a specific dataset even when technically successful.
- Aggregates may remain re-identifiable.
- Legal obligations depend on deployment context and cannot be proven by this technical policy.

## Revisit triggers

Revisit this ADR when:

- Hermes adds/removes a supported policy hook that materially changes the boundary;
- OMES needs to proxy model traffic rather than verify host posture;
- a legal/regulatory change affects AI/personal-data transfer requirements;
- a Restricted cloud-processing exception is proposed;
- local inference deployment expands to new runtime/GPU trust assumptions;
- a privacy/security incident shows that the classification or evidence model is insufficient.
