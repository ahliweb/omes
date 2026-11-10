---
issue: 276
type: added
---
The compose backend gains an opt-in `egress: "allowlist"` mode (`egressAllowlist` hostnames plus an operator-supplied, digest-pinned `egressProxyImage`, in both manifest versions): the agent is attached only to an `internal: true` network and reaches the outside world through a hardened proxy sidecar that renders deterministic tinyproxy default-deny config for the declared hostnames. Hostnames only (no wildcards, IP literals, localhost, single-label, `.local`/`.internal`, duplicates), and `allowlist` is rejected with published `ports`, a shared topology, a missing or unpinned proxy image; OMES ships no default proxy image. Existing `open` and `none` renders are unchanged. Shim-tested only; the runtime egress probe, sidecar drift check, doctor wiring, orphan cleanup and a matrix job remain Not implemented yet (tracked in #276).
