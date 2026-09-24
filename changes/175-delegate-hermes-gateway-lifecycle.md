---
issue: 175
type: changed
---
Delegate native agent deployment lifecycle to Hermes CLI (`hermes gateway`, ADR-0019). Hermes owns base unit creation (`hermes-gateway[-<profile>].service`), while OMES attaches resource quotas and hardening via drop-in overlays (`10-omes-agent-resources.conf`), with automatic legacy unit migration and multiplexed mode awareness.
