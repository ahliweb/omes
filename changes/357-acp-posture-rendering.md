---
issue: 357
type: changed
---
`omes health acp` human (non `--json`) output now renders the recorded gateway hardening and egress profile (`profile status`, `hardening profile` and `egress profile` lines), where an absent, malformed or unrecognised profile always renders as `unknown` and never as ok or safe, and `docs/cli.md` and the threat-model rows MA-04 and MA-05 document the new `profile` fields, the `acp.hardening_profile_*`, `acp.egress_profile_*` and `acp.allow_always_discouraged` findings and the allow-always guidance. Not implemented yet (tracked in #357): live verification of the hardening and egress unit state, which needs a real host.
