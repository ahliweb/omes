---
issue: 8
type: fixed
---
`omes install --module <name>` no longer exits 5 when a dependency of the named module belongs to the other privilege scope (the dependency is skipped and reported), and `omes uninstall --module <name>` no longer cascades into rolling back the module's dependencies.
