---
issue: 169
type: security
---
Pin installer dependencies and remove unverified fetch paths: `install/bootstrap.sh` enforces explicit release channels (stable by default, pinned to an immutable tag), validates the origin URL, refuses destructive overwrites, fails closed on git errors, and records verified checkout provenance.
