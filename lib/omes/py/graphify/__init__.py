"""OMES Graphify helpers (stdlib only, ADR-0012).

This package renders vault-ready Markdown notes from a graphify-produced
``graph.json`` file. It never talks to Obsidian (never installs, starts,
or manages it - docs/graphify.md §1.2/§1.4) and never re-implements
graphify's own extraction logic; it only reads the already-extracted
``graph.json`` and writes Markdown + YAML front matter under an
OMES-managed subdirectory of an operator-provided vault.
"""
