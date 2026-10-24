---
issue: 298
type: added
---
Add `omes health browser-worker`, a read-only producer of the #296 browser-worker readiness document (ready, degraded, blocked, unsupported or unknown with stable reason codes) that reuses the Ollama probe, the exposure audit and the AI-privacy destination classifier, plus an opt-in advisory `omes doctor` row; it refuses (blocked, BROWSER_BINARY_UNSAFE) to execute a browser binary that is not root-owned or is group/world-writable when run as root, it ships no browser executor and never reads cookies or profiles, and per-job isolation evidence is not integrated yet (depends on #276) so the state is never ready today.
