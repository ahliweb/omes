# AI Data Privacy and Model Security

> Status: **authoritative design and policy; runtime enforcement is partially implemented**.
> The architecture decision is recorded in [ADR-0029](adr/0029-ai-data-boundary-and-private-inference.md).
> The machine-readable data-classification and egress-policy contract and its deterministic
> evaluator are implemented ([#214](https://github.com/ahliweb/omes/issues/214)); see
> [contracts/ai-egress/v1](../contracts/ai-egress/v1/) and
> [lib/omes/py/privacy/egress_policy.py](../lib/omes/py/privacy/egress_policy.py). The Restricted
> local-only deployment posture ([#215](https://github.com/ahliweb/omes/issues/215)) is implemented
> for the Hermes SYSTEM gateway: `modules/hermes-restricted/module.sh` verifies (via
> [lib/omes/py/privacy/restricted_posture.py](../lib/omes/py/privacy/restricted_posture.py), which
> delegates the actual decision to `egress_policy.py` rather than duplicating it) that the
> configured Hermes model endpoint is local/private before allowing apply, and denies outbound
> network access from the gateway unit by default when applied. A read-only AI privacy
> posture/egress evidence surface is also implemented
> ([#216](https://github.com/ahliweb/omes/issues/216)); see `omes health ai-privacy`,
> [contracts/ai-egress/v1/privacy-posture-evidence.schema.json](../contracts/ai-egress/v1/privacy-posture-evidence.schema.json),
> and [lib/omes/py/privacy/posture_evidence.py](../lib/omes/py/privacy/posture_evidence.py), as is
> its sanitized Control Center projection
> ([#217](https://github.com/ahliweb/omes/issues/217); see
> [lib/omes/py/privacy/posture_projection.py](../lib/omes/py/privacy/posture_projection.py)).
> Negative regression and exfiltration-resistance coverage is also implemented
> ([#218](https://github.com/ahliweb/omes/issues/218); see
> `tests/py/privacy/test_privacy_boundary_regression.py`). Structured provider-assurance evidence
> (retention, human access, subprocessors, residency) for the section 5 checklist is also
> implemented ([#237](https://github.com/ahliweb/omes/issues/237); see
> [contracts/ai-egress/v1/provider-assurance-evidence.schema.json](../contracts/ai-egress/v1/provider-assurance-evidence.schema.json)),
> gating `cloud_sanitized` approval in the same evaluator.
>
> This document is engineering guidance for OMES. It does **not** claim legal compliance,
> regulatory approval, ISO certification, Common Criteria certification, or that a local model is
> automatically secure.

## 1. Purpose

OMES integrates Hermes Agent as its automation layer. AI workloads may use either a local/private
model endpoint or a cloud model. This document defines the security and privacy boundary for those
workloads so that sensitive data is not sent to an AI provider merely because a model is useful.

The central rule is:

> **A model is untrusted intelligence, not a security authority. Sensitive-data access, model
> egress, authorization, approval, and host mutation are controlled by deterministic policy outside
> the model.**

The preferred architecture separates:

- a **sensitive data plane**, which stays local/private unless an explicit policy permits otherwise;
- a **reasoning/model plane**, owned by Hermes, which may use local/private or cloud inference;
- a **policy/enforcement plane**, where OMES verifies host posture, applies host-side isolation,
  evaluates machine-readable policy metadata, and records sanitized evidence;
- a **business/control plane**, owned by AWCMS/Control Center when implemented, for tenant policy,
  approvals, and durable business state.

## 2. Validation of the two primary patterns

The two security patterns proposed for OMES are valid, but they solve different risks.

### 2.1 Pattern A — local-only/private inference

Sensitive prompts and context are processed by a local or operator-controlled model endpoint.

```text
Hermes
  |
  | OpenAI-compatible API
  v
local/private inference endpoint
  |
  v
local/private data and tools
```

Hermes already supports custom/self-hosted OpenAI-compatible endpoints, including local-model
deployments. OMES therefore must **not** build a second model router. OMES may provision, harden,
verify, monitor, back up configuration for, or remove an OMES-owned local inference service when a
future implementation issue explicitly adds that lifecycle.

This pattern reduces model-provider data disclosure, but does not remove risks from host compromise,
malicious dependencies/models, prompt injection, insecure tools, logs, backups, administrators,
network exposure, or model supply chain.

### 2.2 Pattern B — local sensitive-data plane with cloud reasoning

A cloud model can assist with architecture, code, workflow generation, or reasoning while raw
sensitive records remain in the local/private system.

```text
cloud model
    ^
    | approved minimized context only
    |
application-owned sanitization/tokenization
    ^
    |
local policy / authorization / executor
    ^
    |
sensitive data store
```

Examples of cloud-safe context may include:

- a schema without real records;
- synthetic test data;
- opaque token identifiers whose mapping remains local;
- bounded aggregate statistics with re-identification risk reviewed;
- public or explicitly releasable metadata;
- a task specification that describes how local code should process data without providing the data.

This is preferred over sending raw confidential records whenever cloud reasoning is sufficient to
design or generate the local automation.

## 3. Product ownership and non-duplication

| Capability | Authority |
|---|---|
| Reasoning, sessions, memory, skills, delegation, tools, model/provider selection and routing | **Hermes Agent** |
| Host compatibility, install/lifecycle, hardening, service/network posture, local evidence, rollback, provenance | **OMES** |
| Tenant/business privacy policy, approvals, durable control-plane state | **AWCMS/Control Center**, when implemented |
| Application-specific sensitive records and sanitization semantics | The **owning application/data controller**, not generic OMES logic |
| Provider-side retention, training, subprocessors, data location and model service behavior | The **external provider**, subject to contract/configuration and verification |

Per [ADR-0017](adr/0017-upstream-first-ownership-and-boundary-enforcement.md), OMES must delegate
model/provider routing to Hermes and communicate only through supported upstream interfaces. It must
not inspect private Hermes databases or introduce a second agent runtime.

## 4. Data classification

The initial OMES policy uses four classes. Machine-readable classification and egress policy is
implemented as [contracts/ai-egress/v1/egress-decision-request.schema.json](../contracts/ai-egress/v1/egress-decision-request.schema.json),
[contracts/ai-egress/v1/egress-decision-response.schema.json](../contracts/ai-egress/v1/egress-decision-response.schema.json),
and the deterministic evaluator in
[lib/omes/py/privacy/egress_policy.py](../lib/omes/py/privacy/egress_policy.py) (#214). The
evaluator is metadata-only: it never receives, logs, or persists prompt text, response text,
embeddings, retrieved documents, or credential values, and it performs no network I/O.

| Class | Typical examples | Cloud-model default |
|---|---|---|
| **PUBLIC** | Published documentation, public source code intended for disclosure, public web content | Allowed subject to provider and purpose policy |
| **INTERNAL** | Non-public operational documentation, ordinary internal telemetry, non-sensitive configuration metadata | Allowed only to approved providers/purposes; minimize first |
| **CONFIDENTIAL** | Proprietary source/code context, contracts, customer/business information, identifiable personal data not classified Restricted | Deny by default; only sanitized/tokenized/minimized context through an explicit approved policy |
| **RESTRICTED** | Credentials, API tokens, private keys, authentication material, raw health/biometric/genetic data, highly sensitive personal/financial records, cryptographic secrets, incident secrets | **Local/private only; cloud denied by default** |

Classification is about the **content and processing risk**, not the filename, storage location, or
whether a user typed it manually.

Unknown or missing classification is treated as **RESTRICTED for egress decisions** until resolved.

## 5. Model-destination classes

A policy decision distinguishes model destinations rather than merely naming a vendor.

- **local_only** — loopback/local runtime; no model request leaves the host.
- **private_endpoint** — operator-controlled private network/VPC/on-prem endpoint with separately
  assessed transport, identity, retention, and operations.
- **cloud_sanitized** — approved external model service receiving only policy-approved minimized
  content.
- **deny** — no model processing is permitted for the requested content/purpose.

The existence of TLS, a private API key, a paid enterprise account, or a statement that data is
“not used for training” does not by itself make a destination equivalent to local-only processing.
Specifically, a provider's "not used for training" claim does **not** by itself imply:

- zero retention;
- no abuse-monitoring copy;
- no human access;
- no subprocessors;
- no cross-border transfer;
- no provider-side logging;
- no lawful-access exposure.

Each of these must be assessed and recorded as a separate property; none may be inferred from
another.

Before approving any external model service as a destination, and before treating a
`cloud_sanitized`/`private_endpoint` decision as durable, operators must separately record, where
applicable:

1. data categories and purpose;
2. controller/processor (or equivalent) roles;
3. retention and deletion behavior;
4. use for training/model improvement;
5. abuse-monitoring and human/operations access conditions;
6. subprocessors and transfer/geographic processing locations;
7. encryption in transit/at rest and tenant isolation;
8. contractual/DPA terms and applicable legal basis;
9. incident notification and audit evidence;
10. private-networking/zero-retention options, where available.

Provider marketing language is not evidence of an OMES security control. This checklist is the
single canonical provider-assurance list for this document; do not create a second one elsewhere.

**Implemented ([#237](https://github.com/ahliweb/omes/issues/237)):** this 10-item checklist is
recorded as a versioned, structured `provider_assurance` object -
[contracts/ai-egress/v1/provider-assurance-evidence.schema.json](../contracts/ai-egress/v1/provider-assurance-evidence.schema.json)
documents the canonical shape, and the same shape is embedded directly in
[contracts/ai-egress/v1/egress-decision-request.schema.json](../contracts/ai-egress/v1/egress-decision-request.schema.json)
(duplicated rather than `$ref`'d, because `scripts/check-contracts.py`'s stdlib validator subset
does not support `$ref` - the two must be kept in sync by hand). Each item records a bounded
`status` (`verified`/`not_verified`/`unknown`/`not_applicable`) plus an optional opaque
`evidence_ref` pointer (e.g. a ticket or DPA register entry) - never the underlying document,
contract text, or provider response. `lib/omes/py/privacy/egress_policy.py`'s `evaluate()` consults
this object for any `cloud_sanitized` destination: items 1-9 must all be `verified` (item 10,
private-networking/zero-retention, may instead be `not_applicable` when the provider offers no such
option); a missing `provider_assurance` object or any incomplete item is a hard, fail-closed `deny`
(`AI_EGRESS_DENY_MISSING_PROVIDER_ASSURANCE` / `AI_EGRESS_DENY_PROVIDER_ASSURANCE_INCOMPLETE`) that
overrides an otherwise-approved decision. The assurance record must also be bound to the exact
destination provider: `provider_posture.provider_id` must be present and exactly equal
`provider_assurance.provider_id`, or the request is denied
(`AI_EGRESS_DENY_PROVIDER_ASSURANCE_MISMATCH`) - adequate due diligence recorded for one provider
must never approve egress to a different provider. None of this ever turns an existing deny into an
allow, and it never weakens the unconditional RESTRICTED-to-`cloud_sanitized` denial. See
`tests/py/privacy/test_egress_policy.py`'s `TestProviderAssuranceGate` and
`tests/py/privacy/test_privacy_boundary_regression.py`'s
`TestProviderAssuranceIsNeverInferredFromAProviderClaim` and
`TestProviderAssuranceMustBeBoundToTheDestinationProvider` for the exhaustive regression coverage.

**Residual limits, stated honestly:** this is caller-recorded attestation evidence, not independent
verification. The evaluator has no way to confirm that a `verified` status is actually true of the
provider - it only enforces that someone recorded the due-diligence claim in the required
structured shape before egress can be approved, and that a provider's own prose claim cannot
satisfy an item (only the closed status enum counts). It also does not itself enforce evidence
freshness/staleness (an `assessed_at` timestamp is recorded but no expiry policy is evaluated yet)
or automatically detect that a provider's actual practice has drifted from its recorded assurance -
both remain an operator process, not an automated OMES control, until a future issue adds them.

## 6. Decision matrix

Default rules:

| Data class | local_only | private_endpoint | cloud_sanitized |
|---|---:|---:|---:|
| PUBLIC | Allow | Allow | Allow subject to provider policy |
| INTERNAL | Allow | Allow subject to policy | Allow only when minimized and provider is approved |
| CONFIDENTIAL | Allow | Explicit policy | Deny by default; explicit policy + sanitization evidence required |
| RESTRICTED | Allow | Explicit policy and equivalent private controls | **Deny** |

A future exception for Restricted data requires a separate reviewed ADR, legal/privacy basis,
explicit operator authorization, and controls proportionate to the data. No implementation may
silently turn a denied decision into an approval prompt.

## 7. Sanitization, tokenization and aggregation

Sanitization is application/domain specific. OMES may evaluate **evidence that required sanitization
occurred**, but generic OMES code must not pretend that a regex makes arbitrary domain data safe.

Preferred techniques, depending on risk:

- remove unnecessary fields before model invocation;
- replace direct identifiers with high-entropy opaque tokens whose mapping stays local;
- generalize values where exact precision is not needed;
- aggregate records where re-identification risk remains acceptable;
- use synthetic data for coding/testing;
- redact secrets with deterministic secret-handling controls;
- enforce output validation before any returned model text reaches an executor.

Tokenization is not anonymization if the local mapping still exists. Hashing low-entropy personal
values is also not sufficient anonymization because they may be brute-forced or joined with other
data.

## 8. RAG, embeddings and vector stores

RAG does not create a privacy exemption.

The following are data-processing steps and inherit the source data classification:

- document chunking;
- embedding generation;
- vector storage;
- retrieval;
- reranking;
- prompt assembly;
- model inference;
- logging or observability around any of those steps.

For Restricted workloads, embedding and vector operations default to local/private processing.
Sending a document to a cloud embedding endpoint is external processing even when the resulting
vector database is local.

Retrieved context must pass the same egress decision as directly supplied prompt context.

## 9. Agent and tool security

AI agents have a larger attack surface than chat-only use because model output can influence tools.

OMES requirements:

1. Model output is **untrusted input**.
2. Authorization is deterministic and outside the model.
3. A model must never generate an unrestricted shell/API operation that is executed directly.
4. Mutations use existing typed, allowlisted OMES jobs with scope, correlation/idempotency,
   approval where required, verification, and audit.
5. Prompt injection must not alter the deterministic egress policy.
6. Tool results are minimized before being returned to any external model.
7. Secrets are supplied by references or scoped runtime mechanisms, never inserted into prompts.
8. A timeout/provider acknowledgement is never interpreted as verified success.

These rules align with existing #192 fixed-argv pull-worker controls and the sanitized Hermes
orchestration projection from #183.

## 10. Local-only runtime posture

The target Restricted posture, tracked in [#215](https://github.com/ahliweb/omes/issues/215) and
implemented by `modules/hermes-restricted/module.sh` (opt-in; requires
`modules/hermes-gateway-system` to already be applied), is:

| Requirement | Status |
|---|---|
| Hermes uses a supported local/self-hosted endpoint | **Implemented.** `module_check`/`module_apply`/`module_verify` all read only the documented `hermes config get model` / `hermes config get providers.<id>.base_url` keys (via the supported `HERMES_HOME` redirection, never `sudo -u`, never a second provider router) and classify the endpoint's network locality without any network I/O of its own. |
| No silent cloud fallback | **Implemented.** A non-`allow` decision (deny or approval_required) is a hard `module_check`/`module_apply`/`module_verify` failure. OMES never substitutes, retries against, or defaults to a cloud endpoint. |
| Local inference service runs non-root | **Partially implemented / scope boundary.** OMES does not install, select, or run a model server itself (non-goal: no custom model scheduler). The Hermes gateway process this module hardens already runs under `hermes-gateway-system`'s dedicated non-root service account; verifying the non-root identity of an operator's own separately-managed local inference server (e.g. Ollama) is **not implemented yet**. |
| Endpoint binds to loopback/private interface by default | **Verified, not configured.** This module verifies the endpoint classifies as local/private; it does not itself configure or bind a model server's listen address (that server is not OMES-owned - see scope boundary above). |
| Service/network egress is denied or narrowly allowlisted where technically compatible | **Implemented for the gateway unit.** `module_apply` writes a `40-omes-restricted-network.conf` systemd drop-in (`IPAddressDeny=any` / `IPAddressAllow=localhost link-local` plus any operator-approved ranges) on the `hermes-gateway` system unit. This is an **all-or-nothing, whole-unit** policy - every process in that unit's cgroup loses outbound network access, not just model calls; see the module's own `module_apply` confirmation prompt for this trade-off (a gateway that also needs internet access for browser automation or other MCP tools is not compatible with this policy). Requires systemd >= 235; older systemd is logged as an advisory warning, not enforced. |
| Model/runtime artifacts have provenance and integrity evidence | **Not implemented yet** (tracked in #215 follow-on work; existing host-level provenance in `docs/compatibility-evidence.md` does not yet cover model/runtime artifact hashes). |
| GPU/CPU compatibility is checked before mutation | **Implemented, advisory only.** `module_check` reports CPU core count, total memory, and GPU tooling presence (`nvidia-smi`/`rocm-smi`/`/dev/dri`) read-only; it warns below a configurable memory threshold but never blocks on hardware alone (only the endpoint-locality check blocks). |
| Logs avoid prompt bodies and secret values | **Implemented.** Only the bounded evidence fields section 11 allows (endpoint-locality bucket, destination class, decision, reason codes) are logged or persisted to state - never a raw endpoint URL, model name, or credential. |
| Reinstallation is idempotent | **Implemented.** A repeated `module_apply` re-evaluates the posture and rewrites the same drop-in content; no new resources are created. |
| Backup/restore follows classification and ownership rules | **Implemented for this module's own state; not yet extended repository-wide.** `module_rollback` removes only the OMES-managed `40-omes-restricted-network.conf` drop-in and this module's own state keys - never `hermes-gateway-system`'s own drop-ins, the Hermes account, or its data. Broader Restricted-classified backup/restore policy (section 12) is **not implemented yet**. |
| Drift from local-only to cloud-capable is reported as failure/action-required | **Implemented.** `module_verify` re-runs the same endpoint-locality check on every verification pass; a decision change away from `allow` is reported as a hard failure ("DRIFT DETECTED"), never a silent pass-through. |

This table reflects `modules/hermes-restricted/module.sh` and
[lib/omes/py/privacy/restricted_posture.py](../lib/omes/py/privacy/restricted_posture.py) as of
#215; it must be updated in the same change as any behavior change to either.

**Integration point between #215 and #216's evidence surface (implemented):**
`modules/hermes-restricted/module.sh` writes exactly the two state keys `omes health ai-privacy`
([lib/omes/py/health/ai_privacy.py](../lib/omes/py/health/ai_privacy.py), issue #216) reads. Both
sides use the same closed vocabulary (`lib/omes/py/privacy/posture_evidence.py`'s
`LOCAL_ONLY_SOURCE_STATUSES`), and the reader only ever touches OMES's own state — never a Hermes
internal file:

- `ai.local_only_posture.available` (`"true"`/`"false"`) and `ai.local_only_posture.status`
  (`pass`/`fail`/`warn`/`unknown`) — written by `module_apply` and refreshed by every
  `module_verify` run (so drift is visible to `omes health ai-privacy` without waiting for the
  next apply), mapping `egress_policy.py`'s `allow`/`deny`/`approval_required` decision onto
  `pass`/`fail`/`warn` respectively. A configured legacy Hermes `fallback_model` (see below) always
  forces `status=fail`, independent of the primary endpoint's own decision. When the keys are
  absent (the module was never applied), `omes health ai-privacy` reports
  `local_only_posture.available = false`, which — combined with a declared `restricted_local_only`
  expected posture — is evaluated as `BLOCKED`, never as a healthy default (see
  `lib/omes/py/privacy/posture_evidence.py`).
- `ai.privacy.expected_posture` — the operator-declared target posture
  (`restricted_local_only`/`unrestricted`), settable via `OMES_AI_PRIVACY_EXPECTED_POSTURE` or
  `state_set ai.privacy.expected_posture <value>`, and set to `restricted_local_only` by
  `module_apply` (applying that module is the explicit operator declaration of that intent).
  `module_rollback` clears it, but only when the value is still exactly what the module set — an
  operator's own independent declaration is never clobbered. Absent/unset reads as `unknown` and
  never upgrades a report to a healthy status on its own.

**Cloud-fallback detection (implemented, honestly bounded):** both surfaces read the same two
non-secret Hermes config keys, documented at
[hermes-agent.nousresearch.com/docs/user-guide/features/fallback-providers](https://hermes-agent.nousresearch.com/docs/user-guide/features/fallback-providers),
through the same supported, read-only `hermes config get` interface — never `$HERMES_HOME/.env`,
never a file under `.hermes/`, never `messages.db`. `modules/hermes-restricted/module.sh` reads
them the same safe way it reads `model`/`providers.<id>.base_url`; `omes health ai-privacy` reads
them through the vetted allowlist in `lib/omes/py/provenance/versions.py`
(`ALLOWED_HERMES_CONFIG_KEYS`).

- **`fallback_model`** — the legacy scalar, in the same `provider/model` shape as `model`. For
  #215, a non-empty value means "fallback configured": it fails `module_check`/`module_apply` and
  drifts `module_verify` even when the primary endpoint is otherwise local. For #216, a value
  whose provider classifies as cloud reports `cloud_fallback_enabled = "enabled"`, which under a
  declared `restricted_local_only` posture is a `FAIL`
  (`AI_PRIVACY_POSTURE_FAIL_CLOUD_FALLBACK_ENABLED_UNDER_RESTRICTED_POSTURE`).
- **`fallback_providers`** — the newer **list**-valued key. Neither Hermes's fallback nor
  configuration documentation specifies what `hermes config get fallback_providers` actually prints
  for a list-valued path (the configuration docs note that `hermes config get` on some paths
  "prints the value from your file together with a stderr notice that Hermes may not read it"), so
  neither surface parses it, rather than guess at an undocumented, possibly multi-line format.
  `omes health ai-privacy` reads it for **presence only**: a present, non-empty value reports
  `"unknown"`, never `"disabled"`. `modules/hermes-restricted/module.sh` does not read it at all,
  so a fallback configured *only* via `fallback_providers` (and never via the legacy
  `fallback_model`) is not detected by that module's check — a real, documented gap.

For `omes health ai-privacy`, `"disabled"` is reported only when `fallback_model` is confirmed
unset (or names a provider that classifies as local) **and** `fallback_providers` is confirmed
unset. Every other outcome — a provider outside the bounded local/cloud vocabulary, a bare model
name with no provider prefix, a missing `hermes` binary, a non-zero exit, an unrecognized
`config get` subcommand, or a timeout — is `"unknown"`. Ambiguity is never reported as
`"disabled"`.

## 11. Audit and evidence

Privacy evidence should prove policy/posture without retaining the protected content.

Allowed examples:

- policy version;
- data class;
- destination class;
- policy decision and stable reason code;
- sanitization method/evidence identifier, not the source record;
- provider/profile identifier that is not a credential;
- local endpoint network classification;
- cloud-fallback enabled/disabled;
- hardening/isolation state;
- actor/service identity, tenant/resource scope where applicable;
- correlation/idempotency identifier;
- verification timestamp and source;
- PASS/FAIL/WARN/BLOCKED.

Prohibited evidence:

- raw prompts or responses for Restricted workloads;
- chain-of-thought;
- full chat transcripts;
- secrets, API keys, private keys, cookies or tokens;
- raw medical/personnel/financial records;
- raw provider responses that may contain credentials or personal data.

**Implemented ([#216](https://github.com/ahliweb/omes/issues/216)):** `omes health ai-privacy`
reports exactly this bounded evidence shape — see
[contracts/ai-egress/v1/privacy-posture-evidence.schema.json](../contracts/ai-egress/v1/privacy-posture-evidence.schema.json)
and [lib/omes/py/privacy/posture_evidence.py](../lib/omes/py/privacy/posture_evidence.py). Every
free-text-shaped field (`evidence_source`, `hermes_version_reference.source`,
`local_only_posture.source`) is drawn from a closed vocabulary rather than accepting arbitrary
text, and any unexpected/unrecognized input key is silently dropped rather than echoed — this is
the structural control that keeps a caller from smuggling prompt/secret content through this
surface, proven by adversarial canary-value tests in `tests/py/privacy/test_posture_evidence.py`
and `tests/py/health/test_ai_privacy.py`. Unknown or stale evidence (a missing/too-old
`observed_at`) is reported `BLOCKED`, never a healthy status, and a drift from a declared
`restricted_local_only` posture to an observed `cloud` destination is always `FAIL`.

**Retention:** this evidence is intentionally NOT persisted by OMES today — `omes health
ai-privacy` is a point-in-time read-only report (matching the existing `omes health`/`omes health
versions` pattern), not a stored log. If a caller chooses to persist the JSON output (for example,
piping `omes health ai-privacy --json` into an operator-controlled log or ticket), the same
retention rules as any other bounded evidence apply: keep only what is operationally useful,
apply an explicit retention/deletion period, and never widen it into a place raw prompt/response
content could later be pasted "for context." Automated evidence retention/rotation tooling is
**Not implemented yet (tracked in [#234](https://github.com/ahliweb/omes/issues/234)).**

**Incident-response use:** when investigating a suspected AI privacy-posture incident (for
example, a report that a Restricted-local-only workload may have reached a cloud destination),
responders should run `omes health ai-privacy --json` and preserve ONLY that JSON evidence object
— `status`, `reason_codes`, `destination_class`, `local_endpoint_classification`,
`cloud_fallback_enabled`, `network_isolation_active`, `last_verified_at`, `evidence_source`, and
`local_only_posture` — as evidence in the ticket/PR/incident record. Consistent with section 12
below, responders must NOT paste prompt/response text, `hermes doctor` raw output, `.env`
contents, or any other unbounded artifact into the same record; if a `FAIL` (especially
`AI_PRIVACY_POSTURE_FAIL_DRIFT_LOCAL_ONLY_TO_CLOUD`) is observed, treat it as signal to rotate any
credential that may have been exposed to the unexpected destination (section 12) and to re-run
`omes health ai-privacy` after remediation to confirm the evidence returns to `PASS` with a fresh
`last_verified_at`.

## 12. Backup, recovery, and incident response

Backup and incident-response handling for AI-adjacent data follows the same classification rules
as the data itself; a backup or recovery path is not a separate trust boundary that resets
classification.

- **Backup classification follows source classification.** A backup, snapshot, or replica of
  Restricted or Confidential prompt/session/context data inherits that same classification; it
  does not become less sensitive because it is stored as a backup artifact.
- **Restricted prompt/session data must not silently enter default backups.** OMES-owned backup
  jobs must not sweep up Restricted-class prompt, session, transcript, or context data as a side
  effect of a general/default backup scope. Inclusion requires an explicit, reviewed policy
  decision, not an accidental glob match.
- Restoration must preserve the access controls, ownership, retention, and encryption assumptions
  that applied to the original data — a restore must not weaken protection relative to the source.
- **Incident evidence must be preserved without copying sensitive payloads into tickets, PRs, or
  chat.** When an incident may involve AI data egress (for example, a suspected Restricted-class
  prompt reaching a cloud destination), responders preserve evidence by reference — policy/version,
  destination class, timestamps, correlation/idempotency identifiers, affected scope, and
  provider/runtime versions, with secrets and sensitive content redacted — rather than pasting raw
  prompts, transcripts, or credentials into an issue, pull request, or chat message.
- **Rotate affected credentials on exposure.** If a credential, API key, or other authentication
  material cannot be excluded from having been exposed to a model, log, backup, or unauthorized
  party, rotate it. Do not treat "we deleted the record" as equivalent to rotation.
- **Provider-side retention/deletion is reconciled separately from local deletion.** Deleting or
  purging local copies of prompt/session data does not delete any provider-side copy, cache, log,
  or abuse-monitoring retention. Provider-side retention and deletion obligations must be verified
  against that provider's stated terms and, where required, an explicit deletion request — never
  assumed to have completed because the local OMES/Hermes state was cleared.

Section 11's `omes health ai-privacy` evidence surface (#216) supports the incident-evidence
practice above (preserve the bounded JSON report by reference, never raw content). Automated
backup-scope *enforcement* (actively preventing a default backup job from sweeping up
Restricted-class prompt/session data) is **not implemented yet (tracked in [#235](https://github.com/ahliweb/omes/issues/235))** (it is
out of #215's scope, which covers the Hermes gateway's local-only inference posture, not backup
enforcement); today the
backup-scope guidance in this section remains operating guidance for anyone handling a backup or
restore of AI-adjacent data.

## 13. Control Center projection

AWCMS/Control Center may display privacy posture and policy decisions, but must not become a prompt
archive or secret store.

The OMES-side wire contracts and pure evaluation logic for this projection are delivered (issue
#217): [`contracts/control-center/v1/ai-privacy-posture-view.schema.json`](../contracts/control-center/v1/ai-privacy-posture-view.schema.json),
[`ai-egress-approval.request.schema.json`](../contracts/control-center/v1/ai-egress-approval.request.schema.json)/[`.response.schema.json`](../contracts/control-center/v1/ai-egress-approval.response.schema.json),
and `lib/omes/py/privacy/posture_projection.py`; see
[docs/control-center-contracts.md](control-center-contracts.md) section 2.10 for the full field set
and authority split. They show:

- current privacy mode (`classification_mode`);
- data class (`latest_decision.classification`);
- local/private/cloud destination class (`destination_class`, `latest_decision.destination`);
- allow/deny/approval-required decision (`latest_decision.decision`);
- reason code (`reason_codes`, `latest_decision.reason_codes`);
- evidence authority and freshness (`authority`, `evidence_freshness`);
- drift and remediation status (`status`, e.g. `AI_PRIVACY_POSTURE_FAIL_DRIFT_LOCAL_ONLY_TO_CLOUD`).

**The AWCMS-side screen, API, and database that would consume these contracts are not implemented
yet (tracked in [#232](https://github.com/ahliweb/omes/issues/232))** - this repository only fixes the projection's wire
shape and the OMES-side authorization backstop (cross-tenant denial, the RESTRICTED-cloud approval
block), both implemented under #217 (closed; commit `ce44b0a`, PR #231).

Tenant/RBAC/ABAC/RLS authorization remains server-side in AWCMS; UI hiding of a field is never a
substitute for that check. OMES nodes continue using the outbound pull-worker model
(`ai-privacy-posture.changed`/`ai-egress-approval.recorded` events) rather than exposing a new
privileged listener.

## 14. Threats addressed

| Threat | Primary control |
|---|---|
| Raw sensitive records sent to a cloud model | Classification + default-deny egress policy |
| Cloud fallback from a Restricted local workload | Local-only posture + drift verification |
| Prompt injection changes the security decision | Deterministic policy outside the model |
| Agent uses model output as shell/API instructions | Typed allowlisted jobs; no arbitrary execution |
| Secrets leak through logs/audit/Control Center | Content-minimized evidence + existing redaction |
| RAG/embedding silently exports Restricted documents | Same classification propagated through the RAG pipeline |
| Local model is assumed safe despite compromised host/model | Host hardening, provenance, non-root execution, supply-chain checks |
| Provider marketing claim is treated as a full privacy guarantee | Structured, versioned provider-assurance fields for training, retention, access, subprocessors, transfer, gating `cloud_sanitized` approval fail-closed (#237) |
| Restricted prompt/session data silently swept into a default backup | Backup classification follows source classification; explicit inclusion policy required |
| Sensitive payload pasted into an incident ticket/PR/chat as "evidence" | Evidence preserved by reference (policy, destination, timestamps, correlation ID), never raw payload |

Regression coverage is implemented ([#218](https://github.com/ahliweb/omes/issues/218), closed;
commit `ce44b0a`, PR #231; see `tests/py/privacy/test_privacy_boundary_regression.py`).

## 15. Indonesia legal/privacy context

This is an engineering mapping, not legal advice.

### UU No. 27 Tahun 2022 tentang Pelindungan Data Pribadi

Relevant requirements include:

- Article 4 classifies health information, biometric data, genetic data, criminal records,
  children's data, and personal financial data as specific personal data.
- Article 34 requires a data-protection impact assessment for high-risk processing, including
  specified categories such as automated decision-making with significant effect, specific
  personal data, large-scale processing, systematic monitoring, dataset matching, and use of new
  technology.
- Articles 35–39 require technical/operational safeguards, confidentiality, supervision of parties
  involved in processing, protection against unlawful processing, and prevention of unauthorized
  access.
- Article 56 regulates transfer of personal data outside Indonesia.

Source: https://jdih.komdigi.go.id/produk_hukum/view/id/832/

The OMES Restricted classification is intentionally conservative for the specific-data categories
above, but the legal controller/processor must still determine lawful basis, purpose, retention,
data-subject obligations, transfer requirements, and DPIA requirements.

### PP No. 71 Tahun 2019 tentang PSTE

PP 71/2019 remains an applicable Indonesian electronic-system framework and reinforces the need for
reliable, secure, and accountable electronic-system operation.

Source: https://peraturan.bpk.go.id/Details/122030/pp-no-71-tahun-2019

### SE Menkominfo No. 9 Tahun 2023 tentang Etika Kecerdasan Artifisial

The circular provides ethical guidance for AI-related policy, consultation, analysis, and
programming by public/private electronic-system operators and AI businesses. OMES uses it as a
governance reference, not as a substitute for binding privacy/security law.

Source: https://jdih.komdigi.go.id/produk_hukum/view/id/883/

Sector-specific rules remain the responsibility of the deploying organization and application
owner. Health, financial, government, education, and other regulated workloads may require
additional controls beyond this baseline.

## 16. International standards and security frameworks

The mapping below guides engineering; it does not imply certification.

| Standard/framework | OMES relevance |
|---|---|
| ISO/IEC 42001:2023 | AI management-system governance, risk/opportunity, accountable AI lifecycle |
| ISO/IEC 23894:2023 | AI-specific risk management |
| ISO/IEC 27001:2022 | Information-security management and risk-based control framework |
| ISO/IEC 27002 | Security control guidance supporting the ISMS |
| ISO/IEC 27005 | Information-security risk management |
| ISO/IEC 27017 | Cloud-specific security controls/guidance |
| ISO/IEC 27018 | Protection of PII in public-cloud processing |
| ISO/IEC 27034 | Application-security governance and controls |
| ISO/IEC 27701:2025 | Privacy Information Management System requirements/guidance |
| ISO/IEC 20000-1 | Service-management governance for operated AI services |
| ISO 22301 | Business continuity for local/private AI dependencies |
| ISO/IEC 15408 | Security evaluation concepts for components/products where applicable |
| NIST AI RMF + NIST AI 600-1 | Govern/Map/Measure/Manage AI risk and GenAI-specific risks |
| NIST SP 800-207 | Resource-centric zero-trust and least-privilege access |
| OWASP Top 10 for LLM/GenAI 2025 | Prompt injection, sensitive information disclosure, supply chain, improper output handling, excessive agency, vector/embedding weaknesses |

Authoritative references:

- https://www.iso.org/standard/42001
- https://www.iso.org/standard/77304.html
- https://www.iso.org/standard/27001
- https://www.iso.org/standard/27701
- https://www.nist.gov/itl/ai-risk-management-framework
- https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence
- https://csrc.nist.gov/pubs/sp/800/207/final
- https://genai.owasp.org/llm-top-10/

## 17. Hermes upstream compatibility

Validated upstream capabilities at the time of this decision:

- Hermes supports local/self-hosted OpenAI-compatible endpoints and local models.
- Hermes owns model/provider selection and routing.
- Hermes documents security layers including user authorization, dangerous-command approval,
  file-write safety, container isolation, credential filtering, context scanning, session
  isolation, and input sanitization.
- Hermes provider routing may expose provider-specific data-collection controls, but those controls
  are provider-specific and do not replace OMES/application classification.

References:

- https://hermes-agent.nousresearch.com/docs/integrations/providers
- https://hermes-agent.nousresearch.com/docs/user-guide/local-models
- https://hermes-agent.nousresearch.com/docs/user-guide/features/provider-routing
- https://hermes-agent.nousresearch.com/docs/user-guide/security

Upstream changes must be revalidated through the existing OMES upstream-drift process. A feature
seen only on upstream development branches is not treated as supported release evidence.

## 18. Implementation sequence

1. **#214** — machine-readable data classification and egress policy. Implemented: see
   [contracts/ai-egress/v1](../contracts/ai-egress/v1/) and
   [lib/omes/py/privacy/egress_policy.py](../lib/omes/py/privacy/egress_policy.py).
2. **#215** — Restricted local-only inference deployment posture. Implemented: see
   `modules/hermes-restricted/module.sh` and
   [lib/omes/py/privacy/restricted_posture.py](../lib/omes/py/privacy/restricted_posture.py)
   (section 10's table has the per-requirement detail).
3. **#216** — privacy posture, drift and egress evidence without prompt capture. Implemented: see
   `omes health ai-privacy`,
   [contracts/ai-egress/v1/privacy-posture-evidence.schema.json](../contracts/ai-egress/v1/privacy-posture-evidence.schema.json),
   and [lib/omes/py/privacy/posture_evidence.py](../lib/omes/py/privacy/posture_evidence.py). The
   #215 local-only posture source is integrated via the two state keys section 10 documents.
4. **#217** — sanitized Control Center projection and policy-decision contracts. Implemented
   (OMES side): see [lib/omes/py/privacy/posture_projection.py](../lib/omes/py/privacy/posture_projection.py).
   AWCMS-side consumption remains not implemented yet (tracked in [#232](https://github.com/ahliweb/omes/issues/232)).
5. **#218** — negative/regression tests for disclosure and policy bypass. Implemented: see
   `tests/py/privacy/test_privacy_boundary_regression.py`.
6. **#237** — structured provider-assurance evidence (threat AI-07) gating `cloud_sanitized`
   approval. Implemented: see
   [contracts/ai-egress/v1/provider-assurance-evidence.schema.json](../contracts/ai-egress/v1/provider-assurance-evidence.schema.json)
   and the `provider_assurance` gate in
   [lib/omes/py/privacy/egress_policy.py](../lib/omes/py/privacy/egress_policy.py).

Each issue follows one issue → one branch → one pull request and must preserve rollback,
idempotency, evidence, and upstream-first ownership.

## 19. Practical examples

### Example 1 — coding against a patient database

Cloud AI receives table/field semantics and synthetic rows. The real patient database remains
local. Generated SQL/code is reviewed and executed by a local application under its own database
authorization.

### Example 2 — monthly health aggregate

Local code calculates authorized aggregate statistics. Only the approved aggregate, if policy
allows, may be supplied to a cloud model to help draft narrative interpretation.

### Example 3 — DevOps configuration

A cloud model can generate a systemd or Ansible template from requirements, but SSH keys, provider
tokens, `.env` values, private certificates, and production database credentials remain local.

### Example 4 — confidential contract workflow

The cloud model receives a synthetic or tokenized clause structure rather than the full signed
contract when the contract is Confidential and the policy does not allow its external processing.

### Example 5 — Restricted incident response

Incident secrets, forensic credentials, private keys, raw authentication logs containing protected
identifiers, and exploit evidence classified Restricted are processed only with a local/private
model posture; cloud fallback fails closed.

## 20. Security invariants

The following are non-negotiable:

- unknown classification fails closed;
- secrets are never cloud-model prompt material;
- Restricted data is local/private by default;
- no silent provider fallback;
- no model-generated arbitrary shell/API execution;
- no raw Restricted prompt/transcript storage as audit evidence;
- RAG/embedding follows source-data classification;
- provider trust is explicitly assessed rather than inferred from “not used for training” (which
  does not by itself imply zero retention, no abuse-monitoring copy, no human access, no
  subprocessors, no cross-border transfer, no provider-side logging, or no lawful-access exposure);
- backups and restores follow source-data classification and never silently widen Restricted-data
  exposure;
- provider-side retention/deletion is reconciled separately from local deletion, never assumed;
- OMES does not duplicate Hermes model/provider routing;
- implementation claims require tests and repository evidence.
