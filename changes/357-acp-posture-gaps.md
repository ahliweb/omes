---
issue: 357
type: changed
---
`omes health acp` now also reports the OMES-recorded gateway hardening and egress profile (read-only from the OMES state file, `profile` block and `acp.hardening_profile_*` and `acp.egress_profile_*` findings, where an absent or unrecognised key is `unknown` and never reads as safe or ok, and recorded-off hardening warns only when the ACP surface includes execution), adds the `acp.allow_always_discouraged` operator guidance, and keeps an absent `platform_toolsets.acp` as a deliberate `warn` (the upstream default toolset includes execution) with the rationale recorded in ADR-0032 and `docs/hermes-integration.md` section 17.2; ADR-0032 also records the five entry criteria for outbound ACP as a revisit trigger if hermes-agent#5257 ships, and OMES still adds no ACP server, client or proxy (R9).
