# Agent deployment (native OMES + Hermes + systemd)

> Status: describes the actual repository state on this branch (issue
> [#87](https://github.com/ahliweb/omes/issues/87)). This is the MVP
> backend from [docs/agent-orchestration-roadmap.md](agent-orchestration-roadmap.md):
> native systemd only. The rootless Docker Compose and Coolify phases
> described there are **not implemented**. Ubuntu Server 24.04 VM/CI
> evidence is **not included in this PR** (no VM available in this
> environment) - see "Left for follow-up" at the end of this document.

## 1. What this is

`omes agent` (`lib/omes/cmd/agent.sh`, `lib/omes/py/agent/`) manages the
declared lifecycle of a **Hermes-runtime agent deployment** as a systemd
service, isolated by logical name. It does not reimplement any part of
Hermes itself (reasoning, messaging, memory, skills, model/provider
routing - see AGENTS.md section 2); it only declares, plans, applies,
verifies, backs up, and rolls back the *deployment* of an already-
installed Hermes instance pointed at an agent-specific `HERMES_HOME`.

It reuses, rather than duplicates:

- [#79](https://github.com/ahliweb/omes/issues/79)'s layered health
  model (`lib/omes/py/health/`) for the health/readiness aggregation;
- [#81](https://github.com/ahliweb/omes/issues/81)'s hardening
  directives (`modules/hermes-gateway/hardening.sh`'s `hardening_render`)
  for the security portion of the per-agent systemd drop-in;
- [#82](https://github.com/ahliweb/omes/issues/82)'s Hermes data-class
  backup engine (`lib/omes/py/hermesbackup/`) for the backup step of
  `apply`.

## 2. Manifest

A manifest is a JSON file (not YAML - ADR-0012 avoids adding a YAML
dependency) at `$OMES_CONFIG_DIR/agents/<name>.json` (see
[docs/configuration.md](configuration.md) for `OMES_CONFIG_DIR`'s
resolution). Its shape mirrors
[docs/agent-orchestration-roadmap.md section 4](agent-orchestration-roadmap.md#4-proposed-manifest-boundary)
field-for-field, and is validated against the versioned schema at
[`contracts/agent/v1/agent-deployment.schema.json`](../contracts/agent/v1/agent-deployment.schema.json)
(valid/invalid examples under
[`contracts/agent/v1/fixtures/agent-deployment/`](../contracts/agent/v1/fixtures/agent-deployment/)):

```json
{
  "apiVersion": "omes.ahliweb.com/v1",
  "kind": "AgentDeployment",
  "metadata": {
    "name": "researcher",
    "workspace": "ahliweb",
    "environment": "production"
  },
  "spec": {
    "runtime": "hermes",
    "profile": "researcher",
    "role": "generic",
    "backend": "systemd",
    "serviceMode": "user",
    "restartPolicy": "always",
    "capabilities": ["web-search", "document-read"],
    "deny": ["docker-control", "firewall-write"],
    "resources": { "memory": "1G", "cpu": "1.0", "pids": 128 },
    "health": { "command": "omes agent health researcher", "timeout": "10s" },
    "storage": { "memory": "private", "sessions": "isolated", "skills": "managed" },
    "secrets": ["provider-primary"]
  }
}
```

The manifest never contains a secret **value** - `spec.secrets` is a
list of reference **names** only (schema-enforced pattern), never an API
key, token, or password. The MVP validator (`lib/omes/py/agent/manifest.py`,
using the tiny stdlib schema engine in `lib/omes/py/agent/jsonschema_lite.py`
— **not** `scripts/check-contracts.py`, which is a different agent's
tool for `contracts/control-center/v1`) also enforces, beyond what the
schema's regexes alone express:

- `metadata.name` must match `^[a-z][a-z0-9-]{1,31}$` and contain no
  path-traversal or separator characters (defence in depth: it is also
  used to derive the systemd unit name and the isolated `HERMES_HOME`);
- `spec.runtime` must be `hermes` (the MVP's only supported runtime -
  any other value fails preflight before any mutation, per issue #87's
  acceptance criteria);
- `spec.backend` must be `systemd` (the only implemented backend);
- `spec.serviceMode` must be `user` or `system`;
- a manifest file's `metadata.name` must match the filename it is looked
  up under (`<name>.json`) - a mismatch is rejected as a duplicate/
  confused declaration rather than silently accepted under either name.

## 3. CLI

```text
omes agent list                          # every declared/applied agent + state
omes agent check <name>                  # preflight only: manifest + privilege, no mutation
omes agent plan <name>                   # prints the computed plan (unit, paths, limits), no mutation
omes agent apply <name> [--dry-run] [--yes]
omes agent status <name> [--json]
omes agent health <name> [--json]
omes agent restart <name>
omes agent logs <name>                   # journalctl passthrough, scoped to the agent's unit
omes agent rollback <name> [--yes]
```

`omes agent` is an **optional extension command**
([lib/omes/cmd/README.md](../lib/omes/cmd/README.md),
[docs/cli.md section 4.12](cli.md)); it is never referenced by any
installer profile. All lifecycle logic is stdlib Python
(`lib/omes/py/agent/cli.py`, ADR-0012); `lib/omes/cmd/agent.sh` is a
thin dispatcher plus the `logs` passthrough (which needs no Python
round-trip).

### 3.1 Privilege

- `serviceMode: user` **must not** be applied as root; it is applied by
  the target operator account via `systemctl --user`.
- `serviceMode: system` **requires** root to apply (writing to
  `/etc/systemd/system`), matching the existing
  `modules/hermes-gateway-system` non-root service-user policy - the
  running agent process itself is never root, only the installation
  step is. OMES never auto-escalates with `sudo`.

A privilege mismatch fails with exit 5 before any mutation.

### 3.2 `apply`: check -> plan -> backup -> mutate -> verify

1. **check**: load + validate the manifest; verify privilege matches
   `serviceMode`; verify `systemctl` is on `PATH`.
2. **plan**: pure computation (`lib/omes/py/agent/plan.py`) of the unit
   name (`omes-agent-<name>.service`), the agent's isolated
   `HERMES_HOME` (`~/agents/<name>/hermes` for `user` mode; a
   root-owned equivalent tree for `system` mode - never shared between
   agents), the resource-limit drop-in lines, and the secret
   **reference names** (never values).
3. **backup**: if the agent's `HERMES_HOME` already exists (i.e. this is
   not the first apply), calls `lib/omes/py/hermesbackup`'s `create`
   (issue #82's default classes: `config`, `skills`) scoped to that
   `HERMES_HOME`. A fresh agent with no prior `HERMES_HOME` has nothing
   to back up yet - not an error.
4. **mutate**: writes the unit file and a resource/hardening drop-in
   (`10-omes-agent-resources.conf`), reloads systemd, and
   `enable --now`s the unit.
5. **verify**: confirms the unit is active; then runs the health model
   once more and records `healthy` or `degraded` accordingly.

`--dry-run` runs steps 1-2 only and prints the planned unit name, paths,
environment **references** (never secret values), and resource limits;
it makes no filesystem or systemd mutation and writes no state. `apply`
without `--yes` requires an interactive y/N confirmation; with neither a
TTY nor `--yes` it refuses (matching every other OMES mutating command).

Re-running `apply` against an already-`ready`/`healthy`/`degraded`
deployment restarts the same cycle from `preflighted` rather than being
rejected - this is what makes `apply` idempotent (issue #87's
acceptance criterion): the unit/drop-in content is deterministic from
the manifest, and `systemctl enable --now`/`daemon-reload` are
themselves idempotent.

### 3.3 Unit and drop-in content

The unit file's `ExecStart` runs Hermes's own gateway entrypoint
(`hermes gateway start --home <agent HERMES_HOME>`) - OMES never
reimplements the tool-execution loop or messaging. A secret reference,
if any is declared, becomes `EnvironmentFile=-<HERMES_HOME>/.env` (the
leading `-` makes a missing file non-fatal); OMES never writes that
file's contents itself.

The resource/hardening drop-in combines two things, one reused and one
this issue's own:

- the **hardening** directives (`NoNewPrivileges`, `ProtectSystem=full`,
  etc.) are produced by calling
  `modules/hermes-gateway/hardening.sh`'s existing `hardening_render`
  function (via a small `bash -c 'source ...; hardening_render ...'`
  bridge in `lib/omes/py/agent/hardening_bridge.py`) rather than
  duplicating those directives - default profile `conservative`,
  overridable with `OMES_AGENT_HARDENING=off|conservative|strict`
  (mirrors `OMES_HERMES_HARDENING`'s values, see
  [docs/hermes-hardening.md](hermes-hardening.md), but is a separate
  variable since it governs a different unit);
- the **resource limits** (`MemoryMax`/`MemoryHigh`/`CPUQuota`/
  `TasksMax`) and **restart policy** lines come directly from the
  manifest's `spec.resources`/`spec.restartPolicy` and always take
  precedence over any same-named directive `hardening_render` also
  emits (de-duplicated at render time).

### 3.4 Health

`omes agent health <name>` reuses `lib/omes/py/health`'s layered model
(`lib/omes/py/agent/health.py`): the **gateway** layer checks
`systemctl is-enabled`/`is-active` for this agent's own unit (not the
shared `hermes-gateway` unit); the **provider** and **channel** layers
are the exact functions #79 already implements
(`check_provider`/`check_channel`), reused unmodified. Every check is
read-only; the channel layer only ever calls Telegram's `getMe`/
`getWebhookInfo` (never `getUpdates`/`setWebhook`/`deleteWebhook` -
issue #87's explicit requirement, inherited from #79).

### 3.5 Rollback

`omes agent rollback <name>` stops and disables the unit and removes
**only** the paths this tool itself wrote (the unit file and its
drop-in, tracked in the agent's own state file) - it never touches
`HERMES_HOME` or any file an operator or Hermes itself created. State
moves to `rolled-back`.

## 4. State and provenance

Per-agent state lives at
`<state-dir>/agents/<name>/state.json` (root: `/var/lib/omes`; user:
`${XDG_STATE_HOME:-$HOME/.local/state}/omes`, same resolution as every
other OMES state, see [docs/configuration.md](configuration.md)),
written atomically (temp file + `os.replace`, mode `0600`). It records:

- `state`: the current lifecycle state (section 5 below);
- `history`: a bounded (last 50) list of `{from, to, at, detail}`
  transitions;
- `managedPaths`: exactly the paths `rollback` is allowed to remove;
- `provenance`: `omesVersion` (from `VERSION`), `gitRef` (`git rev-parse
  HEAD` at apply time, best-effort), `hermesVersion` (`hermes --version`
  output, best-effort) - **no credentials**, ever.

## 5. Lifecycle states

```text
declared -> preflighted -> planned -> backed-up -> applied -> verified -> ready -> healthy
```

Failure states, reachable from any in-progress state:
`degraded | failed | rolled-back`. See
[docs/agent-orchestration-roadmap.md section 5](agent-orchestration-roadmap.md#5-lifecycle-contract)
for the full contract this implements
(`lib/omes/py/agent/lifecycle.py`).

## 6. Isolation

Every agent gets its own `HERMES_HOME`, its own systemd unit
(`omes-agent-<name>.service`), and its own state directory. Nothing is
shared between two agents by default: not sessions, not memory, not
browser profiles, not credentials, not the resource/hardening drop-in.
Declaring a second manifest with a different `metadata.name` produces an
entirely independent deployment.

## 7. Secrets

Manifests and OMES state never contain a secret value - only reference
**names** (`spec.secrets`, schema-pattern-constrained to bare
identifiers). The rendered unit references
`$HERMES_HOME/.env` via `EnvironmentFile=-`; populating that file with
real values is an operator responsibility outside this tool's scope
(the same boundary `omes agent-backup`/`hermesbackup` already draws -
see [docs/hermes-backup.md](hermes-backup.md)).

## 7a. Relationship to `lib/omes/runtime.sh` (issue #85)

[`lib/omes/runtime.sh`](../lib/omes/runtime.sh) (issue #85, ADR-0013)
defines `runtime_supported`/`runtime_require`/`runtime_describe`/
`runtime_home`/`runtime_service_unit` for the **shared** Hermes gateway
service (`hermes-gateway`, one per user/system scope). This issue's
per-agent unit (`omes-agent-<name>.service`, one per declared agent) is a
different shape than `runtime_service_unit` currently returns (a single
fixed unit name per scope), so `lib/omes/py/agent/` does not call into
`lib/omes/runtime.sh` today; it re-derives the runtime==`hermes` and
`HERMES_HOME` facts directly (`lib/omes/py/agent/manifest.py`,
`lib/omes/py/agent/paths.py`). Extending `runtime_service_unit` to accept
a logical agent name (so both call sites share one implementation) is
left as follow-up rather than done speculatively in this PR.

## 8. Left for follow-up (not satisfied by this PR)

- **Ubuntu Server 24.04 / Linux Mint VM evidence**: this environment has
  no VM to run a real `systemctl`/`hermes` install against; all testing
  here uses `tests/shims/systemctl` and `tests/shims/hermes`. Real-host
  verification is tracked as follow-up (see PR body).
- **`omes doctor` integration**: this MVP does not yet add an
  `omes agent`-aware `module_doctor` hook the way
  `modules/hermes-gateway-system` does for the shared gateway.
- **Compatibility recording** (#83) and a dedicated **provenance**
  issue (#84) are referenced by `provenance.collect()` but not
  otherwise integrated here.
- **Rootless Docker Compose / Coolify backends** remain out of scope by
  design (see docs/agent-orchestration-roadmap.md section 2 and issue
  #87's explicit non-goals).
