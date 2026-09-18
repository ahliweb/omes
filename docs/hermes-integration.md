# Hermes Integration

> Status: describes the actual repository state. Sections marked **Not
> implemented yet** are tracked by the linked issue; everything else
> reflects `modules/hermes/module.sh` as it exists today.
>
> This document covers the `hermes` module (issue [#11](https://github.com/ahliweb/omes/issues/11)).
> Gateway service integration (`hermes-gateway`, user/system systemd units)
> is documented separately once implemented — tracked in issue
> [#12](https://github.com/ahliweb/omes/issues/12). Telegram-specific
> security guidance lives in [`docs/telegram-security.md`](./telegram-security.md),
> tracked in issue [#13](https://github.com/ahliweb/omes/issues/13).
>
> OMES is an independent, MIT-licensed, Omarchy-inspired compatibility
> layer. It is not official Omarchy and Hermes Agent is a separate upstream
> project (nousresearch.com) that OMES installs and configures, not
> something OMES authors.

## 1. What the `hermes` module does

`modules/hermes/module.sh` (`MODULE_SCOPE=user`) installs the Hermes Agent
CLI for the invoking user by downloading and running the upstream installer,
then does the minimum additional wiring needed for the `hermes` command to
be usable in future shells: a managed `PATH` snippet, and an empty,
mode-`0600` `$HERMES_HOME/.env` if one does not already exist.

It follows the standard OMES module lifecycle (`docs/architecture.md`
§4): `module_check` (read-only preflight), `module_apply` (idempotent
install + wiring), `module_verify` (proves `hermes --version` and `hermes
doctor` both succeed), `module_rollback` (best-effort undo of OMES's own
wiring only).

## 2. Install model

**Contract: per-user only, never root.** Hermes installs entirely within
the invoking user's `$HOME` (`~/.hermes/hermes-agent/` for its code,
`~/.local/bin/hermes` for the binary). `MODULE_SCOPE="user"` means the OMES
module runner refuses to run it as root (exit 5), and `module_check` itself
additionally refuses and logs:

```text
[omes] ERROR hermes: must not run as root - Hermes installs per-user; re-run as the target user without sudo (see docs/hermes-integration.md)
```

This is a hard invariant, not a default that can be overridden with a flag
— see `docs/security.md` §1 ("Hermes runtime user").

**Supply chain.** OMES never pipes the upstream installer directly into a
shell (`curl | bash`). `module_apply`:

1. Downloads `https://hermes-agent.nousresearch.com/install.sh` (override
   for testing only via `OMES_HERMES_INSTALLER_URL`) to a private temp file
   with `curl -fsSL <url> -o <tmpfile>`.
2. If `OMES_HERMES_INSTALLER_SHA256` is set, verifies the downloaded file's
   sha256 against it; a mismatch aborts with **no execution** and
   `module_apply` fails (exit 6 from `omes install`).
3. If `OMES_HERMES_INSTALLER_SHA256` is **not** set, logs a `WARN` and
   proceeds anyway — pinning is optional (upstream does not currently
   publish a stable release hash to pin against) but its absence is always
   visible in the log, never silent. See `docs/security.md` §6.
4. Runs the verified temp file with an explicit `HERMES_HOME=<resolved
   home>` environment variable, then deletes the temp file.

This satisfies ADR-0006 and the "no `curl | bash` inside a module"
invariant in `docs/architecture.md` §10.

## 3. `HERMES_HOME` and profile layout

- Default: `~/.hermes` (the upstream default, unchanged).
- Override: set `OMES_HERMES_HOME` before running `omes install` to give a
  profile its own isolated Hermes instance (e.g. a second, differently
  configured Hermes identity on the same host). The module resolves
  `HERMES_HOME` once per `module_apply`/`module_verify` call and exports it
  for the rest of that `omes` process, so later modules in the same run
  (e.g. `hermes-gateway`, issue #12) see the same value.
- OMES does **not** create or manage multiple named profiles itself; a
  distinct `HERMES_HOME` value per invocation *is* the profile mechanism,
  exactly as upstream Hermes defines it. Running `omes install --profile
  hermes` twice with two different `OMES_HERMES_HOME` values produces two
  independent Hermes installs under the same OS user.

## 4. What OMES manages vs. what the operator manages

| Path | Managed by | Notes |
|---|---|---|
| `~/.hermes/hermes-agent/`, `~/.local/bin/hermes` | The upstream Hermes installer (invoked by OMES) | OMES does not vendor or modify this code; `module_rollback` never deletes it. |
| `$HERMES_HOME/config.yaml` | The operator, via `hermes config set <key> <value>` | OMES never writes this file directly. |
| `$HERMES_HOME/.env` | OMES creates it (empty, mode `0600`) **only if absent**; the operator fills in secrets | OMES never overwrites an existing `.env`, never reads secret values out of it, and never writes a provider or Telegram credential into it. See §5. |
| `${XDG_DATA_HOME:-~/.local/share}/omes/hermes-path.sh` | OMES (fully) | A small, marker-commented snippet exporting `PATH="$HOME/.local/bin:$PATH"` when not already present. Overwritten idempotently on every `module_apply`. |
| `~/.bashrc`, `~/.profile` | OMES appends one marker-delimited block sourcing the snippet above; the rest of the file is the operator's | Backed up (via `omes_manage_path`) before the first append; a file that already contains the marker block is left untouched on re-run. |
| Provider/model configuration (LLM API keys, `hermes model`) | The operator, entirely | See §5 — OMES never automates this. |

## 5. Secret boundaries: provider setup is never automated

**OMES never writes a provider API key, Telegram bot token, or any other
credential into `.env` or anywhere else.** `module_apply` only ever
*creates an empty* `.env` file (mode `0600`) when one does not already
exist, so a fresh install has a secrets file with the right permissions
ready for the operator to populate — it never contains a real or
placeholder-real-looking secret written by OMES itself.

Provider and model configuration is entirely a manual, documented, operator
action, using the Hermes CLI directly:

```console
$ hermes config set provider.name <provider>
$ hermes model                      # interactive model picker / current model
$ $EDITOR "$HERMES_HOME/.env"       # add e.g. ANTHROPIC_API_KEY=... yourself
```

This is deliberate: embedding a credential-writing step in an installer
module would mean OMES scripts handle plaintext secrets, and would give any
`omes install` run the ability to silently overwrite operator-managed
credentials. Neither is acceptable per `docs/security.md` §5. Telegram bot
token handling specifically is documented in
[`docs/telegram-security.md`](./telegram-security.md) (issue #13).

## 6. The doctor gate

`module_verify` treats `hermes doctor` as the authoritative proof that the
install actually works, not just that the binary exists:

1. `hermes --version` must succeed; its output is logged. Failure here
   fails verification immediately (exit 7) — the binary is missing or
   broken regardless of what `doctor` would say.
2. `hermes doctor` is then run. Its exit code decides the result:
   - **Non-zero exit (`FAIL`)** → `module_verify` fails, and the doctor
     output is logged at `ERROR` level in full so the operator sees exactly
     what to fix, instead of a generic "verification failed" message.
   - **Zero exit with `WARN` somewhere in its output** → `module_verify`
     still succeeds, but the output is logged at `WARN` level so the
     warning is visible in `omes install`'s output and in the log file, not
     silently swallowed.
   - **Zero exit, no `WARN`** → logged at `INFO` level; nothing further to
     do.

This means `omes install --profile hermes` failing with exit 7 always means
"look at the `hermes doctor` output printed above it," not an opaque
failure.

## 7. Idempotency

Re-running `omes install --profile hermes` (or `--module hermes`) against an
already-applied host:

- Does **not** re-download or re-run the installer when a `hermes` binary
  is already present and runnable (`hermes --version` succeeds), unless
  `OMES_HERMES_VERSION` is set and does not match the installed version's
  output — in which case the installer runs again to bring the host to the
  pinned version.
- Does **not** duplicate the `~/.bashrc`/`~/.profile` marker block (checked
  by the marker's presence before appending).
- Does **not** overwrite an existing `$HERMES_HOME/.env` (checked by file
  existence before creating).
- Leaves `module.hermes.status=applied` and re-runs `module_verify`
  regardless, so a doctor regression is still caught on every run.

## 8. Rollback

`module_rollback` is invoked by `omes uninstall`/`omes restore`, or
explicitly by an operator recovering from a `failed` state — never
automatically mid-`install` (see `docs/architecture.md` §3.2). It removes
only what OMES itself created:

- The managed PATH snippet file.
- The marker-delimited block it appended to `~/.bashrc`/`~/.profile`
  (everything else in those files is left untouched).
- The `module.hermes.home` state key.

It **never** deletes `$HERMES_HOME` or `~/.local/bin/hermes` — that is
real Hermes data and the operator's install, not something OMES has enough
information to safely destroy (config, secrets, session history, skills).
Instead, it prints the exact command a human would run to fully remove
Hermes themselves:

```text
[omes] WARN hermes: to fully remove Hermes yourself, run: rm -rf '/home/<user>/.hermes' "$HOME/.local/bin/hermes"
```

## 9. Environment variables this module reads

| Variable | Purpose | Default |
|---|---|---|
| `OMES_HERMES_HOME` | Overrides `HERMES_HOME` for this install | `~/.hermes` |
| `OMES_HERMES_VERSION` | Pins the installed version; a mismatch triggers re-install | unset (accept whatever the installer provides) |
| `OMES_HERMES_INSTALLER_SHA256` | Verifies the downloaded installer's integrity before executing it | unset (proceeds with a `WARN`) |
| `OMES_HERMES_INSTALLER_URL` | Overrides the installer URL | `https://hermes-agent.nousresearch.com/install.sh` (testing only; not a documented operator knob) |

## 10. Not implemented yet

- Gateway services (`hermes gateway install`, user/system systemd units,
  `loginctl enable-linger`) — tracked in issue #12.
- Telegram allowlist tooling and security documentation — tracked in issue
  #13.
- `omes doctor`/`omes update`/`omes uninstall` as first-class CLI commands
  (currently stubs in `bin/omes`) — tracked in issue #14 and #10
  respectively; this module's `module_rollback` is reachable today only by
  calling it directly (e.g. from a future `omes uninstall`), not via a
  finished CLI command.
