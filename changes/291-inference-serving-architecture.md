---
issue: 291
type: docs
---
Documented a provider-neutral AI inference-serving architecture boundary and evidence/readiness design without introducing any new runtime, model router, service installer, or deployment backend. vLLM (#289) and Kubernetes/OpenShift (#290) remain explicitly deferred and not implemented; generic read-only inference endpoint evidence (#292) is a separately tracked follow-up. Clarified reuse of Hermes-native routing, existing Ollama and host health, AI privacy/egress, source authority and Mission Control.
