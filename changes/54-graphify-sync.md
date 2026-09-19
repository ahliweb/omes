---
issue: 54
type: added
---
Add `omes graphify sync <path>` (OMES-owned manifest-based change detection, wrapping upstream's real — but previously undocumented — `graphify update`/`extract` subcommands with debounce, loop avoidance, and interrupted-run recovery) and the read-only `omes graphify status <path>`; corrects docs/graphify.md's earlier "no update/watch" claim now that both are verified to exist as real subcommands.
