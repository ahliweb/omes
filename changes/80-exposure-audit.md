---
issue: 80
type: security
---
Add `omes audit exposure` (`lib/omes/cmd/audit.sh`, `lib/omes/py/health/exposure.py`): detects unsafe Hermes gateway/browser-control/MCP/Ollama listener exposure (loopback vs. LAN vs. wildcard) cross-referenced with `ufw`, detection-only.
