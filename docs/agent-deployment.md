# Agent deployment (native OMES + Hermes: systemd MVP + rootless Docker Compose)

> Status: describes the actual repository state on this branch (issues
> [#87](https://github.com/ahliweb/omes/issues/87) and
> [#96](https://github.com/ahliweb/omes/issues/96)). Two backends are
> implemented: the systemd MVP from
> [docs/agent-orchestration-roadmap.md section 2.1](agent-orchestration-roadmap.md)
> and the rootless Docker Compose isolation backend from
> [section 2.2](agent-orchestration-roadmap.md). The Coolify phase
> (section 2.3) is **not implemented**. See
> [docs/hermes-deployment-guide.md §17](hermes-deployment-guide.md#17-per-agent-deployments-omes-agent)
> for the condensed runbook version. This revision (`Part of #87`,
> `Part of #96`) additionally closes four of the five follow-up gaps the
> original #87/#96 PRs listed: `omes doctor` integration (section 11),
> `omes agent logs` for the compose backend (section 11), containerized-
> Hermes health reuse of `lib/omes/py/health/hermes.py`'s provider/
> channel layers (section 9.4), and `lib/omes/runtime.sh` unit-name
> integration (section 7a). Ubuntu Server 24.04 VM/CI evidence and a real
> (non-shimmed) Docker daemon are still **not available in this
> environment** for either backend - see "Left for follow-up" (section
> 10) at the end of this document, which has been trimmed to what
> actually remains.

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
[`contracts/agent/v1/fixtures/agent-deployment/`](../contracts/agent/v1/fixtures/agent-deployment/) (schema fixtures, validated by `scripts/check-contracts.py`) and [`contracts/agent/v1/fixtures-semantic/`](../contracts/agent/v1/fixtures-semantic/README.md) (schema-valid manifests that only the semantic checks reject)):

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

`spec.backend` selects `"systemd"` (this section) or `"compose"`
(section 8, issue #96). `spec.compose` is required when-and-only-when
`spec.backend` is `"compose"`; it is validated by
[`lib/omes/py/agent/compose.py`](../lib/omes/py/agent/compose.py), not
described further here - see section 8.

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
service (`hermes-gateway`, one per user/system scope). A per-agent unit
(`omes-agent-<name>.service`, one per declared agent) is a different
shape than `runtime_service_unit` returns (a single fixed unit name per
scope), so `lib/omes/runtime.sh` now defines a second, sibling function
for it instead of overloading `runtime_service_unit`:
**`runtime_agent_service_unit <name> <scope>`** prints
`omes-agent-<name>.service` for a given agent name/scope, and is the one
source of truth for that naming shape (see
[docs/agent-runtime-boundary.md](agent-runtime-boundary.md) section 2 and
[ADR-0013](adr/0013-agent-runtime-boundary.md)'s "Consequences" update).

`lib/omes/py/agent/runtime_bridge.py` reads it via the same
`bash -c 'source ...; <function> <argv>'` bridge pattern
`lib/omes/py/agent/hardening_bridge.py` already uses for
`hardening_render` - fixed script paths, fixed argv, no shell
interpolation of untrusted data, never a secret. `lib/omes/py/agent/cli.py`'s
`_build_systemd_plan()` resolves the unit name through this bridge and
passes it into `lib/omes/py/agent/plan.py`'s `build_plan(manifest,
unit_name_override=...)`; `plan.py` itself stays a pure, subprocess-free
function (its own `unit_name()` remains as the literal-identical
fallback used when the bridge cannot run, e.g. in a unit test that calls
`build_plan` directly with no override) so `omes agent plan`'s tests
do not need bash/systemd/PATH plumbing to stay fast and deterministic.
`runtime_home hermes` and the runtime==`hermes` check are still
re-derived directly in `lib/omes/py/agent/manifest.py`/`paths.py` rather
than bridged - those facts do not vary per invocation the way the unit
name selection does, and bridging every runtime fact through a
subprocess call would add latency to every `omes agent` invocation for
no behavioral benefit.

## 8. Compose backend: rootless Docker Compose isolation (issue #96)

### 9.1 When to use it vs. systemd

Use `backend: "systemd"` (the default, section 1-8 above) when a plain
Hermes install under an isolated `HERMES_HOME` and a `systemctl` unit is
enough - the common case. Use `backend: "compose"` when an agent needs
stronger isolation than a systemd service can give it: a pinned,
immutable filesystem (the image), a dedicated network namespace, no
ambient access to the host's other services, and a container-scoped
process/resource boundary - e.g. an agent that runs untrusted or
lightly-trusted generated code, browses the web with its own browser
profile, or otherwise should not share the host's process/network
namespace with anything else. It is optional: agents that work well
under systemd are not required to move to it (roadmap section 2.2: "This
phase must not be implemented by silently turning the entire OMES server
profile into a Docker platform").

### 9.2 Rootless requirements

This backend requires a **rootless** Docker daemon (Docker's own
rootless mode: `dockerd-rootless.sh` / the `docker-ce-rootless-extras`
package, running as the operator's own non-root user, with its own
per-user socket - typically `unix:///run/user/<uid>/docker.sock`, never
`/var/run/docker.sock`). `omes agent check`, and the start of
`omes agent apply` (before any file is written or any container
started), calls
[`lib/omes/py/agent/compose_preflight.py`](../lib/omes/py/agent/compose_preflight.py),
which runs only read-only commands (`docker context show`,
`docker context inspect --format '{{.Endpoints.docker.Host}}'`,
`docker info --format '{{.SecurityOptions}}'`, `id -nG`) and refuses
(exit 4) if:

- the active context's `SecurityOptions` do not report `name=rootless`;
- the active context's endpoint is the well-known rootful socket path
  (`/var/run/docker.sock` or `/run/docker.sock`), even if
  `SecurityOptions` somehow claimed otherwise;
- the invoking user's only path to that Docker access is membership in
  the `docker` group on a daemon that is not reported as rootless -
  group membership on a rootful daemon is root-equivalent and is never
  treated as a substitute for a rootless daemon.

**What OMES will never do for this backend:**

- add the operator to the `docker` group (no `usermod`/`gpasswd`, ever,
  for this backend - unlike `modules/containers`' explicit,
  operator-requested `--allow-docker-group` opt-in for the *host*
  Docker install, which is a different concern entirely);
- run `sudo` on the agent's behalf;
- grant the agent container access to the Docker socket, at any path,
  under any name (`spec.compose.volumes` rejects any host path
  containing `docker.sock`, structurally, regardless of intent);
- allow `privileged`, host PID namespace, or host network mode - these
  are not fields the manifest schema exposes at all
  (`additionalProperties: false`), and `network: "host"`/`"none"` are
  rejected explicitly as defence in depth;
- allow a host bind mount outside the agent's own OMES-managed state
  directory (`<state-dir>/agents/<name>/...`) - not another agent's
  directory, not an arbitrary host path;
- allow a non-loopback port bind (`ports` entries must be
  `127.0.0.1:<port>:<port>`; the list is empty by default);
- accept an image reference that is not pinned by digest (`@sha256:...`)
  - a tag-only reference (e.g. `:latest`) is rejected before any
  mutation.

### 9.3 Manifest shape

```json
{
  "apiVersion": "omes.ahliweb.com/v1",
  "kind": "AgentDeployment",
  "metadata": { "name": "compose-worker", "workspace": "ahliweb", "environment": "staging" },
  "spec": {
    "runtime": "hermes",
    "profile": "compose-worker",
    "role": "generic",
    "backend": "compose",
    "serviceMode": "user",
    "restartPolicy": "on-failure",
    "resources": { "memory": "512M", "cpu": "0.5", "pids": 64 },
    "health": { "command": "omes agent health compose-worker", "timeout": "10s" },
    "storage": { "memory": "private", "sessions": "isolated", "skills": "managed" },
    "secrets": ["provider-primary"],
    "compose": {
      "image": "registry.example.com/agents/compose-worker@sha256:<64 hex>",
      "project": "omes-agent-compose-worker",
      "network": "omes-agent-compose-worker-net",
      "user": "1000:1000",
      "capDrop": ["ALL"],
      "readOnlyRootfs": true,
      "ports": ["127.0.0.1:8081:8080"]
    }
  }
}
```

`spec.compose.project` and `.network` default to `omes-agent-<name>` and
`omes-agent-<name>-net` when omitted. `spec.compose.capDrop` defaults to
`["ALL"]` and, if given explicitly, must equal exactly `["ALL"]` - this
backend never re-adds a capability. `spec.compose.readOnlyRootfs`
defaults to `true`. `spec.compose.user` must be a non-root `uid:gid`
(both parts non-zero). `spec.resources` (memory/cpu/pids) is the same
field the systemd backend uses, reused here and rendered into the
compose file's `mem_limit`/`mem_reservation`/`cpus`/`pids_limit`.
Validation lives in
[`lib/omes/py/agent/compose.py`](../lib/omes/py/agent/compose.py)`.validate_compose_spec`,
called from `manifest.py`'s semantic checks; fixtures under
[`contracts/agent/v1/fixtures/agent-deployment/`](../contracts/agent/v1/fixtures/agent-deployment/)
(`valid-compose-generic.json` and five `invalid-compose-*.json` cases).

### 9.4 Rendering, backup, and lifecycle

`omes agent apply` for this backend follows the same
check -> plan -> backup -> mutate -> verify lifecycle as systemd:

1. **check**: manifest validation, privilege check, then the rootless
   Docker preflight (section 8.2) - all before any mutation.
2. **plan**: `compose.build_plan()` computes the project/network/image/
   volumes/ports/resource limits and the rendered file's path
   (`<state-dir>/agents/<name>/compose/compose.yaml`).
3. **backup**: the agent's `HERMES_HOME`, if it already exists, is
   backed up via `lib/omes/py/hermesbackup` exactly as the systemd
   backend does (default classes `config`, `skills`); additionally, the
   *previously rendered* `compose.yaml` (if this is not the first apply)
   is copied aside to
   `<state-dir>/agents/<name>/compose-backups/<timestamp>/compose.yaml`
   before being overwritten, so a failed update can be rolled back to
   exactly the file that was previously running.
4. **mutate**: the compose file is (re-)rendered deterministically (no
   PyYAML - a plain, flat string template, ADR-0012) and written
   atomically, then `docker compose -p <project> -f <file> up -d`.
   Re-applying an unchanged manifest re-renders byte-identical content
   and re-runs an already-idempotent `up -d` - a true no-op.
5. **verify**: [`lib/omes/py/agent/compose_health.py`](../lib/omes/py/agent/compose_health.py)
   builds a layered result: the container/gateway layer (`docker compose
   ps --format json` must report `running` with no unhealthy
   healthcheck), a `runtime` layer (`hermes --version`/`hermes doctor`
   run *inside* the container via `docker compose exec -T`, since the
   `hermes` binary lives in the container's filesystem, never the
   host's), the manifest's own `spec.health.command` (also via `docker
   compose exec -T <service> sh -c '<command>'`), and - the issue #96
   follow-up this revision closes - the **provider** and **channel**
   layers reused from
   [`lib/omes/py/health/hermes.py`](../lib/omes/py/health/hermes.py):
   `check_channel` is called completely unmodified (the compose
   backend's `env_file:` reference is a *host* path, so the existing
   host-side `.env` lookup already reflects what the container was
   started with - no container round-trip needed for that layer); the
   provider (Ollama) layer's *reachability probe* runs *through* `docker
   compose exec -T <service> sh -c 'curl ...'`, since an isolated
   compose network does not automatically share the host's loopback
   interface, so "is Ollama reachable" has to be answered from inside
   the container's own network namespace. Every check stays read-only; a
   container that cannot be exec'd into at all (project down, `docker`
   missing) marks the affected layer `not_applicable` rather than
   failing the whole health report - see
   [`tests/py/agent/test_compose_health.py`](../tests/py/agent/test_compose_health.py).

`omes agent rollback` for this backend runs `docker compose down`, then
restores the most recent `compose-backups/` entry (if one exists) and
re-runs `up -d` - or, if there is no previous version (first apply being
rolled back), simply leaves the project down. `omes agent remove`
(compose backend only; not implemented for systemd, which has no
removable named volume and where `rollback` already covers unit/drop-in
teardown) runs `docker compose down --volumes` and deletes the agent's
own `<state-dir>/agents/<name>/compose/` directory - never
`HERMES_HOME`, never another agent's resources.

Provenance recorded on `apply` includes `composeImageDigest` (the
manifest's pinned `@sha256:...` reference) and `composeFileSha256` (the
rendered file's own hash) alongside the existing `omesVersion`/`gitRef`/
`hermesVersion` fields (section 4) - never a secret value.

### 9.5 Secrets

Exactly as the systemd backend (section 7): `spec.secrets` holds
reference **names** only. The rendered compose service references
`env_file: [<HERMES_HOME>/.env]` - never `environment:` with inline
values - so a secret value never appears in the compose file, in
`omes agent plan`'s output, or in any log line.

## 11. `omes doctor` integration and `omes agent logs` (compose)

### 11.1 `omes doctor` integration (issue #87/#96 follow-up)

`omes agent doctor` (`lib/omes/py/agent/cli.py`'s `cmd_doctor`) reports
**every deployed agent** - every name under the agent state directory,
`lib/omes/py/agent/paths.py`'s `agents_state_dir()` (`<state-dir>/agents/`)
- with its current lifecycle state and a health summary, entirely
read-only and bounded by `OMES_HEALTH_TIMEOUT` (default 10s) per agent.
It reuses the exact same health implementations `omes agent health`
uses - `lib/omes/py/agent/health.py` for `backend: "systemd"`,
`lib/omes/py/agent/compose_health.py` for `backend: "compose"` - rather
than a third health mechanism. A missing/invalid manifest for a
still-declared agent is reported as `"ok": false` with an `error` field,
never a crash of the whole report:

```json
{"agents": [{"name": "researcher", "state": "healthy", "backend": "systemd", "serviceMode": "user", "ok": true, "ready": true, "connected": true, "health": {"layers": {"...": "..."}}}], "ok": true}
```

`bin/omes`'s top-level `cmd_doctor` (`docs/cli.md` section 4) calls this
automatically - via the existing generic extension-command loader
(`_omes_run_extension_command agent doctor --json`, the same mechanism
`lib/omes/cmd/README.md` already documents for every `lib/omes/cmd/*.sh`
file; **no second doctor-hook mechanism was invented** for this) -
whenever `lib/omes/cmd/agent.sh` is present and at least one agent has
ever been declared (an install with no agents ever run is entirely
unaffected: no extra check, no extra line). Each agent becomes one
`agent:<name>` row (`OK` when the agent reports `ready`, `WARN`
otherwise, mirroring the existing `module_doctor` convention where a
degraded dependency is advisory, not a hard `FAIL` that blocks the whole
platform doctor run) in `omes doctor`'s own check list and JSON `checks`
array - see `docs/cli.md`'s `omes doctor` and `omes agent doctor`
sections for the exact schema, and
[`tests/py/agent/test_cli.py`](../tests/py/agent/test_cli.py) /
[`tests/integration/agent.bats`](../tests/integration/agent.bats) for the
tests.

This intentionally does **not** detect "manifest edited since last
apply" drift - see section 12.

### 11.2 `omes agent logs` for `backend: "compose"` (issue #96 follow-up)

`lib/omes/cmd/agent.sh`'s `logs` subcommand now branches on the agent's
`spec.backend` (read via a small manifest lookup, mirroring the existing
`_agent_manifest_service_mode` helper): `backend: "systemd"` is
unchanged (a plain `journalctl` passthrough scoped to the agent's own
unit); `backend: "compose"` resolves the agent's `project`/`composeFile`
via `omes agent plan --json` and runs

```bash
docker compose -p <project> -f <compose file> logs --no-color --tail <n>
```

`--tail` defaults to `200`; `--follow` is accepted but **never** the
default - `omes agent logs <name>` always returns rather than streaming
forever, matching the same "no unbounded default" posture the rest of
OMES's CLI already holds (`docs/cli.md` section 1). Any other arguments
are passed through to `docker compose logs` verbatim.
`tests/shims/docker` gained `SHIM_DOCKER_COMPOSE_LOGS_EXIT` for testing
the failure path; see
[`tests/integration/agent-compose.bats`](../tests/integration/agent-compose.bats).

## 12. Left for follow-up (not satisfied by this PR)

This list is trimmed to what actually remains after this revision
(`Part of #87`, `Part of #96`); resolved items now live in section 11 and
section 9.4/7a above rather than here.

- **Ubuntu Server 24.04 / Linux Mint VM evidence**: this environment has
  no VM to run a real `systemctl`/`hermes` install against, and no
  rootless Docker daemon to run a real `docker compose up`/`ps`/`exec`
  against; all testing here uses `tests/shims/systemctl`,
  `tests/shims/hermes`, and `tests/shims/docker`. There is no evidence
  yet of a real image pull by digest or real container resource-limit
  enforcement (`mem_limit`/`cpus`/`pids_limit`) on an actual rootless
  daemon. Real-host verification for both backends is tracked as
  follow-up (see the PR body).
- **Compatibility recording** (#83) and a dedicated **provenance**
  issue (#84) are referenced by `provenance.collect()` but not
  otherwise integrated here.
- **Coolify backend** remains out of scope by design (see
  docs/agent-orchestration-roadmap.md section 2.3 and issue #87's
  explicit non-goals).
- **Compose backend (#96) - named volumes**: only host bind-mounts under
  the agent's own state directory are supported (`spec.compose.volumes`
  has no notion of a Docker-managed named volume). `omes agent remove`
  passes `docker compose down --volumes` defensively for forward
  compatibility, but there is nothing for it to prune today.
- **Compose backend (#96) - `check_provider`'s reachability probe is not
  the literal `lib/omes/py/health/hermes.py` function**: section 9.4
  reuses `check_channel` verbatim, but the provider layer's HTTP call is
  reimplemented as a `docker compose exec -T ... curl` probe (see
  `lib/omes/py/agent/compose_health.py`) rather than executing
  `hermes.py`'s own `check_provider` function unmodified inside the
  container, because that function calls a stdlib `httpjson` import path
  that assumes a host-reachable Python environment, not a guarantee any
  agent image makes. The probe reports the same layer_result/status
  contract, but is a container-adapted equivalent, not a literal call
  into the same function object - a genuine "run `hermes.py`'s exact
  Python code inside an arbitrary agent image" would additionally
  require shipping (or vendoring) `lib/omes/py/health` into every agent
  image, which this PR does not do.
- **`omes doctor` integration is per-agent state, not per-manifest
  drift**: `omes agent doctor`/`omes doctor` report the *current*
  lifecycle state and live health of every agent under the state
  directory; they do not detect a manifest that was edited after the
  last `apply` (i.e. "declared spec no longer matches what is running").
  Detecting that drift is a natural extension of `omes agent doctor` but
  is not implemented here.
