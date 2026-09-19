---
issue: 84
type: added
---
Add `omes audit provenance`, a supply-chain provenance audit of `<state-dir>/provenance/*.json` records (recorded at Hermes install time): a checksum mismatch fails closed (exit 7), an unverified checksum or mutable installer URL warns, and executable files under managed skill/plugin/MCP paths are listed for review but never executed.
