---
issue: 9
type: added
---
Add `lib/omes/pkg.sh` (package install/query helpers, `pkg_map` logical-name-to-real-package mapping with per-release overrides, apt repository validation and deb822 `.sources` management), refactor `modules/apt-base` to use it, and document the mapping table, repository policy, and network/partial-failure exit-code matrix in `docs/packages.md`.
