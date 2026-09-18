# ADR-0005: Root and user scope separation

- Status: Accepted
- Date: 2026-09-18

## Context

Some OMES modules must run as root (installing system packages, managing
system services, editing files outside any user's home), and some must
run as a specific non-root user (per-user Hermes installation under
`~/.hermes`, `--user` systemd units, dotfiles under `$HOME`). Running a
user-scope action as root would create root-owned files/units in a
location the real user needs to control, breaking that user's ability to
manage their own Hermes install and violating least-privilege. Running a
root-scope action as non-root would simply fail (no permission) or,
worse, silently degrade into a per-user partial install of something that
was supposed to be system-wide. The brief requires this separation
explicitly ("Separates privileged and user-level operations" is an
acceptance criterion of issue #4) and defines the enforcement mechanism
(exit 5) and the operator-facing invocation model (`sudo omes install
--profile server` for root-scope work; user-scope modules run as the
invoking user, never as root).

Candidates considered: a single `MODULE_SCOPE` flag enforced by exit-code
refusal (adopted), no scope enforcement (trust module authors to check
`EUID` themselves), and two entirely separate CLI binaries/entry points
for root vs. user operations.

## Options considered

### Option A: `MODULE_SCOPE` metadata variable, enforced centrally with exit 5, and automatic `sudo -u` re-dispatch for user-scope modules under a root-invoked run

- Security: the enforcement point is centralized in `lib/omes/module.sh`
  (Section 4.5 of `docs/architecture.md`), not duplicated per module —
  a module author cannot forget the check, because the runner refuses to
  call `module_apply` at all under the wrong privilege level. Re-dispatch
  via `sudo -u <user>` for user-scope modules under a root-invoked profile
  means a mixed-scope profile (e.g. `server`: `apt-base` root, `hermes`
  user) never lets a user-scope module observe `EUID == 0`, closing the
  root-owned-`~/.hermes` failure mode structurally rather than by
  convention.
- Performance: negligible — one `EUID` check and, when re-dispatch is
  needed, one additional `sudo -u` process fork per user-scope module.
- Maintainability: module authors declare `MODULE_SCOPE` once and never
  write privilege-check boilerplate themselves; the runner's enforcement
  code is unit-testable in isolation (bats can assert exit 5 for a
  root-scope module invoked as non-root and vice versa, without any real
  module).
- Scalability: works uniformly regardless of module count; adding a new
  module only requires picking the correct one of two scope values.
- Compatibility: `sudo -u` and `EUID` checks are POSIX-portable and behave
  identically on Ubuntu and Mint.
- Operational complexity: gives the operator one invocation to remember
  (`sudo omes install --profile <name>`) even for a profile mixing scopes
  — `bin/omes` does the internal dispatch, so the operator does not need
  to run the tool twice or reason about which modules need `sudo`.
- Long-term implications: this is the one place in the architecture that
  depends on `SUDO_USER` being reliably set (Section 4.5's "how is
  `<user>` determined" rule) — an environment that reaches root by a path
  other than `sudo` (e.g. `su -`, a root login shell) cannot be
  auto-dispatched and must fail closed (exit 5) with actionable guidance,
  which is an accepted, documented limitation (`docs/architecture.md`
  Section 13) rather than a silent gap.

### Option B: No centralized enforcement — trust each module to check `EUID` itself

- Security: weakest — enforcement quality now varies per module and per
  author; a single forgotten check in one module reintroduces the
  root-owned-`~/.hermes` risk for that module only, which is exactly the
  kind of inconsistency a shared contract is meant to prevent.
- Performance: same as Option A (the check itself is cheap regardless of
  where it lives).
- Maintainability: worse — the same boilerplate check must be copy-pasted
  (or forgotten) into every module; no single place to fix a bug in the
  enforcement logic.
- Scalability: enforcement quality degrades as module count grows and
  more authors touch the codebase without central review of this
  specific concern.
- Compatibility: no difference.
- Operational complexity: an operator has no structural guarantee across
  modules — behavior on privilege mismatch could differ module to module
  (some might exit oddly, some might partially apply before failing).
- Long-term implications: this is precisely the kind of cross-cutting
  concern that erodes over time without a single enforcement point;
  rejected because the module contract (ADR-0003) already establishes
  that cross-cutting behavior belongs in the runner, not in each module.

### Option C: Two separate CLI entry points (e.g. `omes` for root-scope, `omes-user` for user-scope), no in-process scope switching

- Security: comparable to Option A for the actual privilege boundary
  (each binary would still refuse the wrong EUID), but splits enforcement
  logic across two entry points to keep in sync.
- Performance: no meaningful difference.
- Maintainability: worse — two binaries means two argument parsers, two
  help texts, two places implementing overlapping logic (profile loading,
  logging, state access) that must be kept consistent; the brief's CLI
  contract (`bin/omes`) already specifies a single entry point.
- Scalability: no difference.
- Compatibility: no difference.
- Operational complexity: worse for the operator — a mixed-scope profile
  now genuinely requires two separate invocations (`sudo omes-root
  install --profile server` then `omes-user install --profile server`),
  which is exactly the friction Option A's auto-dispatch avoids, and
  which increases the chance an operator runs only one and believes the
  profile is fully installed.
- Long-term implications: contradicts the brief's explicit CLI contract
  (a single `bin/omes` with `--profile`/`--module` flags); would require
  re-litigating the whole CLI contract for a benefit (slightly simpler
  per-binary logic) that Option A already achieves via a shared runner.

## Decision

Enforce `MODULE_SCOPE` centrally in `lib/omes/module.sh`: a root-scope
module refuses to run (exit 5) unless `EUID == 0`; a user-scope module
refuses to run as root — when a root-invoked `install` resolves a
user-scope module, the runner re-dispatches that module's functions via
`sudo -u <user>` (target user derived from `SUDO_USER`, falling back to
exit 5 with guidance when no target user can be determined), so a
user-scope module never observes `EUID == 0` under any invocation path.
The operator-facing model is: `sudo omes install --profile server` for
any profile containing root-scope modules; `bin/omes` performs the
root/user dispatch per module internally.

## Consequences

- Every module must declare exactly one `MODULE_SCOPE` value; a module
  needing both root and user actions must be split into two modules
  linked by `MODULE_REQUIRES` (documented in `docs/architecture.md`
  Section 4.1).
- `bin/omes` must implement a `sudo -n true` preflight check (Section 10
  of `docs/architecture.md`) so an operator learns *before* mutation
  whether sudo is usable, rather than discovering it mid-`install`.
- The re-dispatch mechanism's correctness depends on `SUDO_USER`; test
  coverage (`tests/integration/*.bats` using `tests/shims/sudo`) must
  exercise both the direct-user-invocation path and the
  `sudo`-invoked-mixed-scope-profile path.
- This ADR's enforcement point (exit 5) is one of the ten stable exit
  codes (`docs/architecture.md` Section 9) and must not be repurposed for
  any other error class.
