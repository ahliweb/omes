---
issue: 65
type: added
---
Add an outbound-only Telegram approval front end for the content
distribution workflow: `lib/omes/py/content/telegram.py` sends the
approval-request preview (caption, target platforms, artifact sha256,
and an optional ffmpeg-generated thumbnail) and status replies via
`sendMessage`/`sendPhoto`, using the same `curl -K` token-handling
pattern as `modules/hermes-gateway/telegram-allowlist.sh` (never argv,
never logged). `omes content approve|reject|edit --channel telegram`
additionally requires the actor to be a numeric Telegram user id present
in both `OMES_CONTENT_APPROVERS` and Hermes's `TELEGRAM_ALLOWED_USERS`,
and `approve --expected-hash` rejects a mismatched artifact hash. New
verbs: `omes content edit`, `omes content notify`, `omes content status`.
`skills/content/SKILL.md` documents how a Hermes skill maps inbound
Telegram chat commands to these CLI verbs — OMES itself never polls
Telegram or receives inbound messages.
