# Content distribution workflow — threat model

> Status: describes the historical design from issue [#70](https://github.com/ahliweb/omes/issues/70)
> as updated by [ADR-0024](adr/0024-content-workflow-boundary-and-migration.md) (issue [#179](https://github.com/ahliweb/omes/issues/179)).
> Content publishing domain ownership transitions to AWCMS Control Center and Hermes Agent.
> Cross-references [docs/content-distribution.md](content-distribution.md) (design),
> [ADR-0015](adr/0015-content-distribution-workflow.md), [ADR-0024](adr/0024-content-workflow-boundary-and-migration.md),
> [docs/threat-model.md](threat-model.md) row T40, [docs/security.md](security.md) §1, and [docs/telegram-security.md](telegram-security.md).
>
> This workflow is optional and frozen. Browser session profiles and tokens on the host
> are deprecated and scheduled for retirement in OMES v2.0. Export tooling (`omes content export --format awcms-v1`)
> strictly excludes sessions and cookies.

## 1. Scope

Covers `lib/omes/py/content/**` (the manager/orchestrator, job state machine,
approval records, audit log, reports, Telegram front end, platform worker
contract, and validation), `lib/omes/cmd/content.sh`, and
`skills/content/SKILL.md` (the Hermes-side command mapping this workflow
depends on but does not execute). Out of scope: the correctness of an
operator-installed real browser driver (Playwright/Chromium/Hermes browser
automation) referenced via `OMES_CONTENT_BROWSER_DRIVER` — OMES never bundles
or vendors one, and cannot audit code it does not ship (docs/content-
distribution.md section 13).

## 2. Assets

Reuses `docs/threat-model.md` section 4's asset list plus:

| ID | Asset | Why it matters |
|----|-------|-----------------|
| A13 | `content/sessions/<platform>/` browser session cookies/login state | A live logged-in platform session; theft lets an attacker publish or act as the operator's account on that platform |
| CA1 | The immutable artifact (`processing/<job-id>/source.<ext>`) and its sha256 hash | The approval record's entire trust anchor — approval means "publish exactly this artifact," not "publish whatever is at this path later" |
| CA2 | The append-only, hash-chained audit log (`state/audit.jsonl`) | The only record of who approved/rejected/retried/cancelled what, and when; the load-bearing evidence for "the operator did this, not an attacker" |
| CA3 | The Telegram bot token (shared with A2, `docs/threat-model.md`) | Used by `lib/omes/py/content/telegram.py` to send previews/status; theft lets an attacker send messages as the bot (though not itself approve anything — approval is gated by `OMES_CONTENT_APPROVERS` ∩ `TELEGRAM_ALLOWED_USERS`, not by bot-token possession) |

## 3. STRIDE threat table

Likelihood/impact scale matches `docs/threat-model.md`: H = High, M =
Medium, L = Low. "OMES control" values: `implemented-in-OMES` (enforced by
named code in this repository today), `operator-responsibility` (documented,
cannot be enforced here — usually because it depends on an operator-installed
browser driver or the target platform's own behavior), `out-of-scope`.

| ID | Threat | STRIDE | Asset(s) | Likelihood | Impact | Mitigation | OMES control | Test coverage |
|----|--------|--------|----------|:-:|:-:|-------------|---------------|----|
| CT01 | A platform worker's browser session cookies (`content/sessions/<platform>/`) are stolen via a report, export, backup, or log path | Information Disclosure | A13 | L | H | `paths.sessions_dir()`/`session_platform_dir()` are never called by `reports.py`, `export_reports()`, `prune()`, or the audit writer; the session directory is created mode `0700` only by a worker's own `prepare`/`bootstrap-session` op | implemented-in-OMES | `tests/py/content/test_cli_worker_flow.py::test_sessions_never_appear_in_report_export_or_audit`, `test_session_dir_is_mode_0700_after_login`; `tests/integration/content_worker.bats` |
| CT02 | A worker's JSON result (or an approval/telegram/audit code path) accidentally includes a secret-shaped field (a stray cookie/token in a worker's stdout) | Information Disclosure | A13, CA3 | L | H | `workers/base.py::redact()`/`content/worker.py::_redact_result()` and `reports.py::redact_structure()` strip any key matching `TOKEN\|KEY\|SECRET\|PASSWORD\|COOKIE` before it ever reaches the job record, report, or audit line | implemented-in-OMES | `tests/py/content/test_workers_base.py::TestRedaction`, `test_workers_generic_browser.py::test_worker_result_never_contains_a_planted_secret_key`, `test_orchestrator_e2e.py::test_canary_secret_never_appears_anywhere` (#70) |
| CT03 | Telegram bot token leaks via argv (`ps`), a printed/logged URL, or the audit log | Information Disclosure | CA3 | L | H | `telegram.py::_api_call()` places the token only inside a mode-`0600` temporary `curl -K` config file, created immediately before the call and deleted immediately after — never argv, never a printed/logged URL, mirroring `modules/hermes-gateway/telegram-allowlist.sh` exactly | implemented-in-OMES | `tests/py/content/test_telegram.py::TestOutboundSend` |
| CT04 | An unauthorized actor sends `omes content approve` (directly, or by compromising a Hermes skill mapping) and it is accepted | Spoofing | CA1, CA2 | M | H | `--channel telegram` requires the numeric actor id to be in the intersection of `OMES_CONTENT_APPROVERS` and Hermes's own `TELEGRAM_ALLOWED_USERS`; an id in only one is never authorized (fail closed); the always-available `--channel cli` path requires local shell access to the host in the first place | implemented-in-OMES | `tests/py/content/test_telegram.py::TestApproverAuthorization`, `tests/integration/content_telegram.bats` |
| CT05 | An approval granted long ago (or for a job whose content later changed) is reused to publish something the operator never actually reviewed | Tampering, Repudiation | CA1 | M | H | Every approval record has `expires_at` (`OMES_CONTENT_APPROVAL_TTL_SECONDS`, default 3600s); `jobs.is_approval_valid()` is checked immediately before `publish_job` runs and rejects an expired approval, moving the job to `manual-review` rather than proceeding | implemented-in-OMES | `tests/py/content/test_state_machine.py::test_expired_approval_is_invalid`, `test_telegram.py::TestHashMismatchAndStaleness`, `test_orchestrator_e2e.py::test_stale_approval_blocks_publish` (#70) |
| CT06 | The job's artifact is replaced or corrupted between when it was approved and when it is actually published (e.g., a `processing/` file is overwritten, or a duplicate-hash bug lets the wrong file publish) | Tampering | CA1 | L | H | The approval record binds the exact `source.sha256` at approval time; `is_approval_valid()` re-checks it against the job's *current* `source.sha256` immediately before every publish and rejects a mismatch, forcing `manual-review`; `omes content approve --expected-hash` gives the approving actor the same check at approval time | implemented-in-OMES | `tests/py/content/test_state_machine.py` (approval hash binding), `test_telegram.py::test_hash_mismatch_rejected`, `test_orchestrator_e2e.py::test_artifact_tampering_between_approval_and_publish_is_blocked` (#70) |
| CT07 | A crash/restart between "the worker said it published" and "the manager recorded success" causes a second, duplicate publish on resume | Tampering (duplicate side effect) | A13, external platform state | M | H | `omes content resume` re-runs **verify only** for jobs found in `verifying`; a job found in `publishing` (outcome unknown) is moved to `manual-review`, never re-published automatically — this is the load-bearing rule against duplicate publication (docs/content-distribution.md section 5.3) | implemented-in-OMES | `tests/py/content/test_state_machine.py::TestPublishVerifyWithFakeWorker` (`test_resume_never_republishes_only_reverifies`, `test_publishing_survives_crash_as_manual_review_not_a_second_publish`), `test_orchestrator_e2e.py::test_restart_resume_never_republishes` (#70) |
| CT08 | The same source file is dropped into `inbox/` twice (accidentally or by a retry script) and gets published twice as two separate jobs | Tampering (duplicate side effect) | external platform state | M | M | `scan()` hashes every settled file and records a match against an existing job as `duplicate_of` instead of creating a new active job | implemented-in-OMES | `tests/py/content/test_inbox.py` (duplicate detection), `test_orchestrator_e2e.py::test_duplicate_inbox_file_is_not_republished` (#70) |
| CT09 | Publishing to one target platform fails, and that failure is incorrectly treated as blocking or corrupting a different target platform's publish for other content | Denial of Service (partial) | external platform state | L | M | Each `omes content publish <job> --platform <p>` call is independently scoped: its own worker subprocess, its own `allowed_paths` (own `session_dir`/`evidence_dir`), its own state transition — one platform's `retryable-failure`/`failed` outcome never touches another job or another platform's session/evidence | implemented-in-OMES | `tests/py/content/test_orchestrator_e2e.py::test_partial_platform_failure_does_not_affect_other_platform` (#70) |
| CT10 | A worker subprocess hangs (a real browser driver stuck waiting on a UI element) and never returns | Denial of Service | — | M | M | `worker.run_worker()` enforces a subprocess timeout (`DEFAULT_TIMEOUT_SECONDS`); a `subprocess.TimeoutExpired` is downgraded to `{"status": "uncertain", ...}`, never treated as success, and routes the job to `manual-review` rather than blocking the manager process indefinitely | implemented-in-OMES | `tests/py/content/test_orchestrator_e2e.py::test_worker_timeout_is_uncertain_not_success` (#70) |
| CT11 | A platform enforces a rate limit (HTTP 429/equivalent) that a naive retry loop hammers repeatedly, worsening the situation or getting the account further throttled/banned | Denial of Service | external platform state, A13 | M | M | A worker reports a rate-limit condition as a **retryable** typed error; `jobs.compute_backoff_seconds()` enforces bounded exponential backoff (`base_seconds * 2**attempt`, capped, default base 30s/max 1800s/5 attempts) and `omes content retry` refuses to run before that delay has elapsed unless `--force` is explicitly given | implemented-in-OMES | `tests/py/content/test_state_machine.py::TestRetryAndCancelAuth` (`test_retry_too_soon_without_force`, `test_retry_exhausted_after_max_attempts`), `test_orchestrator_e2e.py::test_rate_limit_backoff_is_enforced_on_retry` (#70) |
| CT12 | An uncertain/ambiguous worker outcome (unclear UI state, no confirmed URL) is auto-retried and causes a real duplicate post on the platform | Tampering (duplicate side effect) | external platform state | M | H | `classify_worker_result()` routes any `status: "uncertain"` result (including `needs_login`) to `manual-review`, never to `retryable-failure` — an uncertain outcome is never auto-retried; only an operator's explicit `omes content retry --actor <id>` (after manual inspection) can move it forward | implemented-in-OMES | `tests/py/content/test_state_machine.py::TestClassification::test_uncertain_goes_to_manual_review`, `test_orchestrator_e2e.py::test_manual_review_is_never_auto_retried` (#70) |
| CT13 | A worker's or the manager's own filesystem access escapes its intended job/platform boundary (writes into another job's `processing/`, another platform's `sessions/`, or outside `content/` entirely) | Tampering, Elevation of Privilege | A13, CA1 | L | H | `workers/base.py::enforce_path_boundary()` resolves (following symlinks) every path a worker touches against the explicit `allowed_paths` the manager sent it and rejects anything outside those roots as a typed `nonretryable` failure | implemented-in-OMES | `tests/py/content/test_workers_base.py::TestPathBoundary`, `test_workers_generic_browser.py::test_publish_rejects_source_path_outside_allowed_roots` |
| CT14 | The audit log itself is rewritten, reordered, or has lines deleted to hide what actually happened | Repudiation, Tampering | CA2 | L | M | Append-only, hash-chained log (`prev_hash`/`line_hash` per line, `reports.py::audit_verify()`); any rewrite/reorder/deletion breaks the chain at the first affected line, which `audit_verify()` detects | implemented-in-OMES | (existing #68 coverage: audit hash-chain tests) |
| CT15 | A published video, once live, violates a platform's terms of service (ToS) or exceeds an undocumented rate limit that OMES cannot observe from the host side | Tampering (external), reputational | external platform state | M | M | Out of scope for automated enforcement — OMES documents the operator's responsibility (section 5 below) rather than claiming to detect ToS violations; #69's validation profiles flag unsupported factual claims and missing disclosures as a partial, best-effort mitigation, never a compliance guarantee | operator-responsibility | `tests/py/content/test_validation.py` (claim/disclosure detection, not ToS compliance) |

## 4. Real-platform testing policy

Every test referenced above runs against **synthetic fixtures only**:
`tests/fixtures/content/fake-worker.py` (#67), the `generic_browser` worker's
default `manual_stub` driver (#66, no real browser, no network), and a stdlib
`http.server`-based fake Telegram server (#65). CI never talks to a real
platform, a real Telegram bot, or a real browser.

A real-platform integration test is opt-in only: gated behind
`OMES_TEST_REAL_PLATFORM=1` (unset/`0` by default) and skipped otherwise. No
such test exists in this repository yet — the gate is documented here and
enforced by `tests/py/content/test_orchestrator_e2e.py`'s
`unittest.skipUnless` guard so that if/when one is added, it inherits the
same default-skip behavior rather than requiring every future contributor to
remember the rule independently.

**Not added to `scripts/test-matrix.sh`.** That matrix is purpose-built for
root/apt-scope, per-OS-image installer scenarios (`fresh`/`rerun`/`offline`/
`partial-failure`/`reboot`/`rollback`/`dr`), each requiring a fresh Docker
container per target image. The content distribution workflow is entirely
user-scope, OS-independent Python with no `apt`/root/package dependency, and
its own disaster-recovery-shaped scenarios (restart/resume, duplicate
detection, artifact tampering) are already covered end-to-end, in-process, and
far faster, by `tests/py/content/test_orchestrator_e2e.py` and
`tests/integration/content*.bats`. Bolting a `content-dr` scenario onto the
per-image container matrix would duplicate that coverage without exercising
anything OS-image-specific — it is not "trivially safe" in the sense of adding
real value for the added complexity and CI time, so it is documented here
instead, per this issue's own instruction to prefer documentation when a
matrix scenario is not a trivially safe addition.

## 5. Operator responsibilities and platform terms-of-service notes

OMES is a **manager and worker-isolation framework**, not a compliance
service. The operator remains responsible for:

- **Reading and complying with each target platform's terms of service**,
  including automation/bot policies. Several platforms restrict or prohibit
  browser-automation posting outright, or require official API use instead —
  `docs/content-distribution.md` section 13 already states OMES never bundles
  a browser; the operator's choice of driver and account is also the
  operator's compliance responsibility.
- **Rate limits and posting cadence.** `jobs.py`'s backoff/retry bounds
  (CT11) reduce the chance of hammering a platform, but they do not know a
  given platform's actual current rate limit — that number belongs in a
  validation profile (`lib/omes/py/content/platforms/<name>.json`) only once
  verified against that platform's own current documentation (see
  `docs/content-distribution.md` section 15), and even then the profile can go
  stale.
- **Content accuracy and disclosure requirements.** #69's unsupported-claim
  and disclosure detection is a best-effort regex-based safety net for
  obviously risky language, not a legal or platform-compliance review. The
  operator (or whoever approves a job) remains responsible for the caption's
  actual accuracy and for whatever disclosure a jurisdiction or platform
  requires.
- **Session/account security.** The operator, not OMES, performs the actual
  login during `omes content session login <platform>` (or via a real,
  operator-installed browser driver). Multi-factor-authentication prompts,
  session expiry, and account-security settings on the platform side are the
  operator's to manage; OMES only isolates and protects the resulting local
  session artifacts (CT01).
- **Reviewing every approval.** Nothing in this workflow inspects video
  content for policy compliance beyond the caption text; `omes content
  approve` records that a human looked at the plan, not that OMES verified
  the video itself is compliant.

## 6. Cross-references

- `docs/threat-model.md` row T40 points here for the full STRIDE table.
- `docs/security.md` section 1 has one row summarizing this workflow's
  least-privilege defaults (session isolation, approval gating, per-platform
  scoping).
- `docs/content-distribution.md` remains the authoritative design document;
  this file only adds the threat-model and test-coverage view on top of it.
