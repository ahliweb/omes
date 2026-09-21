# Hermes skill: content distribution approvals (issue #65)

This document describes a **Hermes-side** skill, not OMES code. It is shipped
from this repository (docs-as-contract, per AGENTS.md section 1) because the
mapping it defines is the security boundary between "a Telegram user typed a
command" and "an OMES `omes content` mutation happened" — but the skill itself
runs inside Hermes Agent, which owns Telegram messaging, channels, and inbound
command handling (AGENTS.md section 2). **OMES never polls Telegram and never
receives inbound Telegram messages directly** — see
`lib/omes/py/content/telegram.py`'s module docstring and
`docs/telegram-security.md`.

> **MIGRATION NOTE (ADR-0024, issue #179)**: In alignment with ADR-0024, content approvals
> and publishing jobs transition to AWCMS Control Center API and pull-worker jobs (#199)
> rather than local OMES CLI shell commands. The shell mapping below is maintained as a
> compatibility path during OMES v1.x.

## What this skill does

Hermes's Telegram gateway already applies its own DM/group allowlists
(`TELEGRAM_ALLOWED_USERS`, `TELEGRAM_ALLOWED_CHATS` /
`TELEGRAM_GROUP_ALLOWED_CHATS` — docs/telegram-security.md) before any message
reaches a skill at all. Once a message from an allowed sender arrives, this
skill recognizes six commands, each addressed to one content job:

```
approve <job-id>
reject <job-id>
edit <job-id> <new caption text...>
retry <job-id>
cancel <job-id>
status <job-id>
```

For each recognized command, the skill shells out to the `omes` CLI **exactly**
as follows — it does not call any OMES Python module directly, does not read
or write `OMES_CONTENT_ROOT` itself, and does not implement any of the
approval/authorization logic on the Hermes side. All of that logic lives in
`lib/omes/py/content/` and is exercised by this repository's own test suite
(`tests/py/content/test_telegram.py`).

| Chat command | Shell command Hermes runs |
|---|---|
| `approve <job-id>` | `omes content approve <job-id> --actor <telegram-user-id> --channel telegram --json` |
| `reject <job-id>` | `omes content reject <job-id> --actor <telegram-user-id> --channel telegram --json` |
| `edit <job-id> <text>` | `omes content edit <job-id> --actor <telegram-user-id> --channel telegram --caption "<text>" --json` |
| `retry <job-id>` | `omes content retry <job-id> --actor <telegram-user-id> --yes --json` |
| `cancel <job-id>` | `omes content cancel <job-id> --actor <telegram-user-id> --yes --json` |
| `status <job-id>` | `omes content status <job-id> --json` |

`<telegram-user-id>` is the **numeric** Telegram user id of the message
sender, exactly as Hermes's own gateway resolves it for its allowlist check —
never a display name, username, or chat id. The skill must reject (and reply
with an error, never silently drop) any command whose sender id it cannot
resolve to a numeric id.

Reply to the chat with the JSON `--json` output's human-readable summary (or,
for `status`, its formatted fields) — never with the raw JSON blob, and never
with anything read from `content/sessions/` (the skill has no reason to ever
read that path and must not).

## Why authorization still lives in OMES, not in this skill

Hermes's Telegram allowlist (`TELEGRAM_ALLOWED_USERS`) controls **who can talk
to the bot at all**. It does not mean everyone on that list should be able to
approve a publish. `omes content approve --channel telegram` (and `reject`,
`edit`) independently enforces a second, narrower check:
`OMES_CONTENT_APPROVERS` (an OMES-side operator config listing the Telegram
user ids trusted to approve content) intersected with `TELEGRAM_ALLOWED_USERS`
— an id must be in **both** to be treated as an authorized approver
(`lib/omes/py/content/telegram.py::authorized_approvers`). This skill must
always pass `--channel telegram` for `approve`/`reject`/`edit` so that check
runs; omitting it (or using the plain `--channel cli` path) is only for the
no-Telegram, no-Hermes MVP operator flow described in
`docs/content-distribution.md` section 7, and this skill must never use it.

`retry`, `cancel`, and `status` are not gated by `OMES_CONTENT_APPROVERS` —
they do not authorize a new external side effect the way `approve` does
(`retry` only re-attempts a publish that was already approved once;
`cancel`/`status` never publish anything) — but the underlying `omes` CLI
still binds every one of these to `--actor <telegram-user-id>` so the audit
log (`state/audit.jsonl`) always records who did what.

## Sending the approval request in the first place

`omes content notify <job-id> --chat-id <id>` (or `OMES_CONTENT_TELEGRAM_CHAT_ID`
as a default) sends the initial approval-request preview — caption, target
platforms, and the artifact's sha256 hash, plus a small ffmpeg-generated
thumbnail when `ffmpeg` is installed, otherwise text-only. This is typically
run by an operator or a cron/systemd-timer step **after** `omes content plan`,
not by this skill — the skill only handles the reply commands above. See
`docs/content-distribution.md` sections 7 and 13 for the full design.

## What this skill must never do

- Never call Telegram's long-polling read endpoint itself (Hermes's own
  gateway already owns that connection; a second poller causes a 409 conflict
  and steals updates — docs/telegram-security.md section 7).
- Never pass a bot token, cookie, or any `content/sessions/` path as a chat
  reply, log line, or argument to `omes`.
- Never invent a seventh command or bypass `--channel telegram` for
  `approve`/`reject`/`edit`.
