# Content distribution workflow

> Status: design (this document) + partial implementation, tracked across issues
> [#63](https://github.com/ahliweb/omes/issues/63) (this epic/design),
> [#64](https://github.com/ahliweb/omes/issues/64) (inbox watcher and artifact
> lifecycle), [#67](https://github.com/ahliweb/omes/issues/67) (job state machine,
> retry, reconciliation), [#68](https://github.com/ahliweb/omes/issues/68)
> (provenance/audit/archive reports), [#66](https://github.com/ahliweb/omes/issues/66)
> (platform worker contract), [#65](https://github.com/ahliweb/omes/issues/65)
> (Telegram approval front end), [#69](https://github.com/ahliweb/omes/issues/69)
> (caption/cover/policy validation), [#70](https://github.com/ahliweb/omes/issues/70)
> (test and threat-model). See [ADR-0015](adr/0015-content-distribution-workflow.md)
> for why this shape was chosen.
>
> **This workflow is optional.** It is never referenced by `profiles/*.profile` or by
> any installer path — see §9. Enabling it is an explicit, separate operator action.

## 1. MVP definition

The minimum viable path this design commits to, end to end:

```
local inbox file → plan (hash + mime + optional caption draft)
                 → operator approval (CLI now, Telegram in #65)
                 → publish to ONE platform via one isolated worker
                 → verify by resulting URL (not by "the click succeeded")
                 → report (JSON + Markdown)
                 → archive (uploaded/ or failed/, evidence preserved)
```

Everything else (multiple concurrent platforms, AI-drafted captions, Telegram
front end, platform-specific validation) is designed for here so #65/#66/#69 can
build on stable interfaces, but is not required for the MVP to be useful: an operator
can drop a file in `inbox/`, run `omes content scan`, `omes content plan` (from #66/#69
scope), `omes content approve <job> --actor <id>`, and `omes content publish` (from
#66), for a single platform, entirely from the CLI, without Hermes or Telegram
involved at all.

## 2. Sequence (MVP path)

```
Operator/cron        omes content CLI          Job state / disk        Platform worker (#66)
     |                       |                          |                        |
     | drop file in inbox/   |                          |                        |
     |---------------------->|                          |                        |
     |  omes content scan    |                          |                        |
     |---------------------->| hash+settle, create job  |                        |
     |                       |------------------------->| state/jobs/<id>.json   |
     |                       |                          |   (queued)             |
     |  omes content plan    |                          |                        |
     |---------------------->| optional Hermes caption  |                        |
     |                       |------------------------->| (planning ->           |
     |                       |                          |  approval-required)    |
     |  omes content approve |                          |                        |
     |  <job> --actor <id>   |                          |                        |
     |---------------------->| record approval record   |                        |
     |                       |------------------------->| (approved)             |
     |  omes content publish |                          |                        |
     |---------------------->| spawn worker subprocess  |                        |
     |                       |------------------------------------------------->| prepare
     |                       |                          | (publishing)           | publish
     |                       |<-------------------------------------------------| JSON result
     |                       | classify: url -> verifying; none/timeout ->       |
     |                       |   manual-review; confirmed error -> retryable/failed
     |                       |------------------------------------------------->| verify
     |                       |<-------------------------------------------------| JSON result
     |                       | (succeeded | manual-review | retryable-failure)   |
     |  omes content report  |                          |                        |
     |---------------------->| write reports/<id>/*     |                        |
     |                       |------------------------->| archive to uploaded/   |
     |                       |                          |   or failed/<id>/      |
```

ASCII form of the terminal fan-out:

```
queued -> planning -> approval-required -> approved -> publishing -> verifying -> succeeded -> archived
                                                              \            \
                                                               \            -> manual-review -> archived (review/)
                                                                -> retryable-failure -> (retry) -> publishing
                                                                                      -> failed -> archived (failed/)
              (any non-terminal state) -> cancelled -> archived
```

## 3. Directory layout

Root: `OMES_CONTENT_ROOT`, default `${XDG_DATA_HOME:-$HOME/.local/share}/omes/content`
(user-scope only — this workflow never runs as root; see §9).

```
content/
  inbox/        # operator/cron drops new media here
  processing/<job-id>/source.<ext>   # moved here once a job is created
  uploaded/<job-id>/                 # archived evidence for succeeded jobs
  failed/<job-id>/                   # archived evidence for failed jobs
  review/<job-id>/                   # archived evidence for manual-review/cancelled jobs
  reports/<job-id>/report.json, report.md, report-<n>.json  # never overwritten
  sessions/      # mode 0700; browser profiles/cookies; NEVER read by reports/backups/logs
  state/
    jobs/<job-id>.json    # one job record per job (see §4)
    audit.jsonl           # append-only, hash-chained audit log (see §7, #68)
    scan.lock             # lock file preventing concurrent scans (#64)
```

Rules that hold structurally, not just by convention:

- `inbox/`, `processing/`, `uploaded/`, `failed/`, `review/` hold only media and the
  files a worker/report explicitly writes into a job's own subdirectory.
- `reports/` and `state/` never contain raw media or session secrets.
- `sessions/` is created at `0700` and is the **only** directory that holds
  platform login/cookie state. `lib/omes/py/content/reports.py` and
  `lib/omes/py/content/paths.py` do not expose a path-walking function that can
  reach `sessions/` from report/export/backup code (see §7 and the `content/64`
  test suite's `test_sessions_dir_excluded_from_*` cases).
- A rescan/scan never follows symlinks (`os.path.islink` check before hashing —
  #64) and ignores `reports/`, `processing/`, `state/`, `sessions/`, and dotfiles
  when walking `inbox/`.

## 4. Job record schema (versioned JSON)

`state/jobs/<job-id>.json`, one file per job, written atomically (temp file +
`os.replace`). `job-id` = first 12 hex chars of the artifact's sha256 + `-` +
UTC timestamp (`YYYYmmddTHHMMSSZ`), so IDs are stable, sortable, and collision-safe
even if two different files share a truncated hash prefix.

```json
{
  "schema_version": 1,
  "job_id": "3f2a9c1b7e4d-20260919T101500Z",
  "state": "queued",
  "source": {
    "original_path": "inbox/clip.mp4",
    "processing_path": "processing/3f2a9c1b7e4d-20260919T101500Z/source.mp4",
    "sha256": "3f2a9c1b7e4d...",
    "size_bytes": 10485760,
    "mime_guess": "video/mp4",
    "duplicate_of": null
  },
  "platform": null,
  "plan": {
    "caption": null,
    "caption_source": null,
    "targets": []
  },
  "approvals": [],
  "publish": {
    "worker_version": null,
    "attempts": 0,
    "max_attempts": 5,
    "last_result": null,
    "resulting_url": null
  },
  "created_at": "2026-09-19T10:15:00Z",
  "updated_at": "2026-09-19T10:15:00Z",
  "history": [
    {"ts": "2026-09-19T10:15:00Z", "from": null, "to": "queued", "actor": "system", "note": "scan"}
  ]
}
```

`schema_version` is bumped, never silently reinterpreted, whenever a field is added
or its meaning changes; `lib/omes/py/content/jobs.py` refuses to load a job record
with an unknown `schema_version`.

## 5. State machine

States: `queued`, `planning`, `approval-required`, `approved`, `publishing`,
`verifying`, `succeeded`, `retryable-failure`, `manual-review`, `failed`,
`cancelled`, `archived`.

Valid transitions (`lib/omes/py/content/jobs.py::TRANSITIONS`, #67):

| From | To | Trigger |
|---|---|---|
| `queued` | `planning` | `omes content plan` |
| `planning` | `approval-required` | plan produced |
| `approval-required` | `approved` | `omes content approve` |
| `approval-required` | `cancelled` | `omes content cancel` |
| `approved` | `publishing` | `omes content publish` starts worker |
| `publishing` | `verifying` | worker returned a candidate URL |
| `publishing` | `manual-review` | worker returned no URL / timeout (uncertain) |
| `publishing` | `retryable-failure` | worker returned a confirmed, retryable error |
| `publishing` | `failed` | worker returned a confirmed, non-retryable error, or retries exhausted |
| `verifying` | `succeeded` | verify confirmed the URL is live/matches |
| `verifying` | `manual-review` | verify inconclusive |
| `verifying` | `retryable-failure` | verify confirmed a transient error |
| `retryable-failure` | `publishing` | `omes content retry` (bounded backoff, §5.2) |
| `retryable-failure` | `failed` | retries exhausted |
| any non-terminal state | `cancelled` | `omes content cancel --yes` |
| `succeeded` \| `failed` \| `manual-review` \| `cancelled` | `archived` | archive step (#68) |

Terminal states: `succeeded`, `failed`, `cancelled`, `archived` (after archival,
`archived` further tags which outcome via the job record's `state` history, not a
new mutable state). `manual-review` is terminal for automation purposes but not for
the operator — it can only leave via an explicit operator action recorded with an
actor (`omes content retry`/`cancel`), never automatically.

Invalid transitions raise `InvalidTransitionError` and are rejected outright (#67
test: every non-listed `(from, to)` pair is asserted to raise).

### 5.1 Uncertain vs. confirmed failure

A worker's publish/verify JSON result includes a `status` of `ok`, `uncertain`, or
`error` (§6). The manager classifies:

- `ok` with a `url` → `verifying` (publish) or `succeeded` (verify).
- `uncertain` (no URL yet, ambiguous UI state, or a timeout) → `manual-review`.
  Uncertain results are never auto-retried, because a retry after an uncertain
  publish risks a duplicate post — an operator must inspect and decide
  (`omes content retry <job> --actor <id> --yes` after confirming manually).
- `error` with `retryable: true` → `retryable-failure` (bounded auto-retry, §5.2).
- `error` with `retryable: false` → `failed`.

### 5.2 Retry / backoff

Bounded exponential backoff: `delay = min(base_seconds * 2**attempt, max_seconds)`,
`base_seconds` and `max_seconds` and `max_attempts` are per-platform config (default
`base_seconds=30`, `max_seconds=1800`, `max_attempts=5`). `publish.attempts` in the
job record tracks the count; reaching `max_attempts` forces `failed` instead of
another `retryable-failure` cycle. `omes content retry <job>` refuses to run before
the computed backoff delay has elapsed unless `--force` is also passed (still
requires `--actor`).

### 5.3 Resume after restart

`omes content resume` scans `state/jobs/*.json` for jobs left in `publishing` or
`verifying` (e.g., the process was killed mid-flight) and **re-runs verify only** —
it never re-invokes a worker's `publish` operation without an operator-confirmed
`retryable-failure` and an explicit `omes content retry`. This is the load-bearing
rule against duplicate publication: a crash between "worker said it published" and
"manager recorded success" must not cause a second post.

## 6. Worker interface contract (implemented in #66)

Each platform is one specialist worker: a subprocess the manager invokes per
operation, with JSON on stdin and one JSON object on stdout (stderr is free-form
human log). The manager never talks to a platform directly and never holds
platform-specific selectors/logic itself.

Operations (one process invocation per operation call in the MVP; a worker may keep
warm state in its own profile directory between calls, but the manager does not
depend on process reuse):

| Operation | stdin JSON | stdout JSON (on success) |
|---|---|---|
| `prepare` | `{"job_id","source_path","session_dir","platform"}` | `{"status":"ok","ready":true}` |
| `publish` | `{"job_id","source_path","caption","targets","session_dir"}` | `{"status":"ok"\|"uncertain"\|"error","url":str\|null,"screenshot_ref":str\|null,"retryable":bool,"note":str}` |
| `verify` | `{"job_id","url","session_dir"}` | `{"status":"ok"\|"uncertain"\|"error","url":str,"retryable":bool,"note":str}` |
| `collect-evidence` | `{"job_id","session_dir"}` | `{"status":"ok","evidence_paths":[...]}` (paths are non-secret evidence only — never the session profile itself) |
| `revoke-session` | `{"job_id","session_dir","platform"}` | `{"status":"ok"}` |

Every worker invocation is called as
`python3 <worker-executable> <operation>` with the JSON body on stdin, and must:

- Exit `0` and print exactly one JSON object on stdout for every documented
  outcome (`ok`/`uncertain`/`error` are all exit `0` — they are business outcomes,
  not process failures). A non-JSON stdout or non-zero exit is treated by the
  manager as `uncertain` (never silently as success).
- Never write outside `session_dir` (its own isolated profile) and the paths it was
  given for source/evidence.
- Never print or return session cookies, tokens, or credentials in its JSON output
  (the manager rejects/redacts a result whose fields match `TOKEN|KEY|SECRET|
  PASSWORD|COOKIE`, mirroring the audit-log redaction in #68).

`tests/fixtures/content/fake-worker.py` (#67) implements this exact contract with a
controllable outcome (via env vars or a control file) so the manager, state machine,
and resume logic can be tested without any real browser or platform account.

## 7. Approval contract

An **approval record** is appended to a job's `approvals` array, never mutated in
place:

```json
{
  "actor": "operator-id",
  "decision": "approved",
  "artifact_hash": "3f2a9c1b7e4d...",
  "approved_at": "2026-09-19T10:20:00Z",
  "expires_at": "2026-09-19T11:20:00Z",
  "channel": "cli"
}
```

- **Immutable artifact hash binding.** The approval records the exact
  `source.sha256` of the job at approval time. If the job's artifact hash ever
  differs from the most recent approval's `artifact_hash` (should never happen for
  an already-hashed, immutable `processing/` copy, but is checked defensively before
  every `publish`), the approval is rejected and the job is forced to
  `manual-review`.
- **Staleness expiry.** `expires_at` defaults to `approved_at + 1h` (config:
  `OMES_CONTENT_APPROVAL_TTL_SECONDS`, default `3600`). `omes content publish`
  refuses to proceed on an expired approval and reports the job as needing
  re-approval — it does not auto-renew.
- **MVP approval path (this design + #64/#67):** `omes content approve <job-id>
  --actor <id>` — no Telegram, no Hermes dependency. `omes content reject <job-id>
  --actor <id>` records a `"decision": "rejected"` record and moves the job to
  `cancelled`.
- **#65 adds Telegram** as a second channel writing the *same* approval record shape
  (`"channel": "telegram"`), reusing `docs/telegram-security.md`'s allowlist model
  for who is allowed to approve, and must bind to the same artifact hash and TTL
  rules — it does not get a separate, looser approval schema.

## 8. Security boundaries

- **Browser profiles/cookies are secrets.** `content/sessions/` is created at mode
  `0700` (owner-only) and is the only place they live. `paths.py` provides
  `sessions_dir()` but no function in `reports.py`, `jobs.py`'s audit writer, or any
  export/backup path walks into it; this is asserted directly in tests (#64, #68).
- **Never in Git.** `.gitignore` excludes `content/` entirely (the whole runtime
  tree lives under `OMES_CONTENT_ROOT`, outside the repository, by construction —
  the same reason `$HERMES_HOME` never appears in the repo).
- **Never in logs.** CLI output (`--json` or human) never includes a raw session
  path's contents, only its existence; `omes_redact`-style redaction (pattern
  `TOKEN|KEY|SECRET|PASSWORD|COOKIE`, case-insensitive) is applied to every audit
  line and report field before it is written (#68).
- **Never in backups.** `lib/omes/backup.sh` is never pointed at
  `OMES_CONTENT_ROOT`; this workflow keeps its own append-only audit log instead of
  relying on the host backup/restore path, and that audit log itself excludes
  `sessions/`.
- **Never in messages.** The Telegram approval flow (#65) sends only caption text,
  target platform names, and a preview reference it is explicitly given — never a
  file path under `sessions/`, never a cookie value, never a raw worker stdout blob
  without going through the same redaction pass.
- **Approval-gated by default.** `omes content publish` refuses to run without a
  current, non-expired, hash-matching `approved` decision in `approvals`. There is
  no flag that publishes without an approval record in this design; a future
  fully-automatic mode (if ever built) would need its own ADR and an explicit,
  loud opt-in — it is out of scope here.
- **Least privilege / isolation.** Each platform worker gets its own subdirectory
  under `sessions/<platform>/` and its own subprocess; one worker's crash or bug
  cannot read another platform's cookies (separate directories, separate
  processes, no shared in-process state).

## 9. Not in the default installer path

`omes content` is a `lib/omes/cmd/content.sh` extension command (§4.12 of
`docs/cli.md`) exactly like the pluggable-commands mechanism already merged for
other extensions. It is:

- **Not** referenced by `profiles/server.profile`, `profiles/desktop.profile`, or
  `profiles/hermes.profile`.
- **Not** installed, enabled, or run by `bin/omes install`, `bin/omes check`, or
  `bin/omes doctor` for any profile.
- **Not** started automatically by any systemd unit OMES itself creates; the
  optional systemd *timer* template documented in #64 (`docs/cli.md`'s content
  section) is a copy-paste example an operator installs by hand if they want
  scheduled scanning — OMES does not install or enable it.

An operator opts in by running `omes content scan` (or the optional timer) after
reading this document, exactly like `omes install --module <name>` requires an
explicit module name rather than being implied by a profile.

## 10. Child issue dependency order

```
#63 (this design) -> #64 (inbox + lifecycle) -> #67 (state machine + retry)
   -> #68 (provenance/audit/reports) -> #66 (worker contract, real platform)
   -> #65 (Telegram approval front end) -> #69 (caption/cover/policy validation)
   -> #70 (test + threat-model pass)
```

Rationale: #64/#67/#68 build the durable core (inbox, state machine, audit/reports)
entirely testable with a fake worker, before any real browser automation (#66) or
external channel (#65, Telegram) is introduced — so the safety-critical logic is
proven first, in isolation, with fixtures only. #69 (content validation) depends on
having a real worker/platform target to validate against, and #70 is the
cross-cutting security/test pass that exercises the whole stack once all pieces
exist.

## 11. How to add a platform worker safely

1. Read §6 (worker interface contract). Do not change the manager, job schema, or
   audit log to accommodate a platform quirk — the contract is platform-agnostic by
   design.
2. Create `lib/omes/py/content/workers/<platform>/worker.py` implementing
   `prepare`/`bootstrap-session`/`publish`/`verify`/`collect-evidence`/
   `revoke-session` per §6 and §13, using its own isolated session directory under
   `content/sessions/<platform>/`. `workers/registry.py` finds it automatically by
   this directory convention — nothing else needs to know a new platform exists.
3. Add the platform to per-platform config (retry limits, backoff base/max —
   §5.2) without touching the generic retry engine in `jobs.py`.
4. Write a contract test using `tests/fixtures/content/fake-worker.py`'s pattern:
   assert your worker's JSON shape matches §6 exactly, including redaction of any
   secret-shaped field.
5. Never let two platforms share a session directory, a browser profile, or a
   subprocess.
6. Ship it disabled by default; an operator adds the platform to `plan.targets`
   explicitly per job (or per default config), never automatically for every job.

## 12. Command surface (cumulative across #64/#67/#68/#66/#65)

See `docs/cli.md`'s content section for the authoritative, implementation-tracked
list. Summary of the subcommands this design defines interfaces for:

| Subcommand | Added in | Purpose |
|---|---|---|
| `omes content scan [--json] [--settle-seconds N]` | #64 | detect new inbox files, create jobs |
| `omes content rescan [--json]` | #64 | re-hash `processing/`/`failed/` for consistency |
| `omes content list [--state X] [--json]` | #64 | list jobs |
| `omes content resume [--json]` | #67 | re-verify jobs stuck in `publishing`/`verifying` |
| `omes content reconcile [--json]` | #67 | list jobs needing operator review, with reasons |
| `omes content retry <job> [--actor ID] [--yes]` | #67 | retry a `retryable-failure`/`manual-review` job |
| `omes content cancel <job> [--actor ID] [--yes]` | #67 | cancel a non-terminal job |
| `omes content approve <job> --actor ID [--yes]` | #64/#63 (MVP), Telegram in #65 | record an approval decision |
| `omes content report <job> [--md\|--json]` | #68 | print/generate a job's report |
| `omes content export --since DATE --out DIR` | #68 | redacted export of reports/audit |
| `omes content prune --older-than DAYS [--dry-run] [--yes]` | #68 | delete archived media/reports (never `sessions/`/audit) |
| `omes content plan <job> [--actor ID] [--caption TEXT] [--target PLATFORM]...` | #66 | record targets/caption, `queued` → `approval-required` |
| `omes content publish <job> --platform NAME [--worker-executable PATH]` | #66 | run the platform worker's `prepare`/`publish`/`verify` |
| `omes content session login <platform> --actor ID` | #66 | run `bootstrap-session` (manual login only; never publishes) |
| `omes content session revoke <platform> --actor ID [--yes]` | #66 | run `revoke-session` and clear the local profile directory |

## 13. Worker isolation implementation (#66)

This section documents the concrete implementation of §6/§8 for the
`generic_browser` worker, the one platform skeleton shipped in this repository.

- **Path allowlist, enforced, not just documented.** Every request the manager
  sends a worker includes `allowed_paths` (`processing_dir`, `session_dir`,
  `evidence_dir` — each an absolute path under this job's/platform's own
  directory). `lib/omes/py/content/workers/base.py::enforce_path_boundary()`
  resolves (following symlinks) every path a worker touches and rejects
  (`PathBoundaryError` → a typed `nonretryable` failure) anything outside those
  roots. This is checked in the worker process itself, not only trusted to
  worker authors — see `tests/py/content/test_workers_base.py` and
  `test_workers_generic_browser.py::test_publish_rejects_source_path_outside_allowed_roots`.
- **Typed failure states.** A worker result's `status`/`retryable` fields (§6)
  are unchanged for backward compatibility with #67's `classify_worker_result`,
  but every failure additionally carries `failure_state`, one of `retryable`,
  `nonretryable`, `uncertain`, or `needs_login` (`workers/base.py::typed_failure`).
  `needs_login` maps to `status: "uncertain"` (never auto-retried — the
  manager moves the job to `manual-review`, matching #66's acceptance criterion).
- **Session directory, created by the worker.** `content/sessions/<platform>/` is
  created (mode `0700`) by the worker's own `prepare`/`bootstrap-session`
  operation (`workers/base.py::ensure_session_dir`), not by the manager, so a
  platform that is never prepared never gets a session directory.
- **Manual login separated from publishing.** `omes content session login
  <platform>` prints an explicit warning that this step only logs in and never
  publishes, then invokes the worker's `bootstrap-session` operation. The
  `generic_browser` worker's default driver (`manual_stub`) records only a
  local marker file — it never launches a browser or stores real cookies. An
  operator-installed real driver (Playwright/Chromium, or a path into Hermes's
  own browser automation) would instead open a real browser window here for the
  operator to log in by hand, and the resulting real cookies would be the only
  thing ever written under `session_dir`.
- **`OMES_CONTENT_BROWSER_DRIVER`.** If set, its value is a filesystem path to an
  operator-installed Python driver module implementing `bootstrap(payload, ctx)`,
  `publish(payload, ctx)`, `verify(payload, ctx)`; the `generic_browser` worker
  loads it dynamically. If unset, the built-in `drivers/manual_stub.py` is used.
  **OMES never bundles, vendors, or depends on an actual browser** — Playwright,
  Selenium, or Hermes's own browser-automation capability are the intended
  production drivers, installed and referenced by the operator, outside this
  repository's dependency surface (ADR-0012, stdlib-only).
- **Evidence is a path reference, never inline bytes.** A successful `publish`
  result's `screenshot_ref` is a path under `reports/<job>/evidence/`
  (`OMES_CONTENT_ROOT`-relative — see `paths.job_evidence_dir()`), never a
  base64/inline blob. `collect-evidence` lists only files under that job's
  `evidence_dir` and never touches `sessions/`.
- **Retry re-invokes the same worker.** `omes content retry <job> --actor ID`
  (#67) transitions `retryable-failure`/`manual-review` back to `publishing`;
  a subsequent `omes content publish <job> --platform <p>` call is what actually
  re-invokes the worker (`jobs.publish_job` accepts a job already in
  `publishing`, so a retry does not need its own separate transition).
