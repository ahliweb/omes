---
issue: 325
type: fixed
---
Version and configuration probes run through the shared `_run()` helper in `lib/omes/py/provenance/versions.py` (used by `omes health versions`, `omes status` and the `omes health` posture checks that call `hermes config get`) now start the child with stdin set to `/dev/null` instead of inheriting the caller's stdin, so a probe of a stdio server such as `hermes acp` can no longer sit on an open pipe until its timeout.
