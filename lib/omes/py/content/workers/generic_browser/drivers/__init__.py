"""Pluggable browser drivers for the generic_browser worker (issue #66).

A driver is a Python module exposing three functions with the signature
``(payload: dict, ctx: DriverContext) -> dict`` (a worker-contract result
dict, see workers/base.py's `ok_result`/`typed_failure`):

  bootstrap(payload, ctx)  - manual/one-time login; MUST NOT publish.
  publish(payload, ctx)    - publishes `payload["source_path"]`.
  verify(payload, ctx)     - confirms `payload["url"]` is live/matches.

`manual_stub` (this package's default, always available, no third-party
dependency) implements all three by recording evidence only - it never
launches a real browser. An operator-installed driver (for example a
Playwright/Chromium driver, or Hermes's own browser automation) is
referenced by file path via `OMES_CONTENT_BROWSER_DRIVER` and loaded
dynamically by `worker.py`; OMES does not ship or depend on one.
"""
