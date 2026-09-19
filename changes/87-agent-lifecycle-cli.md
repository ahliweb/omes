---
issue: 87
type: added
---
Add `omes agent list|check|plan|apply|status|health|restart|logs|rollback`: a versioned JSON manifest, stdlib validator, and native systemd backend implementing the MVP agent deployment lifecycle (check -> plan -> backup -> mutate -> verify, declared -> ... -> healthy/degraded|failed|rolled-back).
