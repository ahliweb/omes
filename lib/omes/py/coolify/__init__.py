"""lib/omes/py/coolify - optional Coolify deployment adapter (issue #97).

Stdlib only (ADR-0012). This package does not import from any sibling
lib/omes/py/<pkg>/ package (see lib/omes/py/jobs/audit.py's docstring for
why: each extension command/package owns its own dependency graph so it
can evolve independently). Where this package needs the same shape as
another package (the jobs audit-log record shape, the jobs JSON Schema
validator subset), it holds a deliberate copy, not an import.

See docs/coolify-adapter.md for the full design and
docs/agent-orchestration-roadmap.md section 2.3 for the authoritative
`spec.backend: coolify` manifest shape this package's `mapping` module
implements the OMES side of.
"""
