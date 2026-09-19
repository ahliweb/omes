# Telegram Integration Security

> Status: describes the actual repository state. Covers issue
> [#13](https://github.com/ahliweb/omes/issues/13):
> `modules/hermes-gateway/telegram-allowlist.sh` (allowlist add/remove/
> `--check`, and safe chat-id diagnostics). Builds on the `hermes` module
> (issue [#11](https://github.com/ahliweb/omes/issues/11), see
> [`docs/hermes-integration.md`](./hermes-integration.md) part 1) and the
> `hermes-gateway`/`hermes-gateway-system` modules (issue
> [#12](https://github.com/ahliweb/omes/issues/12), part 2).
>
> OMES is an independent, MIT-licensed, Omarchy-inspired compatibility
> layer. It is not official Omarchy. Telegram, Hermes Agent, and the
> policies they enforce are upstream products/behavior; OMES configures
> the environment they run in and documents the required posture — it
> does not author Hermes's own agent loop or Telegram's API.

## 1. Why this matters

Telegram long-polling reaches the bot from **every Telegram user on
earth**, not just the operator, until an allowlist filters them out
(`docs/threat-model.md` TB6). Combined with Hermes Agent's ability to
execute shell commands, read/write files, and act on instructions it
receives, a misconfigured or half-configured Telegram allowlist is a
direct path from "random internet user" to "commands executed on this
host." Every control in this document exists to keep that path closed by
default and to make any deviation from the safe defaults visible, not
silent.

## 2. Threat summary

This table is a working index into `docs/threat-model.md` §5 — read the
full rows there for likelihood/impact/mitigation detail; this is only a
map from "what could go wrong" to "where OMES addresses it in this repo."

| Threat model row | Summary | Addressed by |
|---|---|---|
| T04 | Bot token exposed via a hand-rolled API URL reaching shell history/stdout/a backed-up transcript | `telegram-allowlist.sh` never places the token in argv or a logged URL (§8); token redaction in `--check` (§4) |
| T07 | Unauthorized Telegram user reaches the bot because an allowlist uses a wildcard or is left unset | `telegram-allowlist.sh add`/`remove` accept only numeric chat ids, rejecting wildcards/empty outright (§4) |
| T08 | A group is enabled in only one of `TELEGRAM_ALLOWED_CHATS`/`TELEGRAM_GROUP_ALLOWED_CHATS`, "half-enabled" | `add`/`remove` always write both atomically; `--check` detects and flags an existing half-enabled group (§4, §5) |
| T09 | Operator edits the allowlist but doesn't restart the gateway; the edit silently does nothing | Every `add`/`remove` prints the exact restart command and an explicit "inert until restart" warning (§6) |
| T10 | A call to the prohibited long-polling read endpoint causes a 409 conflict and steals updates from the real poller | Chat-id diagnostics use only `getChat`/`getChatMember`/`getChatMemberCount` — see §7 for why, and the CI/test guard in §11 |
| T11 | An unpaired DM sender is silently ignored, or a pairing code becomes a spoofing vector if displayed insecurely | Documented DM policy and pairing-code handling (§6) |

## 3. The allowlist model

Three Telegram env vars in `$HERMES_HOME/.env`, read once at gateway
start (§6):

| Variable | Controls | Shape |
|---|---|---|
| `TELEGRAM_ALLOWED_USERS` | Direct-message (DM) access | Comma-separated numeric Telegram user ids |
| `TELEGRAM_ALLOWED_CHATS` | Group/channel access (one half of the pair) | Comma-separated numeric Telegram chat ids (negative for groups/supergroups/channels) |
| `TELEGRAM_GROUP_ALLOWED_CHATS` | Group/channel access (the other half of the pair) | Same shape as `TELEGRAM_ALLOWED_CHATS` |

**Why two variables for groups, and why both are mandatory:** Hermes
reads `TELEGRAM_ALLOWED_CHATS` and `TELEGRAM_GROUP_ALLOWED_CHATS`
independently; a chat id present in only one is "half-enabled" and
behaves inconsistently — this is threat-model row T08, and it is exactly
the class of bug a hand-edited `.env` file is prone to (a typo, a copy-
paste that only updates one line, a merge that drops one but not the
other). `telegram-allowlist.sh add <chat-id>` and `remove <chat-id>`
always touch **both** variables in the same atomic write (§4), and
`telegram-allowlist.sh --check` specifically flags any id present in only
one (§5) — including ids that were added by hand before this tool
existed.

**Numeric ids only, no wildcards, ever.** Per `docs/security.md` §2 and
threat-model row T07, an allowlist entry that means "anyone" is treated as
a defect, not a convenience. `telegram-allowlist.sh` enforces this
structurally: `add`/`remove` validate their argument against
`^-?[0-9]+$` and refuse anything else (a wildcard `*`, an empty string, a
username, or — not incidentally — a bot token, which is never a bare
integer and so can never be written through this tool's write path).

## 4. `telegram-allowlist.sh add` / `remove`

```console
$ modules/hermes-gateway/telegram-allowlist.sh add -1001234567890
[omes] telegram-allowlist: added '-1001234567890' to both TELEGRAM_ALLOWED_CHATS and TELEGRAM_GROUP_ALLOWED_CHATS
[omes] WARN telegram-allowlist: this change is INERT until the gateway restarts - allowlists are read once, at gateway start (see docs/telegram-security.md)
[omes] WARN telegram-allowlist: restart with: systemctl --user restart hermes-gateway   (user-mode gateway)
[omes] WARN telegram-allowlist: or, for a system-mode gateway: sudo systemctl restart hermes-gateway

$ modules/hermes-gateway/telegram-allowlist.sh remove -1001234567890
```

Guarantees:

- **Both variables, every time.** `add`/`remove` never touch only one.
- **Atomic.** Writes go to a temp file in the same directory as `.env`
  (guaranteeing a same-filesystem rename) and only replace `.env` via
  `mv` after the temp file is fully written. A failure creating or
  writing that temp file (e.g. a read-only `$HERMES_HOME`) leaves the
  original `.env` completely untouched — never a partial file. Tested in
  `tests/unit/hermes-gateway-telegram-allowlist.bats` by making the
  directory read-only and asserting the file is byte-for-byte unchanged
  after the failed attempt.
- **Mode and owner preserved.** The rewritten file keeps `.env`'s `0600`
  mode and, best-effort, its owner/group.
- **Every other line untouched.** `TELEGRAM_BOT_TOKEN` and any other key
  in `.env` are copied through byte-for-byte; this tool has no code path
  that writes `TELEGRAM_BOT_TOKEN` at all.
- **Idempotent.** Adding an id already present in both variables, or
  removing one absent from both, is a no-op (logged, not an error).
- **Never restarts the gateway.** See §6.

## 5. `telegram-allowlist.sh --check`

```console
$ modules/hermes-gateway/telegram-allowlist.sh --check
[telegram-allowlist] TELEGRAM_BOT_TOKEN=***REDACTED***
[telegram-allowlist] TELEGRAM_ALLOWED_USERS=111222333
[telegram-allowlist] TELEGRAM_ALLOWED_CHATS=-1001,-2002
[telegram-allowlist] TELEGRAM_GROUP_ALLOWED_CHATS=-1001
[omes] WARN telegram-allowlist: half-enabled (in TELEGRAM_ALLOWED_CHATS only, missing from TELEGRAM_GROUP_ALLOWED_CHATS): -2002
```

- Prints the current value of all three allowlist variables and the
  token's presence/absence — **never the token's value**. When a token is
  set, the line always reads `TELEGRAM_BOT_TOKEN=***REDACTED***`,
  regardless of what the actual value is; when unset,
  `TELEGRAM_BOT_TOKEN=<not set>`. `tests/unit/hermes-gateway-telegram-allowlist.bats`
  asserts a planted token value never appears anywhere in `--check`'s
  output.
- Computes the symmetric difference between `TELEGRAM_ALLOWED_CHATS` and
  `TELEGRAM_GROUP_ALLOWED_CHATS` and flags every id present in only one,
  exiting non-zero when any are found — this is what catches a
  hand-edited half-enabled group, not just ones this tool created.
- `modules/hermes-gateway/module.sh`'s `module_verify` runs this
  automatically (as an advisory, non-fatal doctor-style signal) whenever
  `$HERMES_HOME/.env` contains a `TELEGRAM_BOT_TOKEN` line — so a
  half-enabled group surfaces on every `omes install`/re-verify, not only
  when an operator remembers to run `--check` by hand.

## 6. Restart requirement (allowlists are read once, at start)

**Hermes reads all three allowlist variables once, at gateway start.**
Editing `.env` while the gateway is running changes nothing until it
restarts (threat-model row T09) — the file is saved, but the running
process never re-reads it. `telegram-allowlist.sh` never restarts the
gateway itself (a restart is a deliberate, operator-initiated action, not
something a config-writing tool should do silently), but every `add`/
`remove` call prints the exact command:

```console
systemctl --user restart hermes-gateway     # user-mode gateway (default, issue #12)
sudo systemctl restart hermes-gateway       # system-mode gateway (opt-in, issue #12)
```

**Always verify after restarting**, not just trust that the restart
succeeded: `systemctl --user status hermes-gateway` (or the system
equivalent) plus `modules/hermes-gateway/telegram-allowlist.sh --check`
to confirm the allowlist looks the way you expect, and — per
`docs/hermes-integration.md` part 2 §14 ("green signals can lie") — a
green `systemctl status` alone does not prove the Telegram connection
itself is healthy; check `journalctl --user -u hermes-gateway -f` (or the
system equivalent) for the actual connection outcome.

## 7. Safe chat-id discovery (never `getUpdates`)

**The prohibited operation:** calling the long-polling read endpoint
against a chat/bot that already has a running polling gateway causes a
**409 Conflict** and can steal updates from the real poller — the
gateway's own message stream gets interrupted by your diagnostic call.
This is threat-model row T10 and `docs/security.md` §2. OMES's tooling
never calls it, and never will:
`modules/hermes-gateway/telegram-allowlist.sh` implements `diagnose`
using only the safe read-only methods:

- **`getChat`** — resolves a chat id to its type/title/description.
- **`getChatMemberCount`** — sanity-checks a chat id actually refers to a
  group with members (catches a typo'd id early).
- **`getChatMember`** — checks whether a specific user id is a member of
  a specific chat (`diagnose <chat-id> --member <user-id>`).

None of these can conflict with a running poller; all are safe to call at
any time, including while the gateway is actively running.

`telegram-allowlist.sh health` (issue [#79](https://github.com/ahliweb/omes/issues/79),
used by `omes health agent`/`omes health gateway`'s channel layer — see
[docs/hermes-integration.md §17](hermes-integration.md#17-health-and-readiness-issue-79))
adds two more safe read-only methods, via the same `curl -K` pattern:

- **`getMe`** — confirms the bot token is valid and resolves to a bot.
- **`getWebhookInfo`** — reports `pending_update_count` and webhook
  configuration state; it does **not** deliver or consume any pending
  update (unlike the prohibited long-polling read endpoint below).

Neither can conflict with a running poller either, for the same reason:
they read metadata about the bot/webhook configuration, never the update
queue itself.

**How to actually discover an unknown chat id in the first place**
(before you have anything to `diagnose`), safely:

1. **Add the bot to the group/channel** you want to allow, then send any
   message in it. Hermes's own gateway logs
   (`journalctl --user -u hermes-gateway -f`) will show the incoming
   message's chat id even though it is not yet in the allowlist — Hermes
   logs and rejects unauthorized traffic rather than silently dropping it
   with no trace, which is exactly the observation channel this relies
   on. This is the primary, recommended method — it uses data the
   gateway already produces, with zero additional API calls.
2. **A dedicated "what's my chat ID" bot** (e.g. a userinfo-style bot
   operated by a third party) added temporarily to the group. Treat this
   as a manual, operator-driven step outside OMES's tooling — OMES does
   not automate adding a third-party bot to your groups.
3. Once you have a *candidate* id from either method above, confirm it
   with `telegram-allowlist.sh diagnose <candidate-id>` (`getChat` +
   `getChatMemberCount`) before adding it to the allowlist — this
   confirms the id resolves to the chat you think it does, without ever
   touching the long-polling endpoint.

**Also prohibited on a polling deployment:** `setWebhook`/`deleteWebhook`.
A polling gateway and a webhook are mutually exclusive delivery modes;
calling either against a bot that is actively long-polling is an
unforced configuration error, not a diagnostic action, and OMES tooling
never calls them.

## 8. Token handling: storage, argv, and diagnostics

- **Storage:** `TELEGRAM_BOT_TOKEN` lives in `$HERMES_HOME/.env`, mode
  `0600`, created empty by the `hermes` module (issue #11) for the
  operator to populate — OMES never writes a token value itself (see
  `docs/hermes-integration.md` §5).
- **Never in argv.** `docs/security.md` §5's "no secrets in argv" rule
  applies here specifically: the token is never accepted as a CLI flag,
  never appears in a `ps`/`/proc/<pid>/cmdline`-visible command line.
  `telegram-allowlist.sh diagnose`'s Telegram API calls read the token
  from `.env` in-process and pass it to `curl` via a `curl -K
  <config-file>` (`url = "https://api.telegram.org/bot<token>/<method>?..."`
  inside a temp file created mode `0600` and deleted immediately after
  the call) — the token appears only inside that short-lived,
  permission-locked file, never in `curl`'s own argv and never in any
  line this script logs. `tests/unit/hermes-gateway-telegram-allowlist.bats`
  asserts a planted token value never appears in the (shimmed) `curl`
  invocation log.
- **Never printed.** No command in `telegram-allowlist.sh` — `add`,
  `remove`, `--check`, or `diagnose` — ever writes the token to stdout,
  stderr, or a log line. `--check` explicitly redacts it (§5).
- **Rotation procedure**, whenever a token may have leaked (committed by
  mistake, printed to a shared terminal, a departing operator had access,
  or on a routine schedule):
  1. In Telegram, message **@BotFather** → `/mybots` → select the bot →
     **API Token** → **Revoke current token**. This immediately
     invalidates the old token everywhere, including on this host.
  2. Update `$HERMES_HOME/.env`'s `TELEGRAM_BOT_TOKEN` line to the new
     value (a manual edit with your editor of choice — this is a token
     write, and per §3/§4 above `telegram-allowlist.sh` deliberately has
     no code path that writes tokens).
  3. Restart the gateway (§6's exact commands).
  4. Verify: `systemctl --user status hermes-gateway` (or system
     equivalent) is `active`, then confirm actual connectivity via
     `journalctl --user -u hermes-gateway -f` (or system) and/or a real
     message round-trip in an allowlisted chat — remember §6's "green
     signals can lie" caveat; `is-active` alone does not prove the new
     token authenticated successfully.

## 9. Backup policy: no token in backups, ever

`$HERMES_HOME/.env` (and therefore `TELEGRAM_BOT_TOKEN`) is **never**
registered via `omes_manage_path` by the `hermes` module
(`docs/hermes-integration.md` §5) or by `telegram-allowlist.sh` — which
means it is never captured by `lib/omes/backup.sh`, never written into
`<state-dir>/backups/<timestamp>/`, and never touched by `module_rollback`
or a future `omes restore`/`omes uninstall`. This is a deliberate,
structural guarantee, not a convention an individual command happens to
follow: no code path in the `hermes`/`hermes-gateway` modules or in
`telegram-allowlist.sh` ever calls `omes_manage_path` on `.env`.
`tests/unit/hermes.bats` and `tests/integration/hermes.bats` (issue #11)
assert that no backup session ever contains a file named `.env`.

Practical consequence: **your own backup strategy for `.env` (if any) is
entirely your responsibility**, separate from OMES's backup system —
which is the point: a token belongs in a secrets-aware backup path (a
password manager, an encrypted secret store), not alongside OMES's
plaintext-on-disk (if permission-locked) module state backups.

## 10. DM policy, group/topic isolation, and mention policy

- **`unauthorized_dm_behavior`** (a Hermes config setting, not an OMES
  one) governs what happens when someone not in `TELEGRAM_ALLOWED_USERS`
  DMs the bot: typically either silently ignore, or reply with a pairing
  code the operator can use to approve them. **Document your chosen
  behavior for this deployment** (there is no OMES-enforced default — it
  is upstream Hermes configuration, per `hermes config set`). Threat-model
  row T11 requires that if pairing codes are used, they are delivered
  **out-of-band** — e.g. as a DM to the bot owner — and never echoed back
  on a surface the agent itself can read (an in-chat reply the agent's
  own context includes is not out-of-band).
- **Group/topic isolation:** treat each allowlisted group as its own
  trust boundary. A group being allowlisted does not imply every member
  of that group should be treated as equally trusted for DM purposes —
  `TELEGRAM_ALLOWED_USERS` (DM) and `TELEGRAM_ALLOWED_CHATS`/
  `TELEGRAM_GROUP_ALLOWED_CHATS` (group) are independent allowlists by
  design; do not assume one implies the other. If your Hermes deployment
  uses forum-style topics within a group, apply the same reasoning
  per-topic where Hermes supports it — document which topics are
  in-scope for agent responses.
- **Mention policy:** whether the bot requires an explicit `@mention` or
  reply to act in a group (vs. responding to every message) is a Hermes
  group-behavior setting. Document your chosen policy per deployment —
  "responds to everything unprompted" and "only responds when mentioned"
  have materially different exposure in a large or public-ish group, and
  an operator reading this deployment's docs later should not have to
  reverse-engineer which one is active.

## 11. Prohibited operations (enforced + documented)

| Operation | Why prohibited | Enforcement |
|---|---|---|
| The long-polling read endpoint (see §7) against a running poller | 409 Conflict, steals updates from the real poller (T10) | `telegram-allowlist.sh` never calls it; a test asserts the literal string never appears in any script under `modules/` (see below) |
| `setWebhook` / `deleteWebhook` on a polling deployment | Mutually exclusive delivery modes; not a diagnostic action | Never called by any OMES tooling |
| A bot token in a URL on a command line | `ps`/`/proc/<pid>/cmdline`-visible to any local user (T04, `docs/security.md` §5) | `diagnose` uses `curl -K` with a `0600` temp config file instead (§8) |

`tests/unit/hermes-gateway-telegram-allowlist.bats` includes a guard test
asserting the prohibited endpoint's name never appears in any script
under `modules/`, written so that *this document* (which necessarily
names it in prose) does not trip the guard — the guard scans `modules/`,
not `docs/`.

## 12. Operator checklist

Before considering a Telegram-enabled Hermes deployment production-ready:

- [ ] `TELEGRAM_BOT_TOKEN` is set in `$HERMES_HOME/.env` (mode `0600`),
      obtained from @BotFather, never committed to any repository.
- [ ] `TELEGRAM_ALLOWED_USERS` contains only the numeric user ids that
      should be able to DM the bot — no wildcard, not left empty-means-open.
- [ ] Every group/channel you want the bot active in has its chat id in
      **both** `TELEGRAM_ALLOWED_CHATS` and `TELEGRAM_GROUP_ALLOWED_CHATS`
      — verified with `telegram-allowlist.sh --check` (exit 0, no
      half-enabled warnings).
- [ ] The gateway has been restarted since the last allowlist edit (§6).
- [ ] `unauthorized_dm_behavior` is set deliberately, not left at
      whatever the upstream default happens to be, and documented for
      this deployment (§10).
- [ ] Mention/response policy for groups is documented for this
      deployment (§10).
- [ ] You have a token rotation plan and know the four steps in §8.
- [ ] You understand `.env` is never in an OMES backup (§9) and have your
      own plan if you need one.
- [ ] You understand `systemctl [--user] is-active` does not prove the
      Telegram connection is healthy (`docs/hermes-integration.md` part 2
      §14) and know how to check `journalctl` for the real signal.
