---
issue: 355
type: docs
---
Documented the coordinator tier policy (Opus-class manager, Sonnet-class complex work, Haiku-class bounded work, each tier as a separate Hermes profile) and conflict-key guidance for parallel side-effecting work in `docs/multi-agent-control-patterns.md` section 3.1a, with a cross-reference from ADR-0032. Conflict keys (repository, component, resource) are enforced by Hermes or the workspace, not OMES; OMES adds no coordinator, scheduler or tier-to-model mapping. Per-task tier selection, key-based serialisation of OMES host jobs and the upstream request for per-task tiering are Not implemented yet (tracked in #355). The job, worker and audit correlation propagation shipped by #356 is now recorded where #272 previously said it was not implemented.
