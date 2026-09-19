---
issue: 83
type: added
---
`omes status`/`omes status --json` now embeds the runtime version/compatibility evidence snapshot (issue #83) as an `evidence` object with the same keys `omes health versions --json` emits, and `modules/apt-base`/`modules/containers`/`modules/hermes` now record apt/uv/pipx package-manager provenance (issue #84) that `omes audit provenance` reports on.
