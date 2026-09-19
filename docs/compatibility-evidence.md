# Compatibility evidence (issue #83)

> Status: implemented. Covers `lib/omes/versions.sh`,
> `lib/omes/py/provenance/versions.py`, `omes health versions`, and the
> `evidence.*` state keys `modules/hermes/module.sh` writes at apply and
> on `omes doctor`.
>
> **Evidence is not a guarantee.** This document records what OMES
> *observed* about the host and Hermes deployment at a point in time, by
> running read-only, argv-safe, timeout-bounded commands. It does not
> certify compatibility, security, or correctness, and a value recorded
> here can go stale the moment a binary is upgraded, a config key is
> changed, or a service is restarted. Treat it as a diagnostic snapshot,
> not a contract. Supply-chain provenance (checksum/installer trust) is a
> separate, narrower concern — see [docs/provenance.md](./provenance.md)
> (issue #84).

## 1. What is recorded

`omes health versions [--json]` and `modules/hermes/module.sh`'s
`module_doctor` report a single evidence snapshot with these components:

| Component | What is recorded | Source |
|---|---|---|
| `omes.version` | The OMES release version | `VERSION` file |
| `omes.git_ref` | Short git ref of the OMES checkout, if any | `git rev-parse --short HEAD` |
| `os.id`, `os.version_id`, `os.codename`, `os.kernel` | OS identity and running kernel | `/etc/os-release` (via `lib/omes/detect.sh`), `uname -r` |
| `arch` | CPU architecture (`amd64`/`arm64`/`unsupported`) | `uname -m` (via `lib/omes/detect.sh`) |
| `hermes` | Hermes CLI version | `hermes --version` |
| `gateway_mode` | Applied Hermes gateway scope (`user`/`system`), if any | OMES state (`module.hermes-gateway{,-system}.mode`) |
| `python3` | The interpreter running OMES's own Python tooling | in-process `sys.version` |
| `node` | Node.js version (Hermes gateway service PATH dependency) | `node --version` |
| `browser` | Chromium/Google Chrome version (browser automation) | `chromium \| chromium-browser \| google-chrome --version` |
| `ffmpeg` | ffmpeg version (first line of the version banner) | `ffmpeg -version` |
| `docker` | Docker **client** version only | `docker --version` (never `docker version`/the daemon socket) |
| `ollama` | Ollama version, if installed | `ollama --version` |
| `provider_config.<key>` | Non-secret Hermes config values, from a fixed allowlist | `hermes config get <key>` |

Each value is reported as an object, never a bare string, so "unknown" is
always distinguishable from a real value:

```json
{"value": "hermes 1.2.3", "source": "hermes", "observed_at": "2026-09-19T01:00:00Z", "method": "hermes --version", "reason": null}
```

When a value cannot be observed, `value` is `null` and `reason` explains
why (`"binary not found on PATH"`, `"command timed out"`, `"command
exited 1 with no usable output"`, `"not_available: 'hermes config get'
subcommand not recognized by this Hermes build"`, etc.). This module
never guesses, and never reports a value it could not actually observe —
an unexpected `--version` output format produces `null`, not a
misleading string.

## 2. The non-secret Hermes config allowlist

`provider_config` reads exactly these keys via `hermes config get <key>`,
verified against
[hermes-agent.nousresearch.com/docs/user-guide/configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration):

- `model.provider`
- `model.name`
- `gateway.mode`

This allowlist lives in `lib/omes/py/provenance/versions.py`
(`ALLOWED_HERMES_CONFIG_KEYS`). It is the single place that decides what
`omes health versions` may ask Hermes for. Adding a key requires
confirming against upstream documentation that the key cannot hold a
credential, token, or secret value. If `hermes config get` is not a
recognized subcommand on the installed Hermes build, every key is
reported `value: null` with `reason` starting `not_available:` instead of
falling back to a different, unverified invocation.

**`$HERMES_HOME/.env` is never read, opened, or dumped by this
component**, under any code path — see `docs/hermes-integration.md` §5
for why `.env` is treated as out-of-bounds for every OMES read path.

## 3. Profile isolation

Every probe resolves `HERMES_HOME` the same way the `hermes` module does
(`OMES_HERMES_HOME` override, else `~/.hermes`), so running `omes health
versions` against a profile with an isolated `HERMES_HOME` reports that
profile's Hermes install, never a different one on the same host.

## 4. Warnings for known-unsupported combinations

`omes health versions` additionally reports `warnings[]` for a small set
of known-unsupported combinations, for example:

- An OS/version combination outside OMES's supported tiers (see
  `docs/architecture.md`'s platform table).
- `python3` older than 3.10 (required by graphify, issue #50).
- A gateway mode recorded in state but `hermes --version` failing (a
  likely broken or removed install).

Warnings are informational; they do not change `omes health versions`'
exit code (this command reports evidence, it does not gate anything).

## 5. What is never recorded

- No secret value, ever (Telegram bot token, provider API keys, any
  `.env` content).
- No dump of the process environment (`os.environ`) in JSON or human
  output.
- No provenance/checksum verdict — see `docs/provenance.md` (issue #84)
  for supply-chain evidence, which is recorded and audited separately.

## 6. Related

- `docs/hermes-integration.md` — the `hermes` module this evidence is
  attached to.
- `docs/provenance.md` — supply-chain provenance and `omes audit
  provenance` (issue #84).
- `docs/cli.md` §4.12 — `omes health versions` usage.
- `docs/configuration.md` — environment variables consulted.
