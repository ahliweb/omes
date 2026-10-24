---
issue: 276
type: added
---
Added per-deployment egress control and declared-versus-running drift detection for the rootless Compose backend: an optional `compose.egress` manifest field (`open`, the unchanged default, or `none`, which renders the network as `internal: true` so Docker gives it no external egress, all-or-nothing with no per-destination allowlist and not combinable with `ports`) in both the v1 and v2 schemas with fixtures, and a read-only `omes agent isolation-drift <name> [--json]` command that compares the declared image, `uid:gid`, `cap_drop`, read-only rootfs, `no-new-privileges`, network `internal` flag, resource limits and mounts with `docker inspect` output and reports findings with severities and a status of `ok`, `drift` or `unknown` (never `ok` without evidence); both are tested against the Docker shim only, so a per-destination egress allowlist, a runtime egress probe and real rootless-daemon evidence remain not implemented yet (tracked in #276).
