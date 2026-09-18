---
issue: 12
type: added
---
Add the `hermes-gateway` (user, default) and `hermes-gateway-system` (root, opt-in) modules: `hermes gateway install`, a managed PATH drop-in, `systemctl [--user] enable --now`, consent-gated `loginctl enable-linger` on headless hosts only, and a `module_verify` that always warns that a green `systemctl is-active` does not prove the messaging adapter is connected.
