# ADR-0015 — Content distribution workflow architecture

- **Status:** Superseded by ADR-0024 (issue #179)
- **Date:** 2026-09-19 (superseded 2026-09-21)
- **Decision maker:** @ahliweb
- **Related:** ADR-0002 (explicit state), ADR-0003 (module lifecycle), ADR-0004 (check before mutate), ADR-0005 (root/user separation), ADR-0011 (Control Center and provider boundaries), ADR-0017 (upstream-first precedence), ADR-0024 (content workflow boundary and migration), [docs/content-distribution.md](../content-distribution.md), [docs/telegram-security.md](../telegram-security.md)

## Context

Issue #63 asks for an optional Hermes workflow that takes one approved video from a
local inbox and distributes it to multiple publishing platforms (YouTube, TikTok,
Instagram, etc.) through isolated, per-platform specialist workers, with an operator
approval gate in front of every external side effect.

This is squarely content/social-media automation, not host compatibility. It must not
become part of the OMES core installer, must not weaken OMES's least-privilege and
single-authority design (AGENTS.md §2), and must not turn Hermes or OMES into a second
browser-automation runtime with ad-hoc credential handling.

Three shapes were considered.

## Options considered

### A. Hermes-only prompt workflow

Implement the whole flow as a Hermes skill/prompt: Hermes itself watches the inbox,
drafts captions, drives a browser, and posts to Telegram for approval, with no OMES
component at all.

Rejected: Hermes owns agent runtime behavior (reasoning, messaging, sessions, browser
automation per AGENTS.md §2), but it has no built-in job state machine, retry/backoff
policy, audit log, or artifact lifecycle. Encoding all of that in a prompt/skill makes
the safety-critical parts (approval gating, exactly-once publish, secret isolation)
untestable outside a live Hermes session and unauditable in this repository's test
suite.

### B. OMES Python orchestrator + Hermes as an optional caption/plan generator (chosen)

OMES owns the durable, testable parts: inbox detection, the job state machine,
retries, the approval record, the audit log, and archival/reporting — all as stdlib
Python under `lib/omes/py/content/`, exposed through `omes content <subcommand>`
(ADR-0012 language policy). Hermes is treated as *one optional input* to the planning
step (it may draft a caption/plan on request) and *one approval channel* (#65 adds a
Telegram approve/reject front end that calls back into `omes content approve`), never
as the thing that executes side effects or owns job state. Publishing itself happens
through isolated, per-platform worker subprocesses (#66) that OMES's manager invokes
and verifies — never Hermes browser automation running unsupervised.

Chosen because it keeps the safety-critical logic (state transitions, retry bounds,
approval binding, audit trail, secret isolation) in code this repository's own test
suite can exercise deterministically, with Hermes and Telegram as pluggable,
replaceable front ends rather than the source of truth.

### C. External tool (e.g., a dedicated social-media scheduler product)

Adopt or wrap an existing open-source or commercial multi-platform scheduler.

Rejected for the MVP: introduces a new external dependency and trust boundary,
duplicates OMES's own backup/state/audit conventions instead of reusing them, and most
such tools assume centralized API credentials rather than isolated per-platform
browser sessions with cookies treated as host secrets. Revisit only if a specific
platform requires an official API integration that isolated browser workers cannot
satisfy safely.

## Decision

Build an **OMES-owned Python orchestrator** (stdlib only, per ADR-0012) that:

1. Owns the inbox → job → archive lifecycle and the job state machine (#64, #67).
2. Treats **Hermes as an optional caption/plan generator**, never the executor.
3. Treats **Telegram as the (default, but swappable) approval channel** (#65), with
   `omes content approve <job> --actor <id>` as the always-available, no-external-
   dependency MVP approval path (this PR, #63/#64/#67, defines the CLI surface; #65
   adds the Telegram front end on top of the same approval record).
4. Isolates every platform integration in its **own worker subprocess** (#66),
   communicating with the manager over JSON on stdin/stdout, so a single platform's
   selectors/browser-automation bug cannot corrupt another platform's session or the
   manager's own state.
5. Keeps browser profiles/cookies under `content/sessions/`, a `0700` directory that is
   structurally excluded from Git, logs, backups, and reports (see
   docs/content-distribution.md §7).
6. Ships entirely as an **optional extension command** (`lib/omes/cmd/content.sh`,
   ADR pattern from the pluggable-commands feature) that no installer profile
   references — see docs/content-distribution.md §9.

## Consequences

- The manager/orchestrator and the state machine can be fully unit-tested with a fake
  worker (#67's `tests/fixtures/content/fake-worker.py`) and without any real browser,
  Telegram bot, or platform account.
- Adding a new platform means adding one new worker subprocess implementing the
  contract in docs/content-distribution.md §6 — the manager, job schema, and audit log
  do not change.
- Hermes and Telegram both become optional layers on top of a CLI that already works
  without either (`omes content approve ... --actor ...`), so #65 can be delayed,
  replaced, or disabled without blocking the MVP.
- This workflow is never added to `profiles/*.profile` or the default installer path;
  enabling it is always an explicit, separate operator action (`omes content scan`,
  called manually or from an optional systemd timer — see
  docs/content-distribution.md §5).
