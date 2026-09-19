# OMES Coolify adapter (issue #97)

> Status: contracts and a fake-provider implementation are delivered in this
> repository. There is no live Coolify HTTP integration exercised by CI, no
> wiring into `omes job`'s fixed operation table, and no `spec.backend:
> coolify` field in the agent-deployment manifest yet. Every claim below is
> qualified as implemented-here vs. left for follow-up work; see section 7.

## 1. Purpose and boundary

[`docs/agent-orchestration-roadmap.md`](agent-orchestration-roadmap.md) section
2.3 defines Coolify as an **optional, later-phase deployment backend** for
multi-server OMES deployments - not the OMES runtime, not a billing engine,
and not a second source of truth for anything OMES already owns. This
document is the implementation-level companion to that section: what the
adapter actually does, what it deliberately does not do, and the operational
guidance (when to pick Coolify, token scope, network exposure) an operator
needs before enabling it.

[`AGENTS.md`](../AGENTS.md) section 2 states the same boundary as a
non-negotiable product authority: "Coolify is an optional later deployment
adapter. It is not the OMES runtime, billing engine, or source of truth."
This document does not redefine that boundary; it implements it.

## 2. When Coolify is appropriate

OMES ships three deployment backends on a deliberate staged path
(`docs/agent-orchestration-roadmap.md` section 2):

| Backend | When to use it | Status |
|---|---|---|
| Native systemd (user or system unit) | The default. A single host, or a small number of independently administered hosts, each running its own OMES install. No shared control plane needed. | Implemented (issues #79-#87). |
| Rootless Docker Compose | A single host running several isolated agent workloads that benefit from container isolation, without needing a multi-server control plane. | Not implemented yet (tracked in #96). |
| Coolify adapter (this document) | Multiple servers whose container deployments should be centrally visible/managed through Coolify's own UI/API - e.g. an operator who already runs Coolify for other workloads and wants OMES-managed agent deployments to show up there too, or who needs Coolify's proxy/domain and build-history features across more than one node. | Contracts and a fake-provider implementation only (this issue, #97); no live integration. |
| Nomad/Kubernetes | Evidence-gated only - measured multi-node scheduling, HA, autoscaling, or multi-tenant requirements that neither of the above meets. | Not evaluated; see roadmap section 2.5. |

Do not reach for Coolify by default. It adds an external control-plane
dependency (Coolify's own Laravel/PostgreSQL/Redis/realtime stack, which
OMES explicitly does not embed - roadmap section 2.3) and a second place
deployment state can live. Pick it only when the multi-server/centralized-UI
need is real, not speculative.

## 3. Source-of-truth split

Exactly as stated in `docs/agent-orchestration-roadmap.md` section 2.3 and
`AGENTS.md` section 2, and enforced structurally by
[`contracts/coolify/v1/observed-state.schema.json`](../contracts/coolify/v1/observed-state.schema.json)
and
[`contracts/coolify/v1/reconciliation.request.schema.json`](../contracts/coolify/v1/reconciliation.request.schema.json):

**OMES remains the source of truth for:**

- logical agent/deployment identity;
- role and capability policy;
- secret references (never secret values);
- data classification;
- health contract;
- backup policy;
- runtime compatibility;
- desired deployment intent (the mapping itself: which Coolify
  project/environment/resource a logical deployment is bound to).

**Coolify remains the source of truth for the delegated resource's:**

- external resource ID;
- target server;
- container deployment status;
- build/deployment history;
- proxy/domain configuration;
- platform-specific logs (metadata only - see section 5).

`lib/omes/py/coolify/reconcile.py`'s `observation_patch` is restricted, by
both the JSON Schema (`additionalProperties: false` over exactly the
observed-state key set) and a runtime check, to the Coolify-owned list
above. It is structurally impossible for a reconciliation patch to carry an
OMES logical field (role, policy, secret_ref, entitlement, backup policy) -
there is no property for it in the schema, and `reconcile.py`'s
`validate_patch()` rejects the whole patch (not a partial merge) if one
sneaks in anyway.

## 4. The mapping

`docs/agent-orchestration-roadmap.md` section 2.3's illustrative manifest
shape:

```yaml
spec:
  backend: coolify
  coolify:
    instance: coolify-production
    project: agent-platform
    environment: production
    resource: researcher-worker
