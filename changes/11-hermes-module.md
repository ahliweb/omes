---
issue: 11
type: added
---
Add the `hermes` module (user-scope): downloads and runs the upstream Hermes installer to a pinned/verifiable temp file, manages a PATH snippet and `$HERMES_HOME/.env` (0600), gates on `hermes doctor`, and wires `hermes` into the `server`/`hermes` profiles.
