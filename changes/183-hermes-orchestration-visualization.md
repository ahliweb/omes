---
type: minor
area: control-center
issue: 183
title: Hermes delegated-task orchestration and live subagent visualization
---

Added contracts, ingestion, state tree reconstruction, and CLI projection for visualizing Hermes delegated-task orchestration and live subagent processes in the Control Center.
- Created `hermes-orchestration-event.schema.json` and `hermes-orchestration-tree.schema.json` with comprehensive test fixtures.
- Implemented `lib/omes/py/agent/orchestration.py` with XSS sanitization, secret stripping, duration calculations, and stale task detection.
- Added `omes agent orchestration <stream|snapshot|prune>` CLI commands.
- Authored ADR-0028 documenting architectural trade-offs and read-only projection boundaries.
- Added comprehensive unit and integration tests.
