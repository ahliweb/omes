# Browser worker host-readiness evidence (`omes health browser-worker`)

> **Status: partially implemented.** Issue [#298](https://github.com/ahliweb/omes/issues/298) under epic [#294](https://github.com/ahliweb/omes/issues/294), stacked on the wire contract from [#296](https://github.com/ahliweb/omes/issues/296). The read-only evidence **producer** exists. Per-job browser isolation evidence is **Not implemented yet (tracked in #298, depends on #276 / PR #282)**, so on a real host the reported state is **never `ready` today** — by design, not by accident. OMES ships **no** browser worker, RPA runtime, browser executor, Playwright dependency or adapter, and nothing in this feature launches a browser.

Read first: [AGENTS.md](../AGENTS.md), [scope](scope.md), [architecture](architecture.md), [security](security.md), [threat model](threat-model.md), [AI data privacy](ai-data-privacy-and-model-security.md), [ADR-0029](adr/0029-ai-data-boundary-and-private-inference.md), [Ollama](ollama.md).

## 1. Purpose and boundary

An authenticated consumer (the AWCMS Control Center, via the existing pull-worker boundary) needs to know whether an **optional, separately installed** browser worker's *host* is fit to be given browser jobs. OMES answers only the host half of that question, as sanitized metadata:

| Concern | Authority |
|---|---|
| Host compatibility, listener exposure, resource floors, runtime provenance, host-side evidence | **OMES** (this document) |
| Model readiness | The reused [#71 Ollama probe](ollama.md) (`omes health ollama`); model/provider routing stays **Hermes**-owned |
| Browser actions, locators, origin allowlists, the worker itself | A Hermes-owned adapter or an external worker ([#299](https://github.com/ahliweb/omes/issues/299)); never OMES |
| Per-job isolation (profile, user, filesystem, network, ceilings) | [#276](https://github.com/ahliweb/omes/issues/276), open [PR #282](https://github.com/ahliweb/omes/pull/282); not integrated here |
| Tenant/job scope, approval, lease, replay, idempotency, record ledger, whether to run a job | **AWCMS**; it verifies identity and freshness **outside** this document |

A `ready` document is a *producer claim about this host at `observed_at`*. It is not authorization to run a browser job, not proof that the worker is isolated, and not a statement about any Hermes-native tool use reached through other Hermes entry points (OMES does not mediate those; see [architecture §18.2](architecture.md#182-limitations--read-this-before-assuming-omes-mediates-agent-behavior)).

The output is exactly the document defined by [`contracts/browser-transfer/v1/browser-worker-readiness.schema.json`](../contracts/browser-transfer/v1/browser-worker-readiness.schema.json) ([contract README](../contracts/browser-transfer/v1/README.md)). The command validates its own output against that schema with the repository's stdlib validator before printing it and refuses (exit 1, nothing on stdout) rather than emit an off-contract document.

## 2. Usage

```bash
export OMES_JOBS_TENANT_ID=tenant-1 OMES_JOBS_SERVER_ID=server-1 OMES_BROWSER_WORKER_ID=worker-1
export OMES_BROWSER_WORKER_CAPABILITY_PROFILE=deterministic      # or ai_assisted (+ OMES_OLLAMA_MODEL)
export OMES_BROWSER_WORKER_ADAPTER_VERSION=adapter-v1             # declared: OMES cannot observe the adapter
export OMES_BROWSER_WORKER_BROWSER_PIN=chromium-122.0.6261.94     # the version label the worker pins
export OMES_BROWSER_WORKER_PROFILE_ROOT=/var/lib/omes-browser/profiles   # mode 0700
export OMES_BROWSER_WORKER_HEARTBEAT_FILE=/run/omes-browser/heartbeat

omes health browser-worker            # human summary, exit 0 only for ready, 7 otherwise
omes health browser-worker --json     # exactly one contract document on stdout
omes health browser-worker --worker-id worker-2 --capability-profile ai_assisted --model qwen3.5:9b
```

Variables are listed in [configuration §15a](configuration.md#15a-omes-health-browser-worker--libomespyhealthbrowser_workerpy-issue-298). Logs and explanations go to stderr; with `--json`, stdout is the document alone.

| Exit | Meaning |
|---|---|
| `0` | state is `ready` |
| `7` | any other state (`degraded`, `blocked`, `unsupported`, `unknown`) — the document is still printed |
| `4` | **not configured**: the tenant id, server id or worker id is missing. No contract document can be produced without inventing an identifier, which OMES never does, so **nothing is printed to stdout** and stderr names the missing variable(s) |
| `2` | usage error or an invalid configuration value (including an id that does not match the contract's pattern); nothing on stdout |
| `1` | internal error, including a failed contract self-check; nothing on stdout |

Output of the producer on a host with every declaration present but no isolation evidence source (the only result reachable on a real host today; `tests/integration/health-browser-worker.bats` asserts exactly this shape and reason list, with fixture identifiers):

```json
{"schema_version": "1.0.0", "tenant_id": "tenant-1", "server_id": "server-1", "worker_id": "worker-1", "state": "unknown", "browser_engine": "chromium", "adapter_version": "adapter-v1", "browser_version": "chromium-122.0.6261.94", "model_provider": "none", "model_ready": "not_required", "session_isolation": "unknown", "exposure": "loopback", "capability_profile": "deterministic", "observed_at": "2026-10-14T12:00:00Z", "reason_codes": ["ISOLATION_EVIDENCE_NOT_INTEGRATED"]}
```

The same document once an isolation source reports `per_job` (shape only — this requires the future #276 integration and is exercised in tests by injecting that source; it cannot be produced on a real host yet):

```json
{"schema_version": "1.0.0", "tenant_id": "tenant-1", "server_id": "server-1", "worker_id": "worker-1", "state": "ready", "browser_engine": "chromium", "adapter_version": "adapter-v1", "browser_version": "chromium-122.0.6261.94", "model_provider": "none", "model_ready": "not_required", "session_isolation": "per_job", "exposure": "loopback", "capability_profile": "deterministic", "observed_at": "2026-10-14T12:00:00Z", "reason_codes": ["READINESS_CHECKS_PASSED"]}
```

## 3. State precedence

The overall `state` is the most severe state among all findings:

| Rank | State | When |
|---|---|---|
| 1 | `unsupported` | The platform is outside the OMES support matrix (`detect_tier`). Reserved for this. Short-circuits: no other probe runs. |
| 2 | `blocked` | A hard safety or prerequisite failure that must be fixed before any job is leased: a CDP/MCP/Ollama listener bound beyond loopback, a missing or mismatched browser, an unsafe profile root, an orphan browser session or leftover profile, a cloud model destination, or isolation reported as not configured. |
| 3 | `unknown` | Evidence is missing, unreadable or not integrated, or a required declaration is absent. **Absence of evidence is never `ready`.** |
| 4 | `degraded` | A known, usually recoverable impairment: model unhealthy, resource floor not met, heartbeat missing or stale, profile root absent. |
| 5 | `ready` | No finding at all. The reason list is exactly `["READINESS_CHECKS_PASSED"]`. |

`unknown` deliberately outranks `degraded`: a known minor impairment must not hide behind a verdict when something else could not be checked at all. Consumers must treat **every** state other than `ready` as "do not lease jobs". The `reason_codes` array lists every finding, most severe first, capped at the schema's 20 entries (a blocking code is never the one dropped).

As a defense in depth, a `ready` result is re-checked against its own fields (Chromium present with a known version and declared adapter, `loopback` exposure, `per_job` isolation, model `ready`/`not_required`); any mismatch downgrades to `unknown` with `READINESS_INVARIANT_VIOLATED`.

## 4. What is checked

All probes are read-only, use fixed argv with bounded timeouts (never a shell), and report metadata only.

1. **Browser runtime provenance.** Candidate order: (a) the operator-declared `OMES_BROWSER_WORKER_BROWSER_BIN` — authoritative, never silently replaced by another browser; (b) the Playwright browser cache (`PLAYWRIGHT_BROWSERS_PATH`, default `~/.cache/ms-playwright`), where Hermes' installer places its "bundled Playwright Chromium" — found by directory name, the `INSTALLATION_COMPLETE` marker and an executable's presence, **never executed**, and the version label is the Playwright revision (`playwright-chromium-1194`); (c) `chromium`, `chromium-browser`, `google-chrome`, `google-chrome-stable` on `PATH`, queried with `--version` only. A version label (`chromium-122.0.6261.94`, `chrome-…`, `playwright-chromium-<rev>`) must equal the declared `OMES_BROWSER_WORKER_BROWSER_PIN`; an undeclared pin is `unknown`, a different one is `blocked`. Raw version output is never echoed. OMES does not look inside Hermes' own install tree or `$HERMES_HOME`, so the Hermes `agent-browser` version is **not observed** — hence the declared adapter version. This does not change `omes health versions`, which still reads `PATH` binaries only.
2. **CDP / MCP / Ollama listener binding**, reusing the [#80 exposure audit](security.md#31-exposure-audit-omes-audit-exposure-issue-80)'s listener table (`exposure.collect_listeners`). Watched: browser-control listeners (CDP ports 9222–9229 by default, or a Chromium-family process name), MCP servers (the documented Playwright MCP example port 8931, or an `mcp` process name), the Ollama endpoint port, plus operator-declared extra ports. **Any non-loopback bind of those is `blocked`**, and `OMES_EXPOSURE_ALLOW` deliberately does **not** approve it. The contract's `exposure` is `loopback` (nothing beyond loopback), `private_boundary` (bound to a private or link-local address; still blocked) or `public_blocked` (wildcard or global address); an unreadable socket table is `unknown`, never `loopback`. Process visibility follows `ss -p` permissions for the invoking user.
3. **Model readiness** (only for the declared `ai_assisted` profile), by calling the [#71 `omes health ollama` implementation](ollama.md) as a library — no probe is re-implemented and its CLI/JSON output is unchanged (a regression test pins its key sets). It sends the same short synthetic non-secret prompt that command does and is the only network-active probe (loopback by default). Set `OMES_OLLAMA_PROFILE` to `tools` or `structured` to require those capabilities; the default `text` only requires chat. A `deterministic` worker reports `model_provider: none`, `model_ready: not_required`. An undeclared profile reports `unknown`, not `not_required`.
4. **Model egress.** For `ai_assisted`, the configured Hermes `model.provider` is classified with the [#216](ai-data-privacy-and-model-security.md) destination classifier (`omes health ai-privacy`'s helper). A cloud destination is `blocked` and the document reports `model_provider: unknown` rather than a healthy-looking `ollama`; an unreadable or unlisted provider is `unknown`. OMES reads this and never rewrites Hermes routing, and non-Ollama providers cannot produce `model_ready: ready` because the contract only enumerates `ollama`. Per-job network egress allowlists and origin allowlists are **not** observed here (see §7).
5. **Resource floors** — free disk (profile root, else `$HOME`), `MemAvailable`, and usable CPUs (`sched_getaffinity`) against the declared minimums (defaults 2048 MB, 2048 MB, 2 CPUs). Per-job **ceilings** (cgroup limits) are isolation evidence and are part of §7.
6. **Orphan sessions and profile cleanup.** One `ps` call finds Chromium-family main processes (not `--type=` children) that look like automation (`--headless*`, `--remote-debugging-pipe|port`, `--enable-automation`) or run with a `--user-data-dir` under the declared profile root, and counts those older than `OMES_BROWSER_WORKER_SESSION_MAX_AGE_SECONDS` (default 3600). OMES holds no lease table, so this is an **age heuristic**: a legitimate job longer than the ceiling is reported as `blocked` until the ceiling is raised. The declared profile root is `lstat`ed (must be a real directory owned by the invoking user or root with no group/other access) and its immediate children are counted by type and mtime; a directory older than the ceiling that no live browser references is a leftover profile. Process arguments are parsed locally and never copied anywhere. OMES **never kills a process or deletes a profile**: cleanup belongs to the worker's supervisor.
7. **Heartbeat freshness.** The operator-declared `OMES_BROWSER_WORKER_HEARTBEAT_FILE` is `lstat`ed; its **mtime is the heartbeat** and nothing else — its content is never read, so no format is invented and nothing sensitive can travel through it. It must be a regular file that is not world-writable; a future-dated mtime beyond a 5-second skew allowance is invalid. No heartbeat producer exists in OMES; whatever worker is installed touches the file.
8. **`hermes_version`** (optional field) is extracted from `hermes --version` via the existing [#83](compatibility-evidence.md) probe, reduced to the dotted version token.

## 5. Safety properties

- **Read-only.** No write, delete, chmod, signal, install or restart anywhere in the module (a static AST test enforces the absence of those calls and of any browser-automation import; a filesystem-snapshot test enforces no change). It honours `--dry-run` trivially.
- **No cookies, sessions or profile content.** Profile directories are never opened or listed beyond the root's immediate children; heartbeat contents are never read; canary-value tests assert nothing from a profile, a heartbeat or a process argument (`--proxy-server=http://user:…@…`) reaches stdout, stderr, logs or the document. The declared profile root and heartbeat file are not OMES-managed paths and belong to no `omes backup` or `omes agent-backup` class (the default `omes-host` classes are `config` and `skills`); nothing here adds them, and they must never be added, because cookie jars and session storage are credential-equivalent ([hermes-backup.md](hermes-backup.md)).
- **No new listener, no router.** The command opens no socket of its own, adds no public MCP/CDP/Ollama endpoint, and does not route models ([ADR-0029](adr/0029-ai-data-boundary-and-private-inference.md)).
- **No privilege escalation.** It runs as the invoking user and never calls `sudo`; facts the user cannot see (other users' sockets or processes) surface as `unknown`/partial evidence, not as a pass.
- **No fabrication.** Missing tenant/server/worker ids exit `4` instead of defaulting; unknown stays `unknown`.
- **Fail closed.** See §3. The reason-code table below is the stable vocabulary; `tests/py/health/test_browser_worker.py` has one scenario per code and fails if a code is added without one or without a row here.

## 6. Reason codes

Codes are stable identifiers (`^[A-Z][A-Z0-9_]{0,63}$`). The *State* column is the severity a code contributes; the document's state is the most severe contribution (§3).

**Ready**

| Code | State | Meaning | Remediation |
|---|---|---|---|
| `READINESS_CHECKS_PASSED` | ready | every check passed; the only code a `ready` document carries | — |

**Platform and host facts**

| Code | State | Meaning | Remediation |
|---|---|---|---|
| `PLATFORM_UNSUPPORTED` | unsupported | this OS/architecture is outside the OMES support matrix | run the worker host on a supported platform ([compatibility matrix](compatibility-matrix.md)) |
| `HOST_FACTS_UNAVAILABLE` | unknown | platform support could not be determined (no host facts supplied) | run through `omes health browser-worker`, which supplies the host facts |

**Declarations**

| Code | State | Meaning | Remediation |
|---|---|---|---|
| `CAPABILITY_PROFILE_UNDECLARED` | unknown | the worker's capability profile (deterministic or ai_assisted) is not declared, so the model requirement is unknown | set `OMES_BROWSER_WORKER_CAPABILITY_PROFILE` (or `--capability-profile`) |
| `ADAPTER_VERSION_UNDECLARED` | unknown | the browser adapter version is not declared; OMES cannot observe an adapter it does not own | set `OMES_BROWSER_WORKER_ADAPTER_VERSION` to the pinned adapter version |
| `BROWSER_PIN_UNDECLARED` | unknown | no pinned browser version is declared, so browser runtime provenance cannot be checked | set `OMES_BROWSER_WORKER_BROWSER_PIN` to the expected browser version label |

**Browser runtime provenance**

| Code | State | Meaning | Remediation |
|---|---|---|---|
| `BROWSER_BINARY_MISSING` | blocked | no usable Chromium-family browser binary was found | install the browser the worker pins, or set `OMES_BROWSER_WORKER_BROWSER_BIN` to its absolute path |
| `BROWSER_VERSION_UNKNOWN` | unknown | a browser was found but its version could not be determined | check that `<browser> --version` works for the operator account |
| `BROWSER_PIN_MISMATCH` | blocked | the observed browser version differs from the declared pin | reinstall the pinned browser, or update the pin after reviewing the new version |

**Listener exposure**

| Code | State | Meaning | Remediation |
|---|---|---|---|
| `CDP_LISTENER_NONLOOPBACK` | blocked | a browser remote-debugging (CDP) listener is bound beyond loopback | bind it to 127.0.0.1 or use `--remote-debugging-pipe`; `OMES_EXPOSURE_ALLOW` does not apply to browser control |
| `MCP_LISTENER_NONLOOPBACK` | blocked | an MCP server listener is bound beyond loopback | bind it to 127.0.0.1 or use stdio; `OMES_EXPOSURE_ALLOW` does not apply to browser control |
| `OLLAMA_LISTENER_NONLOOPBACK` | blocked | an Ollama listener is bound beyond loopback | bind Ollama to 127.0.0.1 (`OLLAMA_HOST`); see [ollama.md](ollama.md) |
| `EXPOSURE_AUDIT_UNAVAILABLE` | unknown | the listener table could not be read completely (ss missing, failed or timed out) | install iproute2 (`ss`) and re-run; see `omes audit exposure` |

**Model and egress**

| Code | State | Meaning | Remediation |
|---|---|---|---|
| `MODEL_NOT_CONFIGURED` | unknown | the ai_assisted profile is declared but no Ollama model id is configured | set `OMES_OLLAMA_MODEL` (or `--model`) |
| `MODEL_UNAVAILABLE` | degraded | the local model is not healthy (the reused `omes health ollama` check is not ready) | run `omes health ollama` for the layer-by-layer cause; see [ollama.md](ollama.md) |
| `MODEL_PROBE_ERROR` | unknown | the model probe failed unexpectedly, so model readiness is unknown | run `omes health ollama --json` and inspect its output |
| `EGRESS_MODEL_DESTINATION_CLOUD` | blocked | Hermes is configured to route model calls to a cloud destination | use a local-only posture for browser workers ([AI privacy](ai-data-privacy-and-model-security.md)); OMES never rewrites Hermes routing |
| `EGRESS_MODEL_DESTINATION_UNKNOWN` | unknown | the model destination class could not be determined (no hermes, unreadable or unlisted provider) | run `omes health ai-privacy`; set a documented local provider in Hermes |

**Isolation**

| Code | State | Meaning | Remediation |
|---|---|---|---|
| `ISOLATION_EVIDENCE_NOT_INTEGRATED` | unknown | per-job browser isolation (and per-job egress/resource ceilings) evidence is not integrated yet | Not implemented yet (tracked in #298, depends on #276 / PR #282) |
| `ISOLATION_NOT_CONFIGURED` | blocked | the isolation evidence source reports that per-job isolation is not configured | configure per-job isolation (#276) before leasing browser jobs |

**Resources**

| Code | State | Meaning | Remediation |
|---|---|---|---|
| `RESOURCE_DISK_LOW` | degraded | free disk space is below the declared minimum | free disk space or lower `OMES_BROWSER_WORKER_DISK_MIN_MB` deliberately |
| `RESOURCE_MEMORY_LOW` | degraded | available memory is below the declared minimum | free memory or lower `OMES_BROWSER_WORKER_MEM_MIN_MB` deliberately |
| `RESOURCE_CPU_LOW` | degraded | usable CPU count is below the declared minimum | provision more CPUs or lower `OMES_BROWSER_WORKER_CPU_MIN` deliberately |
| `RESOURCE_FACTS_UNAVAILABLE` | unknown | disk, memory or CPU facts could not be read | check that `/proc/meminfo` and the profile filesystem are readable |

**Sessions and profiles**

| Code | State | Meaning | Remediation |
|---|---|---|---|
| `PROFILE_ROOT_NOT_CONFIGURED` | unknown | no browser profile root is declared, so leftover profile directories cannot be checked | set `OMES_BROWSER_WORKER_PROFILE_ROOT` to the worker's per-job profile directory |
| `PROFILE_ROOT_MISSING` | degraded | the declared browser profile root does not exist | create it with mode 0700 owned by the worker account |
| `PROFILE_ROOT_UNSAFE` | blocked | the browser profile root (or an entry in it) is a symlink, not a directory, foreign-owned or accessible to group/other | make the root a real directory owned by the worker account with mode 0700 and remove symlink entries |
| `ORPHAN_BROWSER_PROCESS` | blocked | an automation browser process has outlived the session ceiling | stop the leftover browser process through the worker's own supervisor; OMES never kills processes |
| `STALE_PROFILE_DIRECTORY` | blocked | a per-job browser profile directory outlived the session ceiling with no live browser using it | have the worker supervisor delete the leftover profile; OMES never deletes browser profiles |
| `PROCESS_TABLE_UNAVAILABLE` | unknown | the process table could not be read, so orphan sessions cannot be ruled out | install procps (`ps`) and re-run |

**Heartbeat**

| Code | State | Meaning | Remediation |
|---|---|---|---|
| `HEARTBEAT_NOT_CONFIGURED` | unknown | no worker heartbeat file is declared (no producer exists in OMES yet) | set `OMES_BROWSER_WORKER_HEARTBEAT_FILE` to a file the worker touches periodically |
| `HEARTBEAT_MISSING` | degraded | the declared heartbeat file does not exist | start the worker, or fix the heartbeat path |
| `HEARTBEAT_STALE` | degraded | the heartbeat file is older than the declared freshness window | check that the worker is running; see `OMES_BROWSER_WORKER_HEARTBEAT_MAX_AGE_SECONDS` |
| `HEARTBEAT_INVALID` | unknown | the heartbeat file is not a regular file, is world-writable, is dated in the future or is unreadable | make it a regular file writable only by the worker account |

**Internal**

| Code | State | Meaning | Remediation |
|---|---|---|---|
| `READINESS_INVARIANT_VIOLATED` | unknown | internal consistency check failed (a ready state with a non-ready field); reported as unknown | report this as an OMES bug with the redacted output |

## 7. Not implemented yet

| Item | Status |
|---|---|
| Per-job browser isolation evidence (`session_isolation: per_job`), including the per-job network egress allowlist and cgroup resource ceilings. The existing drift check lives only on the unmerged #276 branch ([PR #282](https://github.com/ahliweb/omes/pull/282)); it is not copied here. Until it is wired in through the single `default_isolation` source, `session_isolation` is `unknown` with `ISOLATION_EVIDENCE_NOT_INTEGRATED` and no document can be `ready`. | Not implemented yet (tracked in #298, depends on #276 / PR #282) |
| Cryptographic (sha256/signature) pinning of the browser executable. Only the version label is compared; hashing hundreds of MB per health call is deliberately avoided, and the [#236](provenance.md) artifact records are model-artifact specific. | Not implemented yet (tracked in #298) |
| Observing the Hermes `agent-browser` version and the Browser Use CLI version (they live in Hermes' own install tree, which OMES does not read); the adapter version is declared by the operator instead. `omes health versions` is unchanged. | Not implemented yet (tracked in #299) |
| A heartbeat **producer**, the browser worker, its Playwright/Hermes adapter and its supervisor (profile cleanup, orphan kill, lease expiry) | Not implemented yet (tracked in #299; OMES will not own a browser executor) |
| Wiring the document into the pull-worker/Control Center transport and the AWCMS `/admin/omes/*` projection | Not implemented yet (tracked in #294 child issues; AWCMS-owned) |
| Shipping this command as an allowlisted `omes job` operation | Not implemented yet (tracked in #298); the host safe-operation allowlist is intentionally **not** extended with browser actions |
| An `omes doctor` check **is** wired (opt-in, advisory): when `OMES_BROWSER_WORKER_ID` is set, `omes doctor` reports a `browser_worker` row (OK for `ready`, WARN with the state and reason codes otherwise, never FAIL). | Implemented |

## 8. Testing

- `tests/py/health/test_browser_worker.py` — injected-dependency unit tests: the state precedence for every pair of severities, **one scenario per reason code** (each also validated against the schema), not-configured and invalid-value handling, browser discovery (explicit, Playwright cache, `PATH`), exposure variants, model and egress, session/profile/heartbeat checks, read-only and no-secret guarantees (filesystem snapshot, `open()` spy, canary values), the static no-mutation/no-automation guard, the `ollama.py` output-shape regression, and doc/registry drift.
- `tests/integration/health-browser-worker.bats` — the real `bin/omes` entry point through `tests/shims/{ss,chromium,hermes,ollama}` and a canned `ps` stub, with every emitted document validated against the published schema (the shim/container smoke test; `tests/run.sh` runs it in Docker when `bats` is not installed). `tests/integration/doctor.bats` covers the doctor row.
- `tests/py/health/test_exposure.py` and `tests/py/health/test_ai_privacy.py` cover the two additive helpers (`exposure.collect_listeners`, `ai_privacy.collect_destination_class`) the producer reuses; `omes audit exposure` and `omes health ai-privacy` output is unchanged.

Real-host behaviour (a real Chromium, a real Hermes install, a GPU Ollama) belongs to the disposable-host matrix, not to default CI; no test needs live credentials or external network.

## 9. Related

[browser transfer wire contracts v1](../contracts/browser-transfer/v1/README.md) · [Ollama](ollama.md) · [exposure audit](security.md#31-exposure-audit-omes-audit-exposure-issue-80) · [AI privacy and model security](ai-data-privacy-and-model-security.md) · [compatibility evidence](compatibility-evidence.md) · [CLI reference §4.13](cli.md#413-omes-health-layered-healthreadiness-checks) · [configuration §15a](configuration.md#15a-omes-health-browser-worker--libomespyhealthbrowser_workerpy-issue-298) · [threat model §5.z2](threat-model.md#5z2-browser-worker-host-evidence-threat-delta-issue-298) · [docs index](README.md)
