# OMES Security Baseline

> Status: Phase 0 design baseline (issue #5), companion to
> [`docs/threat-model.md`](./threat-model.md). OMES has no installer code yet — the
> repository currently contains only `LICENSE` and `docs/research-and-implementation-plan.md`.
> Every control described below is **Not implemented yet**; each row/section names the issue
> that will implement it. This document defines what "done" means for that issue's security
> requirements — it is the acceptance bar, not a report of current behavior.

## 1. Least-privilege defaults

These are the defaults OMES must ship with. Any deviation requires an explicit operator
opt-in flag, and every opt-in must be visible in `omes status`/`omes doctor` output for as
long as it is active.

| Area | Default | Opt-in escape hatch | Implementing issue |
|------|---------|----------------------|---------------------|
| Installer scope separation | Root-scope modules refuse to run as non-root (exit 5); user-scope modules refuse to run as root (exit 5). No module is ever both. | None — this is a hard invariant of the module contract | #4, #6 |
| Docker access | `sudo docker` by default; rootless Docker evaluated when eligible | `--allow-docker-group` (prints a root-equivalence warning, recorded in state) | #9, #12 |
| Hermes runtime user | `hermes` (and its gateway) run as the unprivileged user who installed it, never as root | Explicit `--system` gateway install, which still runs the gateway as a dedicated service account, not root | #11, #12 |
| Secrets on disk | `$HERMES_HOME/.env` mode `0600`, owned by the running user | None | #11 |
| State directory | `/var/lib/omes/` (root scope) or `${XDG_STATE_HOME:-$HOME/.local/state}/omes/` (user scope), mode `0700` | None | #4, #6 |
| Backups | `<state-dir>/backups/<timestamp>/`, mode `0700`; files inside preserve source mode (`.env` stays `0600`) | None | #10 |
| API/gateway bind address | `127.0.0.1` only | Explicit non-loopback bind requires an explicit flag plus a matching firewall rule; never an implicit `0.0.0.0` | #12 |
| sudo | OMES never writes a NOPASSWD sudoers entry for any account | None — out of scope for any automated escape hatch | #6, #16 |

## 2. Telegram policy

Telegram is the primary remote-control surface for Hermes, and it is internet-facing by
construction (long-polling reaches every Telegram user, not just the operator, until an
allowlist filters them out). OMES's Telegram posture:

- **Numeric allowlists only, no wildcards.** `TELEGRAM_ALLOWED_USERS` must contain explicit
  numeric Telegram user IDs. Any documentation, setup helper, or default that would leave the
  DM surface open to unrecognized users is treated as a defect.
- **Both group allowlist variables must be set together.** `TELEGRAM_ALLOWED_CHATS` and
  `TELEGRAM_GROUP_ALLOWED_CHATS` must both list every allowed group. A group listed in only
  one is "half-enabled" and behaves inconsistently — any OMES-provided helper for adding a
  group writes both atomically, preserving file mode and owner, and never restarts the
  gateway as part of that write.
- **Restart required.** Allowlists are read once, at gateway start. Editing `.env` while the
  gateway is running changes nothing until it restarts. Documentation and any setup flow must
  say this explicitly rather than let an operator "test" an edit against a stale process.
- **`getUpdates` is prohibited against a running polling gateway.** Calling it causes a 409
  Conflict and can steal updates from the real poller. Chat discovery must use `getChat`,
  `getChatMember`, `getChatMemberCount`, or an observer pattern that records rejected-message
  metadata — never `getUpdates`, and never `setWebhook`/`deleteWebhook` on a polling
  deployment.
- **Mention policy.** Group behavior (whether the bot requires an explicit mention/reply to
  act) and the DM policy for unpaired senders (`unauthorized_dm_behavior`: ignore vs.
  pairing-code reply) must be documented per deployment, with pairing codes always delivered
  out-of-band (e.g., a DM to the owner) — never displayed on a surface the agent itself can
  read back.
- **Token handling.** The bot token is a secret (§5) and is never embedded in a hand-typed
  URL, printed to stdout, or passed as a CLI argument; Telegram API calls go through the
  Hermes CLI/tooling, not ad hoc `curl`.

Implementing issue: #13 (depends on #5, #11, #12).

## 3. Firewall and SSH policy

Applies to the Ubuntu Server headless profile by default; the desktop profile documents the
same principles but does not assume a server-style exposure.

- **Default deny incoming, allow outgoing** (`ufw default deny incoming` / `ufw default allow
  outgoing`), applied by the server profile.
- **SSH is allowed only when explicitly enabled by the operator via `--enable-ssh`, or when an
  SSH session is already active at the time OMES runs.** OMES does not assume SSH should be
  open just because the profile is headless.
- **Never lock the operator out.** Before applying any firewall change, OMES detects whether
  the current process is running inside an active SSH session (e.g., via `$SSH_CONNECTION` /
  `who`). If one is detected, port 22 stays allowed regardless of other flags, and OMES prints
  a warning naming the detected session. This is a hard invariant: there is no flag that
  disables it, because the failure mode (an operator locked out of a remote host with no
  console access) is worse than a slightly more permissive firewall.
- Any other inbound port (gateway API, Docker-published ports, a desktop remote-access tool)
  requires an explicit, individually justified firewall rule — never a blanket allow.

Implementing issue: #7 (depends on #6).

## 4. Update policy

- **Server profile:** `unattended-upgrades` configured for security updates only. OMES does
  not enable unattended upgrades for the full archive by default — only the security pocket —
  to avoid unreviewed behavioral changes on an unattended host.
- **Desktop profile:** updates remain manual. A desktop session is interactive by nature and
  an unexpected package upgrade mid-session (compositor, display driver) is more disruptive
  than useful; the operator is prompted/informed instead.
- OMES itself is versioned (`VERSION`, SemVer) and updated only via `omes update`, which must
  go through the same backup-before-mutate path as any other module action.

Implementing issue: #7 (server), #8 (desktop).

## 5. Secret handling rules for OMES code

These apply to every script under `bin/`, `lib/`, `modules/`, and `install/`:

- **Never echo a secret.** No `echo "$TOKEN"`, no `printf` of a variable known to hold a
  secret, no debug `set -x` left enabled around code that touches `.env` contents. Any
  temporary `set -x` for debugging must be paired with `set +x` immediately around
  secret-bearing sections, and reviewers must treat a missing `set +x` guard as a bug.
- **Redaction in logs.** `lib/omes/log.sh` provides a single redaction path; any value whose
  variable name matches a secret-like pattern (`*TOKEN*`, `*KEY*`, `*SECRET*`, `*PASSWORD*`)
  is masked before it reaches stdout, stderr, the log file, or `--json` output.
- **No secrets in argv.** OMES commands never accept a secret as a CLI flag value (visible via
  `ps`/`/proc/<pid>/cmdline` to any local user). Secrets are read from `.env`, environment
  variables set in a non-logged context, or stdin.
- **`.gitignore` for `.env`.** Any `.env` file, real or example-with-real-looking-values, is
  git-ignored. Example/template files use obviously fake placeholder values
  (`TELEGRAM_BOT_TOKEN=changeme`), never a plausible-looking token.
- **Backups copy `.env` by content, never by logging it.** The backup module records a sha256
  hash and file metadata in `MANIFEST`/`META`; it never writes secret file contents into a log
  or into the manifest itself.

Implementing issue: #6 (core helpers), #11 (Hermes secret handling), #16 (CI enforcement).

## 6. Supply-chain rules

- **Download-to-file, never `curl | bash` directly.** Any third-party installer (notably the
  Hermes installer) is downloaded to a temporary file first, then optionally verified, then
  executed as a separate step. This makes the exact bytes executed inspectable and pinnable.
- **Optional SHA-256 pins.** `OMES_HERMES_INSTALLER_SHA256` (and equivalent variables for other
  fetched artifacts) let an operator pin an expected hash; a mismatch aborts before execution
  with no mutation.
- **GitHub Actions pinned by SHA.** Every third-party Action referenced in
  `.github/workflows/*.yml` is pinned to a commit SHA, not a mutable tag or branch. Bumping a
  pin is a reviewed change, not an automatic update.
- **No PPAs unless allowlisted.** OMES does not run `add-apt-repository` for an arbitrary PPA
  at runtime. Any PPA a module needs is declared in the compatibility layer
  (`lib/omes/pkg.sh`) as part of an explicit, reviewed allowlist — a module cannot introduce a
  new package source that wasn't already reviewed.
- **Dependency review for language-ecosystem packages.** Where Hermes or desktop tooling pulls
  in npm/pip packages, OMES documents the trust boundary; OMES itself does not vendor or
  silently install additional language-ecosystem dependencies beyond what those upstream
  installers require.

Implementing issue: #6, #9, #11 (mechanics), #16 (CI enforcement and pin review).

## 7. Security acceptance gates for release

No OMES release (including an internal pilot) proceeds unless all of the following hold.
These gates map directly to the QA/security issues that produce the evidence:

| Gate | Evidence required | Verified by |
|------|---------------------|-------------|
| No secret in the tree or history | Secret-scanning CI job passes on the full history, not just the diff | #16 |
| ShellCheck clean on all shell under `bin/`, `lib/`, `modules/`, `install/` | CI job output attached to the PR | #16 |
| Third-party Actions pinned by SHA | CI/lint check rejects unpinned Action references | #16 |
| No destructive default | Manual review against the module contract (`module_apply` honors `OMES_DRY_RUN`; no module deletes data without a prior backup) | #17 |
| Rollback/restore proven offline | `omes restore` succeeds with network disabled, restoring a known-good backup verified against `MANIFEST` sha256 | #17 |
| Firewall/SSH invariant proven | Test asserts an active SSH session is never locked out when firewall rules are applied | #7, #17 |
| Telegram allowlist behavior proven | Test asserts unauthorized users/groups are rejected and authorized ones are served, in both directions (see hermes-telegram-bot lessons: verify allowlisted groups still serve unknown *users* correctly) | #13, #15 |
| Known limitations published | This document and `docs/threat-model.md` are current and linked from the release notes | #5, #18 |

## 8. What OMES does NOT claim

To keep security claims honest and bounded to what OMES actually controls:

- OMES does **not** provide disk encryption. Full-disk or home-directory encryption is a
  host/OS installation-time decision outside OMES's reach on an already-installed system.
- OMES does **not** manage or harden the bootloader. Secure Boot, GRUB configuration, and
  kernel signing are untouched by OMES.
- OMES is **not** a security audit, and running it does not certify a host as secure. It
  applies a documented set of least-privilege defaults and reversible changes; it does not
  scan for or fix vulnerabilities unrelated to what it installs.
- OMES does **not** control Hermes's own agent decision-making (when it chooses to call a
  tool, what it treats as authorization) — that is upstream Hermes behavior. OMES configures
  the environment Hermes runs in, not its reasoning.
- OMES does **not** set a hard ceiling on LLM provider spend. That control exists only at the
  provider's dashboard/billing settings.
- OMES does **not** guarantee protection against a determined operator overriding a documented
  default (e.g., manually adding a user to the `docker` group, disabling the firewall). OMES's
  responsibility ends at making the safe path the default and any deviation explicit, logged,
  and reversible.

See [`docs/threat-model.md`](./threat-model.md) for the full threat/mitigation mapping this
baseline is derived from, and `SECURITY.md` at the repository root for how to report a
vulnerability in OMES itself.