```

is implemented on the OMES side as
[`contracts/coolify/v1/mapping.schema.json`](../contracts/coolify/v1/mapping.schema.json)
and `lib/omes/py/coolify/mapping.py`. A mapping binds one OMES logical
`deployment_id` to exactly one `(instance_id, project, environment,
resource)` tuple on exactly one registered Coolify instance, with an
immutable `correlation_id` and `created_at` recorded at registration time.

**Fail closed on missing or ambiguous mapping.** Both the schema and
`mapping.py`'s `require_mapping_fields()` require each of `instance_id`,
`project`, `environment`, and `resource` to be present and a single
non-empty string. A missing field, an empty string, or a field holding a
list of candidates (an ambiguous match - e.g. two Coolify resources
matching a name search) is rejected before any deployment operation is
attempted; nothing in this adapter guesses which of several matches was
meant. Re-mapping a deployment to a different Coolify resource is a
deliberate `remap()` call that records a brand-new `correlation_id`, not an
in-place mutation of the existing mapping record.

## 5. Deployment operations and observed state

`lib/omes/py/coolify/provider.py` defines the adapter interface:
`apply`, `status`, `health`, `redeploy`, `rollback`, and `observed_state`,
matching
[`contracts/coolify/v1/deployment.request.schema.json`](../contracts/coolify/v1/deployment.request.schema.json)'s
closed `operation` enum. `lib/omes/py/coolify/fake.py`'s
`FakeCoolifyProvider` is the only implementation in this repository; it is
in-memory, deterministic, and used by default and by every test in
`tests/py/coolify/`.

- **Idempotency.** Every mutating operation (`apply`, `redeploy`,
  `rollback`) takes an `idempotency_key`. A repeated call with the same key
  returns the exact same result and never triggers a second simulated
  deploy - the same idempotency discipline as
  `lib/omes/py/jobs/store.py`'s job submission.
- **Rollback fails closed.** `rollback_ref` must be a Coolify deployment
  UUID this adapter has actually observed for the resource (from a prior
  `apply`/`redeploy`/`rollback`'s `external.deployment_uuid`). A
  `rollback_ref` this adapter has never seen raises
  `RollbackReferenceUnknownError` rather than being sent through to a
  provider that might invent a target.
- **Never claim success from an accepted-but-unreconciled deploy.** Per
  `AGENTS.md` section 3 ("Do not treat a timeout or accepted asynchronous
  request as success"), `result: "accepted"` in
  `deployment.response.schema.json` means only that Coolify queued the
  work; a subsequent `status`/`health` read is required before reporting
  `applied`/`healthy`.
- **Observed state is metadata only.** `logs_metadata` in
  `observed-state.schema.json` has no field for a log body, content, or
  line array - only `available`, `ref`, `last_line_at`, `byte_count`. A
  provider implementation structurally cannot return a raw log body
  through this contract; retrieving actual log content, if ever needed, is
  a separate, explicitly authorized read path, not something that flows
  through observed-state reconciliation.
- **Every delegated operation is audited.** `provider.py`'s `_audit()`
  helper appends one record per operation to `lib/omes/py/coolify/audit.py`'s
  hash-chained, redacted log at `<state-dir>/coolify/audit.jsonl`. The
  record shape (`ts`, `actor`, `job_id`, `event`, `from`, `to`, `detail`,
  `prev_hash`, `line_hash`) is a deliberate copy of
  `lib/omes/py/jobs/audit.py`'s shape (see that module's docstring for why
  packages copy rather than import this helper), so every OMES audit log -
  jobs, content, coolify - is readable and verifiable the same way.

## 6. Coolify API surface used, least-privilege token scope, and network exposure

### 6.1 Endpoints

`lib/omes/py/coolify/client.py` calls only the following endpoints,
verified against the Coolify OpenAPI specification
(<https://github.com/coollabsio/coolify>, `openapi.yaml`) and
<https://coolify.io/docs/api/overview> (base URL `https://<instance>/api/v1`,
self-hosted or `https://app.coolify.io/api/v1` for Coolify Cloud; Bearer
token authentication):

| Purpose | Method and path |
|---|---|
| List applications | `GET /applications` |
| Get an application | `GET /applications/{uuid}` |
| Trigger a deployment | `POST /deploy?uuid=...&force=...` |
| List currently running deployments | `GET /deployments` |
| Get a deployment | `GET /deployments/{uuid}` |
| List an application's deployments | `GET /deployments/applications/{uuid}` |
| Roll back an application | `POST /applications/{uuid}/rollback` (body `{"commit": "<deployment_uuid_or_commit>"}`) |
| List servers | `GET /servers` |
| Get a server | `GET /servers/{uuid}` |
| List projects | `GET /projects` |
| Get a project | `GET /projects/{uuid}` |
| Get a project environment | `GET /projects/{uuid}/{environment_name_or_uuid}` |

No other endpoint is called. `client.py`'s module docstring repeats this
exact list so the client and its documentation cannot silently drift apart.

### 6.2 Least-privilege token scope

- Register one Coolify API token per registered instance, scoped to the
  narrowest role Coolify offers that can still perform the operations
  above (application read/deploy/rollback and server/project read). Do not
  reuse a Coolify root/owner token for the OMES adapter if Coolify's
  token/team permission model allows a narrower one.
