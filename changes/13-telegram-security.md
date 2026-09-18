---
issue: 13
type: security
---
Add `modules/hermes-gateway/telegram-allowlist.sh` for safe Telegram allowlist management (atomic paired writes, numeric-only ids, `--check` half-enabled detection, token-redacted output, `getChat`/`getChatMember`/`getChatMemberCount` diagnostics that never touch the prohibited long-polling endpoint or place a token in argv/logs), wired into `hermes-gateway`'s `module_verify`, plus `docs/telegram-security.md`.
