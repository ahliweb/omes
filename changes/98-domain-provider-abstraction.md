---
issue: 98
type: added
---
Define the provider-neutral domain/DNS contracts (`contracts/domains/v1/`: `RegistrarCapability`, credential references, search/availability/pricing/registration/renewal/transfer/contact-update/DNS/DNSSEC, domain-order state machine, routing decisions) and a stdlib routing/state-machine/fake-provider implementation (`lib/omes/py/domains/`) with fixtures and tests covering Cloudflare-like async and SRS-X-like document-required flows.
