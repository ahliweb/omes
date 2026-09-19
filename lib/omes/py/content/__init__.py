"""OMES content distribution workflow (stdlib only, ADR-0012 / ADR-0015).

Optional extension invoked via ``omes content ...`` (lib/omes/cmd/content.sh).
Never imported by, or a dependency of, the core `bin/omes` CLI or any
installer/module path. See docs/content-distribution.md for the design.
"""
