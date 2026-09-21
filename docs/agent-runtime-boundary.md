# Agent-runtime boundary

> Implements [ADR-0013](adr/0013-agent-runtime-boundary.md), [ADR-0017](adr/0017-upstream-first-ownership-and-boundary-enforcement.md),
> and issues [#85](https://github.com/ahliweb/omes/issues/85), [#171](https://github.com/ahliweb/omes/issues/171). Hermes Agent is the
> only supported runtime today (baseline `v2026.9.14`). This document describes the interface
> OMES needs from *any* agent runtime, the Hermes implementation that
> currently fulfils it, and the isolation bar a second runtime would have
> to clear before OMES adds it. It does not announce a second runtime.

## 1. Non-goals and upstream-first ownership (ADR-0017)

Per ADR-0017 and the upstream-first hierarchy (`DELEGATE -> PORT -> ADAPT -> DEFER -> REJECT`), OMES strictly delegates agent runtime behavior to upstream Hermes:

- OMES does **not** become a second agent runtime, chat router, or reasoning engine;
- OMES does **not** reimplement Hermes's channels, memory, skills, sessions, browser
  automation, cron/webhooks, or model/provider routing;
- OMES does **not** access Hermes private databases (`messages.db`, `.hermes/`) directly;
- Follow-up issues delegate specific runtime concerns natively to Hermes:
  - [#174](https://github.com/ahliweb/omes/issues/174): RuntimeDeployment v2 / profile reference boundary (implemented via [ADR-0018](adr/0018-runtimedeployment-v2-and-hermes-profile-references.md)).
  - [#175](https://github.com/ahliweb/omes/issues/175): delegate native Hermes gateway/service lifecycle.
  - [#176](https://github.com/ahliweb/omes/issues/176): delegate backup/export to native `hermes backup`/`hermes profile export`.
  - [#177](https://github.com/ahliweb/omes/issues/177): align container deployments with official Hermes Docker topology.
  - [#178](https://github.com/ahliweb/omes/issues/178): consume native Hermes health endpoints.
- add a plugin-loading mechanism — `runtime_supported()` is a fixed
  allowlist of one name (`hermes`), not an extension point;
- claim a second runtime is supported before its `runtime_supported`
  entry, `runtime_describe` fixture, and isolation requirements (§4) all
  exist and are reviewed.


## 2. The interface OMES needs

Every function below is implemented once, runtime-neutrally, in
`lib/omes/runtime.sh`, and dispatches to a runtime-specific
implementation by name. The only implemented name is `hermes`.

| Interface concern | Bash function / entrypoint | JSON shape (when applicable) |
|---|---|---|
| Supported-runtime check | `runtime_supported <name>` | n/a (exit status only) |
| Fail closed on an unsupported runtime | `runtime_require <name>` | n/a (dies, exit 4) |
| Runtime metadata | `runtime_describe <name>` | `{"name", "version_command", "home_env_var", "home_default", "service_units": {"user","system"}, "health_probe", "backup_classes": [...], "provenance_sources": [...]}` |
| Runtime home directory | `runtime_home <name>` | n/a (prints a path) |
| Service unit name per scope (shared gateway) | `runtime_service_unit <name> <user\|system>` | n/a (prints a unit name) |
| Service unit name per **declared agent profile** (issues #87, #96, #175) | `runtime_agent_service_unit <profile-ref> <user\|system>` | n/a (prints `hermes-gateway[-<profile-ref>].service`, ADR-0019) |
| Install | `modules/<runtime>/module.sh`'s `module_apply` | n/a (module contract, docs/architecture.md §4) |
| Preflight | `modules/<runtime>/module.sh`'s `module_check` | n/a |
| Verify | `modules/<runtime>/module.sh`'s `module_verify` | n/a |
| Service lifecycle | `modules/<runtime>-gateway*/module.sh` (`module_apply`/`module_verify`/`module_rollback`) | n/a |
| Health | `omes health agent\|gateway` (issue #79) via `lib/omes/py/health/*` | `{"layers": {...}, "ready": bool, "connected": bool}` |
| Provenance | `runtime_describe <name>.provenance_sources`, consumed by the (future, #83/#84) provenance recorder | array of command strings |
| Backup classes | `runtime_describe <name>.backup_classes` | array of class names (`config`, `state`, `secrets-ref`) |
| Rollback | `modules/<runtime>*/module.sh`'s `module_rollback` | n/a |

`runtime_describe`'s exact output is asserted byte-for-byte (after JSON
parsing, not string comparison) by `tests/unit/runtime.bats` against the
fixture at `tests/fixtures/runtime/hermes.json`.

## 3. Hermes implementation mapping

| Interface concern | Hermes-specific implementation |
|---|---|
| `version_command` | `hermes --version`, called by `modules/hermes/module.sh`'s `_hermes_installed_version` and `module_verify` |
| `home_env_var` / `home_default` | `HERMES_HOME`, resolved by `modules/hermes/module.sh`'s `_hermes_home` (`OMES_HERMES_HOME` override, else `~/.hermes`) — the same resolution `runtime_home hermes` returns |
| `service_units.user` | `hermes-gateway`, managed by `modules/hermes-gateway/module.sh` via `systemctl --user` |
| `service_units.system` | `hermes-gateway`, managed by `modules/hermes-gateway-system/module.sh` via `systemctl` (no `--user`) |
| `health_probe` | `omes health agent` (issue #79), which layers `hermes doctor`, `hermes gateway status`, and (when configured) the Ollama provider check from issue #71 |
| `backup_classes` | `config` (`$HERMES_HOME/config.yaml`, managed via `omes_manage_path`), `state` (OMES's own `state` keys for the module), `secrets-ref` (`$HERMES_HOME/.env` — deliberately never backed up; see `modules/hermes/module.sh`'s `_hermes_ensure_env_file` comment and docs/security.md §5) |
| `provenance_sources` | `hermes --version`, `hermes doctor`, `hermes gateway status` — read-only commands whose output is used as version/health evidence, never as a place secrets are captured from |
| Install/preflight/verify/rollback | `modules/hermes/module.sh` (`MODULE_SCOPE=user`) |
| Service lifecycle | `modules/hermes-gateway/module.sh` (`MODULE_SCOPE=user`, default) and `modules/hermes-gateway-system/module.sh` (`MODULE_SCOPE=root`, opt-in) |

`modules/hermes*` are not rewritten to call `lib/omes/runtime.sh`
internally by this change — their existing, already-tested
`_hermes_home`/unit-name logic is preserved verbatim (ADR-0013 Option C).
`lib/omes/runtime.sh` exists so *new* consumers (the health model in
#71/#79, the exposure audit in #80) read these facts from one place
instead of re-deriving them, and so the mapping above has one canonical,
reviewable table.

## 4. Isolation requirements for any future runtime

Before OMES adds a second entry to `runtime_supported`, that runtime's
integration must define, and a reviewer must be able to verify:

1. **Users**: a dedicated, non-root service account distinct from any
   other runtime's — never sharing a service account with Hermes or with
   another runtime.
2. **Ports**: non-overlapping listening ports/addresses from any other
   configured runtime or gateway (coordinate with the exposure audit,
   issue #80/`omes audit exposure`).
3. **State directories**: a separate home/state directory tree, never
   nested inside or aliased to `HERMES_HOME` or another runtime's home.
4. **Credentials**: its own secret references (env var names / file
   paths), never sharing a `.env` file or token with another runtime.
5. **Browser profiles**: if the runtime automates a browser, a dedicated
   profile directory distinct from any other runtime's — no shared
   cookies, sessions, or saved credentials.
6. **Health checks**: a bounded, read-only, timeout-guarded health probe
   entrypoint reachable the same way `runtime_describe`'s `health_probe`
   field is reachable for Hermes today, returning the same
   `{"layers": {...}, "ready": bool, "connected": bool}` shape used by
   `omes health` (issue #79) so `omes doctor` can treat any supported
   runtime uniformly.

A PR adding a second runtime must show all six requirements are met (or
explicitly not applicable, with reasoning) before `runtime_supported`
returns true for that name.

## 5. Contract tests

- `tests/unit/runtime.bats` — `runtime_supported`, `runtime_require`
  (exit 4 for unsupported), `runtime_describe` (fixture-matched JSON
  shape), `runtime_home`, `runtime_service_unit`.
- `tests/fixtures/runtime/hermes.json` — the fixture asserting
  `runtime_describe hermes`'s exact JSON shape; any change to that shape
  must update this fixture and this document's §2 table in the same
  change.

## 6. Sources

- [ADR-0013](adr/0013-agent-runtime-boundary.md)
- [docs/architecture.md](architecture.md) §4 (module contract)
- [docs/hermes-integration.md](hermes-integration.md)
- [docs/agent-orchestration-roadmap.md](agent-orchestration-roadmap.md)
- [Issue #85](https://github.com/ahliweb/omes/issues/85)