- OMES never stores the token. `contracts/coolify/v1/instance-registration.request.schema.json`'s
  `credential_ref` names only the environment variable the token lives in
  (`{"store": "env", "key": "OMES_COOLIFY_TOKEN_<INSTANCE_ID>"}` by
  convention); the generic secret-reference ban in
  `lib/omes/py/jobs/schema.py` (reused by `scripts/check-contracts.py`
  across every contract area, including `contracts/coolify/v1/`) rejects
  any raw scalar under a field whose name matches
  `token|password|secret|credential|...`.
- `client.py` reads the token from that named environment variable at call
  time (`os.environ[token_env_var]`) - never from argv, never from a
  request body field, never logged. Every error path redacts
  `Authorization:`/`Bearer <token>`/`TOKEN=...`-shaped text before it
  becomes an exception message (`lib/omes/py/coolify/audit.py`'s
  `redact_text()`, reused by `client.py`).
- Rotate the token by updating the environment variable the credential
  reference names; OMES holds no copy to rotate.

### 6.3 Network exposure policy

- `client.py` makes **zero** network calls unless the environment variable
  `OMES_COOLIFY_LIVE=1` is set. This is checked inside `_request()` before
  any URL is built or any socket opened, so an accidental invocation in a
  test, CI job, or default operator session cannot reach a real Coolify
  instance. `tests/py/coolify/test_client.py` patches `urllib.request.urlopen`
  and asserts it is never called when this flag is unset.
- `instance-registration.request.schema.json`'s `base_url` must be an
  explicit `https://` URL; OMES never falls back to plaintext HTTP for a
  Coolify API call.
- OMES does not open an inbound listener for Coolify to call back into; all
  calls in this adapter are outbound (OMES to Coolify). Coolify's own proxy
  (Traefik/Caddy) and any inbound exposure of the deployed workload itself
  are Coolify's responsibility and configuration, not this adapter's - see
  `proxy_domain_config` in `observed-state.schema.json`, which OMES only
  *observes*, never sets.

## 7. What remains (not implemented in this repository yet)

- **Live Coolify HTTP integration.** `client.py` exists and is unit-tested
  against mocked `urllib` calls, but no test in this repository exercises a
  real Coolify instance (`OMES_TEST_REAL_COOLIFY`-style opt-in real
  integration test does not exist yet). Treat `client.py` as
  contract-correct-by-inspection against the OpenAPI spec, not as
  field-proven, until it has been run with `OMES_COOLIFY_LIVE=1` against a
  real instance. Tracked as follow-up work under #97.
- **`omes job` operation-table wiring.** `lib/omes/py/jobs/runner.py`'s
  `build_argv()` fixed operation table (issue #90) does not yet dispatch a
  control-center `deployment.request` whose target resolves to a
  `coolify`-mapped resource into this adapter. Wiring that in is follow-up
  work once both this chain and the jobs chain are on `main`.
- **`spec.backend: coolify` in the agent-deployment manifest.** The
  manifest/schema work (`contracts/agent/v1/`, `lib/omes/py/agent/`) lives
  on a separate branch stack (issues #85/#87/#96) that this PR's base does
  not include. This PR intentionally does not touch `contracts/agent/v1/`
  or `lib/omes/py/agent/`. Wiring `spec.backend: coolify` (and the
  `spec.coolify{instance,project,environment,resource}` fields from
  `docs/agent-orchestration-roadmap.md` section 2.3) into that manifest is
  explicitly left for a follow-up once both branch stacks have landed on
  `main`.
- **A live provider implementation.** Only `fake.py` exists. A
  `client.py`-backed `CoolifyProvider` implementation (translating
  `apply`/`status`/`health`/`redeploy`/`rollback` into the endpoint calls
  in section 6.1) is follow-up work; `provider.py`'s abstract interface is
  written so that implementation can be added without changing the
  contracts or the fake-provider test suite's expectations.
- **AWCMS/Control Center producer side.** As with every other
  `contracts/<area>/v1/` boundary (see `contracts/README.md`), the
  producer of `coolify` deployment requests (the Control Center, when
  implemented) does not exist in this repository. AWCMS-one owns the web
  GUI, tenant scope, and any UI for registering a Coolify instance or
  viewing its mappings/drift reports; this repository owns only the
  contracts, the OMES-side adapter, and the fake-provider tests.
- **Instance registry persistence beyond a single JSON file.**
  `lib/omes/py/coolify/paths.py`'s `instances_path()` is defined, but no
  code in this PR reads or writes it yet (no `register-instance` CLI
  command). Only the contract
  (`instance-registration.request.schema.json`) and the path layout exist.

## 8. Related documents

- [Agent orchestration roadmap section 2.3](agent-orchestration-roadmap.md)
- [Control Center and integrations](control-center-and-integrations.md)
- [ADR-0011: Control Center and provider boundaries](adr/0011-control-center-and-provider-boundaries.md)
- [Security baseline](security.md)
- [Threat model](threat-model.md)
- [Testing conventions](testing.md)
- [`contracts/coolify/v1/`](../contracts/coolify/v1/)
