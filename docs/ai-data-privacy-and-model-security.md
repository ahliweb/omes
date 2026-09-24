# AI Data Privacy and Model Security

> Status: authoritative design baseline for issue [#213](https://github.com/ahliweb/omes/issues/213).
>
> Runtime enforcement described as future work is **not implemented yet** and is tracked in
> [#214](https://github.com/ahliweb/omes/issues/214) through
> [#218](https://github.com/ahliweb/omes/issues/218).
>
> This document is engineering and risk-management guidance. It does not claim ISO
> certification, legal compliance, or that a particular AI/provider deployment is safe merely
> because one control is present.

## 1. Decision summary

OMES supports two complementary privacy/security patterns:

1. **Local-only/private inference** for workloads whose prompts, context, retrieved documents,
   embeddings, outputs, or tool data are classified `RESTRICTED`. Hermes remains the agent
   runtime and model/provider router; OMES may provision, harden, verify, and observe the
   supported local/private endpoint and host/network posture.
2. **Local sensitive-data plane + cloud reasoning/control plane** for workloads where cloud AI
   is useful but raw sensitive data must remain local. Only policy-approved, minimized,
   sanitized, tokenized, synthetic, or bounded aggregate context may cross the trust boundary.

The security invariant is:

> **The model may propose; deterministic policy authorizes; an allowlisted executor acts.**

Cloud AI is never a security authority. Local AI is not automatically trusted merely because
it runs on-premises.

## 2. Ownership boundary

This policy follows AGENTS.md and ADR-0017.

| Component | Owns | Must not own |
|---|---|---|
| Hermes Agent | reasoning, sessions, memory, skills, tools, delegation, provider/model routing | OMES host policy or Control Center business authority |
| OMES | host compatibility, install/lifecycle, hardening, local enforcement, health/evidence, provenance, recovery, deployment state | a second LLM router, second agent loop, or private Hermes database |
| AWCMS/Control Center | tenant/business policy, approval, durable control-plane state when implemented | arbitrary shell, raw secrets, direct privileged host access |
| Model/provider | inference service and provider-side controls | OMES authorization or proof of lawful processing |

OMES must use supported Hermes CLI/API/configuration interfaces. It must not inspect
`messages.db`, private session stores, chain-of-thought, or raw prompt history in order to
implement this policy.

## 3. Threat and trust model

The relevant boundary is not simply "local versus cloud". It is **what data crosses which
trust boundary, for what purpose, under what policy, with what retention and verification**.

A useful reference architecture is:

```text
                   External / cloud trust zone
             +-----------------------------------+
             | Cloud model / private cloud AI   |
             +----------------+------------------+
                              ^
                              | sanitized/minimized context only
                       +------+------+
                       | AI egress   |
                       | policy gate |
                       +------+------+
                              |
====================== trust boundary ======================
                              |
                    +---------+----------+
                    | OMES local policy  |
                    | and evidence plane |
                    +---------+----------+
                              |
                    +---------+----------+
                    | allowlisted local  |
                    | executor/services  |
                    +----+-----------+---+
                         |           |
                 +-------+--+    +---+----------------+
                 | local AI |    | sensitive DB/files |
                 | / RAG    |    | secrets/credentials|
                 +----------+    +--------------------+
```

The primary risks include prompt injection, sensitive-information disclosure, excessive
agency, unsafe model-output handling, provider/supply-chain compromise, logging/backup
leakage, cross-tenant disclosure, replay, configuration drift, silent cloud fallback, and
false assurance based on incomplete provider claims.

## 4. Data classification

Runtime enforcement is **not implemented yet (tracked in #214)**.

| Class | Typical examples | Default AI egress rule |
|---|---|---|
| `PUBLIC` | published docs, public source code, public metadata | cloud permitted subject to provider policy |
| `INTERNAL` | non-public operating procedures, low-sensitivity internal metadata | cloud only when organizational policy permits |
| `CONFIDENTIAL` | customer/business records, private source, internal financial/operational data | sanitize/minimize; private endpoint or approved cloud only |
| `RESTRICTED` | credentials, tokens, private keys, authentication material, raw health/biometric/genetic data, highly sensitive personal data, regulated secrets | local-only by default; cloud denied |

Missing, unknown, stale, or unrecognized classification must fail closed.

Classification applies to **inputs, outputs, retrieved context, embeddings, tool results,
logs, audit evidence, backups, caches, and derived artifacts**. A transformation does not
automatically lower classification.

## 5. Egress modes

The machine-readable contract is **not implemented yet (tracked in #214)**.

- `local_only`: data and inference remain inside the approved local/private boundary.
- `private_endpoint`: approved private service reachable only through an explicitly
  controlled private network/trust relationship.
- `cloud_sanitized`: only context that passed the required sanitization/minimization policy
  may be sent.
- `deny`: no model egress.

An implementation must decide from bounded metadata such as classification, purpose,
destination class, policy version, tenant/resource scope where applicable, and sanitization
evidence. It must not need to persist the raw prompt to prove that the decision happened.

## 6. Sanitization and minimization

For `CONFIDENTIAL` data, cloud use requires an explicit policy and one or more of:

- remove fields not required for the task;
- replace direct identifiers with locally reversible tokens where re-identification is
  required only inside the trusted boundary;
- generalize/quasi-anonymize attributes such as exact age/location into bounded groups when
  suitable;
- prefer schemas, synthetic records, test fixtures, metadata, or aggregate statistics to raw
  records;
- enforce output filtering before cloud-generated material is persisted or acted upon;
- prevent secrets and authentication material from entering prompts regardless of masking.

Tokenization mappings stay local. Hashing is not accepted as anonymization for low-entropy
identifiers and must not be used as a pretext for retaining restricted values.

### Five representative examples

1. **Health analytics:** cloud AI may design SQL/report logic from a schema; raw patient
   identity and clinical records stay local.
2. **Payroll:** cloud AI may generate workflow code against synthetic employees; real salary,
   bank-account, tax, and identity records stay local.
3. **DevOps:** cloud AI may generate a deployment plan from redacted configuration; SSH keys,
   tokens, `.env`, and production secrets never enter the prompt.
4. **Legal/business:** cloud AI may review a sanitized template; privileged client material
   remains local unless an explicit lawful/provider posture allows otherwise.
5. **Government/citizen data:** cloud AI may operate on approved aggregate statistics or
   synthetic fixtures; raw identity and restricted public-sector records remain local.

## 7. RAG and embeddings

RAG is not a privacy bypass.

- Creating an embedding is data processing.
- Sending text to a cloud embedding API is data egress even if the final chat model never sees
  the original corpus directly.
- Retrieved chunks inherit the classification of their source unless a reviewed transformation
  explicitly changes it.
- Vector stores, caches, chunk metadata, filenames, and retrieval logs can disclose sensitive
  information and therefore require classification, access control, retention, and backup
  policy.
- `RESTRICTED` corpora default to local embedding + local vector storage + local retrieval.
- Any later cloud step receives only policy-approved sanitized context.

## 8. Agent and tool security

Model output is untrusted input. Prompt injection or a compromised model must not be able to
turn generated text into unrestricted host/API authority.

Required pattern:

```text
model proposal
    |
    v
deterministic policy
    |
    +-- deny
    +-- approval_required
    `-- allow --> fixed operation --> scoped executor --> verify/reconcile
```

Controls:

- reuse the existing OMES allowlisted job/pull-worker boundary;
- never expose arbitrary shell or arbitrary remote API execution;
- validate operation, resource, tenant/scope, idempotency, correlation, and approval policy;
- use least-privilege credentials referenced by name, never embedded in model-visible data;
- do not treat tool success, HTTP 202, timeout, or provider acknowledgement as final success
  without verification/reconciliation;
- sensitive/destructive actions require deterministic policy and, where defined, human
  approval.

## 9. Local-only restricted posture

Implementation is **not implemented yet (tracked in #215)**.

A restricted posture should:

- use a Hermes-supported local/self-hosted/OpenAI-compatible endpoint rather than a second
  OMES model router;
- preflight CPU/GPU/runtime requirements before mutation;
- verify endpoint locality/private reachability;
- bind inference services to loopback/private interfaces by default;
- fail rather than silently fall back to cloud;
- use non-root service identities and existing root/user separation;
- apply outbound deny/allowlist isolation where technically supportable without breaking
  documented administrative/update workflows;
- verify model/runtime provenance and avoid unverified `curl | bash` installers;
- make repeated apply idempotent and rollback remove only OMES-owned policy/configuration.

Local execution reduces provider exposure but does not remove host, supply-chain, admin,
telemetry, logging, backup, physical, or model-security risks.

## 10. Evidence, logging, and retention

Privacy posture evidence is **not implemented yet (tracked in #216)**.

Evidence should be bounded metadata only:

- policy version and classification mode;
- effective destination class: local/private/cloud/unknown;
- endpoint network classification;
- cloud-fallback enabled/disabled;
- required hardening/network-isolation state;
- verification timestamp/source;
- PASS/FAIL/WARN/BLOCKED and stable reason codes;
- Hermes/runtime/provenance versions where available.

Never persist raw prompts, outputs, chain-of-thought, retrieved documents, embeddings,
environment dumps, credentials, secret values, or sensitive identifiers merely to create
auditability.

"Not used for training" is not equivalent to:

- zero retention;
- no abuse-monitoring copy;
- no human access;
- no subprocessors;
- no cross-border transfer;
- no provider-side logging;
- no lawful-access exposure.

Provider review must assess these as separate properties.

## 11. Backup, recovery, and incident response

- Backup classification must follow source data classification.
- Restricted prompt/session data must not silently enter OMES default backups.
- Restoration must preserve access controls, ownership, retention, and encryption assumptions.
- An incident involving possible AI data egress should preserve evidence without copying the
  sensitive payload into tickets, PRs, or chat.
- Evidence should include policy/version, destination, timestamps, correlation IDs, affected
  scopes, and provider/runtime versions, with secrets redacted.
- Rotate affected credentials if exposure cannot be excluded.
- Reconcile provider retention/deletion obligations separately from local deletion.

## 12. Data residency and provider due diligence

Before approving a cloud destination, operators should document:

1. data categories and purpose;
2. controller/processor or equivalent roles;
3. retention and deletion behavior;
4. training/model-improvement policy;
5. abuse-monitoring and human-access conditions;
6. subprocessors and transfer locations;
7. encryption and tenant isolation;
8. contractual/DPA terms;
9. incident notification and audit evidence;
10. private networking/zero-retention options, where available.

Provider marketing language is not evidence of an OMES security control.

## 13. Standards and regulatory mapping

This mapping is directional engineering guidance, not certification.

| Reference | OMES relevance |
|---|---|
| ISO/IEC 27001:2022 | ISMS risk/control governance |
| ISO/IEC 27002:2022 | security control guidance |
| ISO/IEC 27005 | information-security risk management |
| ISO/IEC 27017:2026 | cloud-specific security responsibilities and controls |
| ISO/IEC 27018:2025 | PII protection for public-cloud processors |
| ISO/IEC 27034 series | application-security lifecycle |
| ISO/IEC 27701:2025 | privacy information management |
| ISO/IEC 42001:2023 | AI management system governance |
| ISO/IEC 23894:2023 | AI-specific risk management |
| ISO/IEC 15408 series | product/security-target evaluation concepts where applicable |
| ISO 22301 | continuity and recovery |
| ISO/IEC 20000-1 | service-management controls and evidence |
| NIST AI RMF 1.0 + NIST AI 600-1 | govern/map/measure/manage AI and GenAI risk |
| NIST SP 800-207 | zero-trust principles for service/identity boundaries |
| OWASP Top 10 for LLM/GenAI 2025 | prompt injection, sensitive disclosure, supply chain, output handling, excessive agency and related application risks |

Official references:

- https://www.iso.org/standard/42001.html
- https://www.iso.org/standard/77304.html
- https://www.iso.org/standard/27017.html
- https://www.iso.org/standard/27018.html
- https://www.iso.org/standard/27701.html
- https://www.nist.gov/itl/ai-risk-management-framework
- https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence
- https://csrc.nist.gov/pubs/sp/800/207/final
- https://genai.owasp.org/llm-top-10/

## 14. Indonesian legal/policy context

Applicable obligations depend on actor, sector, role, purpose, and actual processing.
Engineering review should include at least:

- **UU No. 27 Tahun 2022 tentang Pelindungan Data Pribadi** — privacy/data-processing
  obligations and heightened treatment of specific personal-data categories.
- **PP No. 71 Tahun 2019 tentang Penyelenggaraan Sistem dan Transaksi Elektronik** — system
  and electronic-transaction governance relevant to Indonesian electronic-system operators.
- **SE Menkominfo No. 9 Tahun 2023 tentang Etika Kecerdasan Artifisial** — AI ethics guidance
  for relevant business actors and public/private electronic-system operators.
- sector-specific rules, contracts, records-retention duties, and cross-border processing
  requirements applicable to the actual workload.

Official references:

- https://jdih.komdigi.go.id/produk_hukum/view/id/832/
- https://peraturan.bpk.go.id/Details/122030/Pp-No-71-Tahun-2019
- https://jdih.komdigi.go.id/produk_hukum/view/id/883/

This document deliberately does not make a blanket statement that a deployment is "compliant".
Legal interpretation and organizational accountability remain outside the scope of this
technical repository.

## 15. Implementation roadmap

The design decision in #213 is decomposed into independent, verifiable changes:

1. [#214](https://github.com/ahliweb/omes/issues/214) — machine-readable classification and
   egress policy.
2. [#215](https://github.com/ahliweb/omes/issues/215) — restricted local-only Hermes inference
   posture.
3. [#216](https://github.com/ahliweb/omes/issues/216) — privacy posture/evidence without raw
   prompt capture.
4. [#217](https://github.com/ahliweb/omes/issues/217) — sanitized Control Center projection.
5. [#218](https://github.com/ahliweb/omes/issues/218) — regression/exfiltration-resistance
   coverage.

Implementation order: **#214 -> #215/#216 -> #217 -> #218**. Parallelism is acceptable only
when contracts/files do not overlap.

## 16. Acceptance invariants

Any implementation claiming conformance with this architecture must preserve all of these:

- `RESTRICTED` defaults to local-only/deny-cloud.
- Secrets and authentication material never cross a model-cloud boundary.
- Missing/unknown policy state fails closed.
- Model output never directly grants authorization.
- No arbitrary shell/API execution is introduced.
- Raw restricted prompts are not audit evidence.
- Embeddings/RAG follow the same classification policy as source content.
- Local-only has no silent cloud fallback.
- OMES delegates model/provider routing to Hermes.
- Observed/effective posture is verified; configuration intent alone is not proof.
- Documentation distinguishes implemented controls from tracked future work.
