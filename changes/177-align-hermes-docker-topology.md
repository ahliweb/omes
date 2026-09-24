---
issue: 177
type: changed
---
Align container deployment with official Hermes Docker topology (ADR-0021). Support explicit `shared` and `dedicated` container topologies, map persistent storage to the canonical `/opt/data` layout, configure s6 tmpfs mounts (`/run`, `/tmp`), delegate in-container supervision to s6 and Hermes commands (`hermes profile start/stop/restart`), and preserve rootless host containment, resource limits, and rollback safety.
