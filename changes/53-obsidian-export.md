---
issue: 53
type: added
---
Add `omes graphify export <graphify-out-dir> --vault <path>` (plus `export rollback`): renders a prior extraction into vault-ready Markdown, preferring upstream `graphify export obsidian` (verified against graphify 0.9.64) with OMES's own front-matter markers injected, falling back to a stdlib-only renderer when upstream is unavailable — writing only under the vault's own managed subdirectory, backed up before every write, and refusing to overwrite any existing note lacking the `omes_generated` marker.
