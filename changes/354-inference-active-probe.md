---
issue: 354
type: added
---
Added an opt-in `--active-probe` flag to `omes health inference-endpoint`. It is off by default and no environment variable can enable it. Once the model list check has passed, it sends one fixed, non-sensitive chat request (constant message, `max_tokens` 8, no tools, no credentials) to the same vetted loopback or private address with no redirects, verified TLS and a bounded deadline and size, and reports it in the `active_probe` layer. The prompt and completion are never logged or printed. A pass is model-dependent and proves only that fixture, and a timeout, 401/403, malformed or empty response, missing model or earlier failure is never ready. Without the flag the `active_probe` reason is now `active_probe_not_run` instead of `active_probe_not_implemented`. Credentialed endpoints and tool-call or streaming probes are Not implemented yet (tracked in #354).
