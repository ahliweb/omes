---
issue: 87
type: added
---
Add `omes agent doctor` (wired into `omes doctor`), `omes agent logs` for `backend: "compose"`, containerized-Hermes health that reuses `lib/omes/py/health/hermes.py`'s provider/channel layers through `docker compose exec -T`, and `lib/omes/runtime.sh`'s `runtime_agent_service_unit` as the single source of truth for per-agent unit naming (`Part of #87`, `Part of #96`).
