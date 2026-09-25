---
issue: 236
type: security
---
Add model/runtime artifact provenance and integrity evidence for local inference (threat AI-06): operators declare artifact paths OMES should track, `omes audit provenance --verify-artifacts` hashes and records them using the existing supply-chain checksum model with a checksum mismatch or missing artifact always failing closed, `omes health ai-privacy` now reports a cheap, hashing-free `model_artifact_provenance` summary that blocks or warns when evidence for a currently-local destination is unavailable, and the re-hash cache keys on size/mtime/ctime/inode/device (not mtime alone) so a same-size content swap with a restored mtime is still detected and rehashed.
