# OMES inference-serving boundary and future deployment readiness

> **Status: architecture guidance / design only.** Primary issue: [#291](https://github.com/ahliweb/omes/issues/291) (2026-10-09). This file does **not** announce a deployed inference service, a new model/provider router, or a new deployment backend.
>
> **vLLM: Not implemented yet (tracked in #289). Kubernetes/OpenShift: Not implemented yet (tracked in #290).** Both are explicitly **DEFERRED — DO NOT IMPLEMENT** until a maintainer records a separate go-decision on their issues. Generic endpoint observation is proposed separately in [#292](https://github.com/ahliweb/omes/issues/292); it is not yet shipped.

This is a *compatibility and ownership blueprint* informed by Red Hat's [AgentOps Unlocked Kubernetes demonstration](https://www.youtube.com/watch?v=6MuAOFfJk2w) and [companion repo](https://github.com/red-hat-ai-dev/agent-ops-series). That demonstration uses **LangGraph**, whereas OMES delegates agent behavior to **Hermes Agent**. Its chosen stack is **not** evidence of compatibility with Hermes or production readiness in OMES.

## 1. Non-negotiable architectural boundary

OMES is a host/deployment lifecycle and security-evidence toolkit; **it is not an inference gateway, agent scheduler or model router**. [ADR-0017](adr/0017-upstream-first-ownership-and-boundary-enforcement.md), [ADR-0018](adr/0018-runtimedeployment-v2-and-hermes-profile-references.md), [ADR-0022](adr/0022-consume-hermes-native-health.md), [ADR-0029](adr/0029-ai-data-boundary-and-private-inference.md), and [the existing agent-runtime boundary](agent-runtime-boundary.md) are authoritative. They already settle the trust boundary; this document introduces no new authority or ADR.

| Domain | Authority | OMES may do | OMES must not do |
| --- | --- | --- | --- |
| Agent identity, profile, reasoning, delegation, sessions, tools, MCP and provider/model selection | Hermes Agent | Consume released supported status/configuration interfaces; verify deployment posture | Persist or route prompts, provider API keys, model choices or tool calls; read Hermes private databases |
| Inference endpoint/model weights/serving behavior | The serving operator or external provider | Read-only, authorized host/service observations and OMES-owned service lifecycle **if separately implemented** | Claim model semantic correctness; install a serving stack without an approved adapter |
| Host OS, systemd and OMES-owned containers | OMES for its operations; OS/container engine for observed execution | Preflight, plan, backup, apply, verify, reconcile, health, recover/uninstall OMES-owned resources | Auto-escalate; mutate or delete unowned resources; expose privileged listeners |
| Kubernetes/OpenShift or Cloud AI provider | External platform/provider | Later, source-attributed observation or approved allowlisted adapter only | Claim cluster/provider authoritative state; automatically enable Kubernetes |
| Data security/egress policy evidence | Existing OMES AI-egress evaluator and responsible data controllers | Enforce host-side restrictions and record sanitized evidence | Let the model authorize data transfer or duplicate policy evaluators |
| Governance, tenants, approvals, Mission Control presentation | AWCMS / approved upstream records | Publish already-scoped, sanitized evidence/contracts | Introduce a second UI, authorization authority or operational source of truth |

### Conceptual relationships (not an installed topology)

```text
    Users via Hermes-supported channels             Authorized AWCMS operator
                      |                                        |
                      v                                        v
             Hermes Agent (upstream)                  Existing Control Center
         reasoning / sessions / tools                  approvals / safe jobs / UI
            |             |                                     |
            |             +----------------------+              |
            |       model/provider routing      |     allowlisted host operations
            v                                     v             v
     Local/remote model-serving              existing OMES host lifecycle/evidence
     endpoint (operator authority)           preflight / verify / audit / recovery
            |
     Ollama (existing host evidence)
     Other OpenAI-compatible endpoint (no new OMES adapter yet)
     vLLM (DEFERRED #289)
     Kubernetes/OpenShift placement (DEFERRED #290, platform authority)
```

**Hermes-native tool execution is not universally mediated by OMES.** Both direct Hermes channel access and upstream-native tool execution remain possible. An OMES host control job is **always** mediated by OMES policy/allowlists; neither diagram nor downstream UI should claim a universal interception boundary.

## 2. Verified baseline versus proposed capabilities

| Concern | Evidence / status | Reuse or next action |
| --- | --- | --- |
| Hermes runtime and deployment | Implemented: #85, #174, [RuntimeDeployment v2 schema](../contracts/agent/v2/runtime-deployment.schema.json) | Keep `runtime.kind: hermes`; do not encode inference engines as agent runtimes |
| Native/systemd/rootless Compose placement | Implemented: #87, #96; existing schema has `native`, `systemd`, `compose` | Do not add Kubernetes to production enum |
| Optional Coolify adapter | Implemented in OMES contracts/adapters: #97 | No second PaaS/scheduler |
| Ollama health evidence | Implemented: #71; [Ollama health implementation](../lib/omes/py/health/ollama.py) | Retain CLI and JSON compatibility; synthetic checks only |
| Hermes-native source-attributed health | Implemented: #79, #178, ADR-0022; [health data model](../lib/omes/py/health/model.py) | Reuse source, authority, prove/do-not-prove semantics |
| Local-only Restricted posture, egress, provenance and privacy | Implemented controls: #214–#218, #235–#237; [AI data policy](ai-data-privacy-and-model-security.md) | Do not duplicate classification, provider assurance or egress engine |
| Generic, non-mutating inference endpoint evidence | **Not implemented yet (tracked in #292)** | Separate versioned/sanitized contract and read-only adapter only after #291 review |
| vLLM hosting/deployment and benchmark | **Not implemented yet (tracked in #289)**; **DEFERRED** | Explicit user/maintainer go decision and evidence gates |
| Kubernetes/OpenShift backend / KServe | **Not implemented yet (tracked in #290)**; **DEFERRED** | Comparative ADR, compatibility/security/cost proof and explicit go decision |
| Cross-plane correlation/Mission Control additions | Existing ownership: #263–#277, several PRs open | Reuse the authoritative correlation/source projection; no duplicate telemetry store, event broker or UI |

The phrase *implemented* above refers to the named OMES baseline components on `main`; it does **not** mean vLLM, Kubernetes, generic endpoint discovery, or related hardware support has been tested.

## 3. Future neutral observation contract: design, not code

[#292](https://github.com/ahliweb/omes/issues/292) may consider additive read-only evidence using the existing `lib/omes/py/health/model.py` layer and existing contracts. Avoid introducing a distinct `inference-runtime` authority, conflicting state store, or model-router selector. The following fields are conceptual and **are not a shipped schema**:

| Evidence field | Semantics / default failure handling |
| --- | --- |
| `subject_ref`, `source_authority`, `observed_at`, `freshness` | Stable opaque scoped identity, provenance and temporal bounds; absent/stale -> `unknown` |
| `service_reachable`, `model_available` | Distinguish TCP/TLS success from valid API and configured model; never equate HTTP 200 to model readiness |
| `capability_results` | Per capability: `supported`, `unsupported`, `not_evaluated` based on safe evidence; model/template-sensitive |
| `health_status`, `proves`, `remediation` | Existing `pass`, `fail`, `not_applicable` plus explicit unknown mapping; accurately identify what a check does **not** prove |
| `endpoint_exposure`, `egress_policy_ref`, `artifact_provenance_ref` | Source-attributed references to existing network, AI privacy and provenance checks; do not copy sensitive payloads |
| `desired`, `observed`, `correlation_id`, `evidence_ref` | Only for OMES-owned host operations using current job/state/reconciliation contracts; unknown != success |

**Read-only probing rule:** default to metadata that requires no prompt and is obtainable through an existing supported and explicitly configured trusted endpoint. No active tool/chat request by default; synthetic fixtures only with an explicit opt-in. Use allowlisted URL schemes, local/private trust decisions, pinned expected destinations, DNS/redirect/IPv4/IPv6/SSRF protections, valid TLS certificates, bounded body sizes/timeouts, and no secrets in arguments, telemetry, state or output. If safe discovery or verification is unavailable, fail closed as `unknown` / `unsupported`, not guessed `healthy`.

For vLLM, upstream documentation specifically warns that `--api-key` does **not** protect every HTTP endpoint; it must not be treated as a sufficient perimeter control. See the [vLLM OpenAI-compatible API security notes](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/). This is a **future security requirement**, not permission to install vLLM.

## 4. Host service lifecycle principle (when individually authorized)

OMES-owned resources would follow:

```text
DETECT -> PREFLIGHT -> VALIDATE -> PLAN/DIFF -> BACKUP
       -> APPLY -> VERIFY -> RECORD -> HEALTH CHECK
Failure: CLASSIFY -> PRESERVE EVIDENCE -> SAFE ROLLBACK
       -> VERIFY ROLLBACK -> REPORT EXACT STATE
```

Key constraints:

1. Unsupported OS/backend/model-serving *adapter* must fail before mutation. The agent runtime remains Hermes; serving engines must never be inserted into `runtime.kind`.
2. Only explicit, allowlisted, scoped, idempotent operations may mutate **OMES-owned** resources. Keep root/user scope separate; never auto-`sudo` or execute untrusted downloaded scripts.
3. Host service deployment must verify artifact provenance and version, resource/port prerequisites, restricted-data posture, resource ownership, and roll-forward/rollback boundaries before apply.
4. The caller's model/provider routing settings stay with Hermes. OMES must not rewrite a Hermes profile to choose vLLM.
5. Provider acknowledgement, HTTP `202`, process status or exit code is **not** proof of a healthy model. Reconcile observed service, model/capability evidence, provenance and security posture, marking missing evidence unknown.
6. Undo only owned resources and always verify post-rollback state. Unsupported hardware/runtime is a non-mutating `BLOCKED` outcome.

No executable service deployment, Kubernetes manifest, endpoint enrollment or additional active CLI command is specified by this document.

## 5. Five practical deployment cases

| Use case | Status / present approach | Required evidence before enabling new work |
| --- | --- | --- |
| A. Single Ubuntu/Mint developer, Ollama | Existing Ollama host checks (#71) + Hermes provider/runtime separation (#79). Model choice remains Hermes-owned | Model/capability-specific synthetic check; local exposure, RAM/VRAM and privacy posture |
| B. Private compatible endpoint operated outside OMES | Hermes config may reference a supported endpoint; OMES generic observer is **not implemented (#292)** | Valid released Hermes API compatibility and safe observation source, trust roots and endpoint ownership |
| C. Multi-user shared inference service | External operator owns inference; no OMES-managed serving adapter is assumed | Benchmark latency/TTFT/p95, throughput, isolation/quotas, failure modes and SLO |
| D. Cloud model with local restricted-data plane | Existing classification/egress rules and provider assurance may apply; do not send `RESTRICTED` records to cloud | Sanitization/aggregate re-identification analysis, egress policy decision, provider assurance evidence |
| E. vLLM server or Kubernetes/OpenShift GPU cluster | **Both deferred:** Not implemented yet (tracked in #289); Not implemented yet (tracked in #290) | Explicit go decision, measured workload need, separate threat/ADR review, hardware/licensing and disposable integration tests |

## 6. Future admission criteria; never implicit activation

Before #289 or #290 may proceed, record: (i) an explicit operator/maintainer authorization on the relevant issue; (ii) a comparative workload and TCO study against Ollama/systemd/rootless Compose and current optional Coolify; (iii) a threat model for endpoint auth, unrestricted routes, SSRF, tenancy, secrets, model supply chain, prompt/tool data, GPU exhaustion, backup/log exposure and rollback; (iv) a pinned tested Hermes release/model/API feature matrix; (v) test evidence (static/security -> unit -> safe fixtures -> disposable host/container -> GPU host or cluster where claims require it); (vi) a reviewed ADR if crossing an existing trust/deployment boundary.

For vLLM benchmark *only after authorization*: compare the same or comparable model weights/quantization, tokenizer/chat templates and prompt workloads; measure TTFT, output tokens/s, error rate, p95/p99, concurrency scaling, tool-calling accuracy, GPU memory, power and total cost per successful result. Never assert vLLM is more intelligent than Ollama solely because of serving throughput.

For Kubernetes *only after authorization*: require multi-node HA/GPU scheduling need, supported cluster versions, least-privileged service account/RBAC, tenant namespaces, device plugins, admission/resource quotas, NetworkPolicy enforcement, verified TLS/secrets, workload recovery, and non-destructive uninstall of owned resources.

### Applicable control references

Use [OMES security](security.md) and [threat model](threat-model.md) for mandatory controls. Relevant standards for mapping, not certification claims: ISO/IEC 27001, 27002, 27005, 27017, 27018, 27034, 27701, 20000-1, 15408, 42001, 23894, ISO 22301, NIST AI RMF, and applicable Indonesian UU 27/2022 (personal data protection), PP 71/2019 (electronic systems), plus health-sector regulations when patient data is involved.

### Related work and sequencing

1. **Now:** document and review this neutral boundary via #291 (no changes to existing runtime schemas/enums).
2. **After review:** optionally implement secure generic inference observation in #292 as a *separate* issue, branch and PR.
3. **Not now:** keep #289 vLLM and #290 Kubernetes/OpenShift OPEN with **DEFERRED — DO NOT IMPLEMENT** until explicitly unblocked.
4. Existing #269/#272/#276/#277 and #263/#264/PR #268 own cross-plane trace, isolation and Mission Control; extend existing sources only after their coupled work merges.

Reference implementations and docs:
- [Red Hat AgentOps demo source](https://github.com/red-hat-ai-dev/agent-ops-series) (not an OMES runtime).
- [Ollama API compatibility](https://docs.ollama.com/api/openai-compatibility).
- [vLLM OpenAI-compatible serving](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/).
- [Kubernetes GPU device plugins](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/).
