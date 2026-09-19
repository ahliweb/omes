"""Platform worker subprocesses for the content distribution workflow
(issue #66). See docs/content-distribution.md section 6 for the contract
each worker under this package implements, and `base.py` for the shared
path-boundary/JSON-transport helpers every worker uses.

This package is imported two ways:

1. As part of the `content` package (`from content.workers import base`)
   when the manager (cli.py/jobs.py) resolves a worker executable.
2. As a standalone script's dependency: a worker is invoked as
   ``python3 <worker-executable> <operation>`` (never ``python3 -m``), so
   each worker file adds its own parent directory to `sys.path` before
   importing `base` as a top-level module. `base.py` therefore has no
   relative imports and no dependency on the rest of the `content`
   package, so both import paths resolve to the same module.

Stdlib only (ADR-0012).
"""
