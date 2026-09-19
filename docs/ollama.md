# Ollama integration

> Status: describes the actual repository state for issue
> [#71](https://github.com/ahliweb/omes/issues/71). OMES does not install
> or manage the Ollama service itself (tracked separately, if ever taken
> up); this document covers `omes health ollama`, the layered health
> checker OMES ships today, which assumes an operator- or
> distro-installed Ollama.

## 1. What OMES does and does not do

OMES does **not** install Ollama, manage its systemd unit, or configure
its models. Those remain the operator's (or a future, separate module's)
responsibility. What OMES provides is a read-only, side-effect-free
health check: `omes health ollama` and, when Ollama looks configured,
an advisory `omes doctor` signal via `modules/hermes`'s `module_doctor`
hook (see [docs/hermes-integration.md](hermes-integration.md)).

## 2. Install pointer

Follow Ollama's own documentation: <https://docs.ollama.com/linux>. On a
systemd-managed Linux host, the standard install creates an
`ollama.service` unit and binds the local API to `127.0.0.1:11434` by
default. OMES's health checker assumes this default unless `OLLAMA_HOST`
says otherwise.

## 3. Endpoint and bind-address policy

`OLLAMA_HOST` (default `127.0.0.1:11434`) is the endpoint the checker
calls. The **service** layer includes a bind-address policy check: if the
resolved host is not loopback (`127.0.0.1`, `localhost`, `::1`) and
`OMES_OLLAMA_ALLOW_REMOTE=1` is not set, that check is reported as
`fail` — Ollama's own local API has no built-in authentication, so a
non-loopback bind is a real exposure risk (see also
[docs/security.md](security.md) and the exposure audit,
[docs/hermes-integration.md](hermes-integration.md) "Health and
readiness"/issue [#80](https://github.com/ahliweb/omes/issues/80)).
`OMES_OLLAMA_ALLOW_REMOTE=1` is an explicit, opt-in acknowledgment that
the operator has accepted that exposure some other way (a firewall, a
reverse proxy, etc.) — the checker does not attempt to validate that.

## 4. Layers

1. **Service**: `ollama` binary present, endpoint reachable, `/api/version`
   responds, bind-address policy (above).
2. **Model**: the configured model (`OMES_OLLAMA_MODEL` or `--model`) is
   present in `/api/tags`, loads within `OMES_OLLAMA_LOAD_TIMEOUT` (default
   30s) via a bounded `/api/generate` call, returns a non-empty response,
   and its runtime placement (from `/api/ps`'s `processor` field) matches
   `OMES_OLLAMA_EXPECT_PLACEMENT` (`gpu`, `cpu`, or `any` — the default,
   which skips the comparison).
3. **Capability**: `chat` (always evaluated), plus
   `structured_output`/`tool_calling`/`embeddings`/`vision` gated by the
   active profile (§5). Each capability reports `pass`, `fail`, or
   `not_applicable`. A `fail` on a *required* capability (or on `chat`,
   which is always required) makes the whole check `ready: false`, exit 7.
   An optional capability that is simply not part of the active profile is
   `not_applicable` and never affects readiness.

Every HTTP call the checker makes uses a bounded timeout
(`OMES_HEALTH_TIMEOUT`, default 10s; the model-load call specifically
uses `OMES_OLLAMA_LOAD_TIMEOUT`, default 30s) — the checker cannot hang.

## 5. Profiles

`OMES_OLLAMA_PROFILE` selects a named profile (default `text`):

| Profile | Required beyond `chat` |
|---|---|
| `text` | none |
| `structured` | `structured_output` |
| `tools` | `tool_calling` |
| `embeddings` | `embeddings` |
| `vision` | `vision` |
| `full` | all four |

`OMES_OLLAMA_PROFILE_FILE` (a path to a JSON file
`{"name": "...", "capabilities": ["structured_output", "tool_calling"]}`)
overrides `OMES_OLLAMA_PROFILE` when set, for a custom capability set.

Every probe uses a synthetic, non-secret prompt or fixture — never a real
document, credential, or private data:

- `structured_output`: asks for `{"ok": true}` and validates the response
  against a minimal JSON Schema (stdlib-only validation, no `jsonschema`
  dependency — ADR-0012).
- `tool_calling`: offers a single allowlisted no-op tool (`omes_noop`) and
  validates the model requested only that tool with JSON-object arguments.
- `embeddings`: calls `/api/embed` twice with the same short synthetic
  string and checks the returned vector is non-empty with a stable
  dimension across both calls.
- `vision`: a tiny 1×1 PNG generated in-process (no network fetch, no
  image library) — never a real or user-supplied image.

## 6. Remediation

| Signal | Remediation |
|---|---|
| `service:binary` fail | Install Ollama: <https://docs.ollama.com/linux> |
| `service:bind_policy` fail | Bind Ollama to `127.0.0.1` (the default), or set `OMES_OLLAMA_ALLOW_REMOTE=1` to explicitly accept remote exposure |
| `service:version` fail | Start the Ollama service (`systemctl [--user] start ollama`) and verify `OLLAMA_HOST` |
| `model:present` fail | `ollama pull <model>` |
| `model:load_and_smoke` fail (timeout) | Verify available memory/GPU; try `ollama run <model>` manually |
| `model:placement` fail | Adjust `OMES_OLLAMA_EXPECT_PLACEMENT` or the model's GPU offload settings |
| `capability:*` fail | See docs/ollama.md §5 for what each capability probes; verify the model supports it (e.g. tool calling and vision are model-dependent — see <https://docs.ollama.com/capabilities/tool-calling>) |

No remediation text ever includes a token, credential, or private data —
the checker never reads or transmits them.

## 7. Testing

`tests/py/health/test_ollama.py` exercises every layer against a stdlib
`http.server`-based fake Ollama (service unavailable, model missing, load
timeout, malformed JSON, empty embedding, unsupported capability, wrong
placement, healthy) — no real Ollama or network access required.
`tests/integration/health-ollama.bats` covers the `omes health ollama`
bash/Python wiring (target dispatch, `--json`, exit codes) via
`tests/shims/ollama` (binary-presence only; the checker itself talks
HTTP, not the CLI) and an intentionally unreachable endpoint. A real
Ollama can be exercised by setting `OMES_TEST_REAL_OLLAMA=1`, but no test
in this repository does so automatically — see [docs/testing.md](testing.md).

## 8. Sources

- <https://docs.ollama.com/linux>
- <https://docs.ollama.com/api/introduction>
- <https://docs.ollama.com/api/openai-compatibility>
- <https://docs.ollama.com/capabilities/structured-outputs>
- <https://docs.ollama.com/capabilities/tool-calling>
- <https://docs.ollama.com/capabilities/embeddings>
- <https://docs.ollama.com/faq>
- [ADR-0012](adr/0012-python-stdlib-for-workflow-engines.md)
- [Issue #71](https://github.com/ahliweb/omes/issues/71)
