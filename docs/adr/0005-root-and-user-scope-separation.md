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

Candidates considered: a single `MODULE_SCOPE` flag enforced centrally by
**skipping mismatched-scope modules and instructing the operator on the
follow-up command** (adopted); the same `MODULE_SCOPE` flag enforced
centrally but with **automatic `sudo -u <user>` re-dispatch** so one
invocation applies both scopes; no scope enforcement (trust module
authors to check `EUID` themselves); and two entirely separate CLI
binaries/entry points for root vs. user operations.

## Options considered

### Option A: `MODULE_SCOPE` metadata variable, enforced centrally, mismatched-scope modules skipped with an explicit follow-up instruction (no automatic escalation or de-escalation)

- Security: the enforcement point is centralized in `lib/omes/module.sh`
  (Section 4.5 of `docs/architecture.md`), not duplicated per module — a
  module author cannot forget the check, because the runner never calls
  `module_apply` for a module outside the current effective privilege.
  Critically, `bin/omes` never calls `sudo` to gain root and never calls
  `sudo -u`/any equivalent to shed root and act as another identity: a
  root process never touches a user's `$HOME`, `$XDG_RUNTIME_DIR`, or
  `systemctl --user` bus on that user's behalf, and a user-scope module
  never observes `EUID == 0` under any invocation path, because it is
  simply never invoked while the process is root. This removes an entire
  class of "root silently acting as a user" risk rather than mitigating
  it with careful re-dispatch plumbing.
- Performance: negligible — one `EUID`-vs-`MODULE_SCOPE` comparison per
  module, no extra process forks beyond the modules that actually run.
- Maintainability: module authors declare `MODULE_SCOPE` once and never
  write privilege-check boilerplate; the runner's filter logic is
  unit-testable in isolation (bats can assert that a root-scope module is
  skipped, not failed, when invoked as non-root, and vice versa, without
  any real module). There is no `sudo -u` re-invocation plumbing
  (`--preserve-env` lists, an internal re-entry point) to keep correct.
- Scalability: works uniformly regardless of module count; adding a new
  module only requires picking the correct one of two scope values.
- Compatibility: an `EUID` check is POSIX-portable and behaves identically
  on Ubuntu and Mint; nothing here depends on `sudo`'s own environment-
  passing behavior (which differs across `sudo` versions/configurations
  for `HOME`, `XDG_RUNTIME_DIR`, and `DBUS_SESSION_BUS_ADDRESS` — exactly
  the variables a de-escalated user-scope module would need correct to
  safely call `systemctl --user` or write to the right `$HOME`).
- Operational complexity: the operator runs `omes install --profile
  <name>` twice for a mixed-scope profile — once with `sudo`, once
  without — but each run tells them, explicitly, the exact next command
  to run (Section 3.2, step 8, of `docs/architecture.md`). This trades a
  small amount of operator friction (two commands instead of one) for a
  much simpler, more auditable privilege model: each invocation is doing
  exactly one thing, as exactly one identity, with its own log file and
  its own scope's state directory (Section 6.1) — there is never a run
  where "root did some of this and, invisibly, someone else did the rest."
- Long-term implications: keeps `bin/omes`'s privilege model permanently
  simple — "the process's own EUID is the only privilege OMES ever acts
  under" — which is easy to state, easy to audit, and does not depend on
  `sudo` conventions (`SUDO_USER`, environment preservation flags)
  continuing to behave a particular way across distributions or `sudo`
  versions.

### Option B: `MODULE_SCOPE` metadata variable, enforced centrally with exit 5, and automatic `sudo -u` re-dispatch for user-scope modules under a root-invoked run (rejected)

- Security: superficially closes the same root-owned-`~/.hermes` failure
  mode as Option A (a user-scope module still never observes `EUID == 0`),
  but does so by having a **root process actively act on behalf of a
  user account** — constructing and running `sudo -u <user>
  --preserve-env=... <module functions>` from within a process that is
  already root. Getting this right requires root to correctly determine
  `HOME`, `XDG_RUNTIME_DIR`, and the user's systemd `--user` D-Bus session
  address for the target user, none of which `sudo -u` sets up
  automatically the way a real login session would; a mistake here
  produces the exact failure class this ADR exists to avoid (e.g.
  `systemctl --user` from inside a `sudo -u` shell frequently cannot reach
  the target user's actual user session/manager unless additional
  environment plumbing and `loginctl enable-linger`/session bus wiring is
  done correctly), and a bug in that plumbing is a *root-privileged* bug
  by construction, since the process constructing the `sudo -u` call is
  root.
- Performance: negligible — one `EUID` check and, when re-dispatch is
  needed, one additional `sudo -u` process fork per user-scope module.
- Maintainability: module authors still declare `MODULE_SCOPE` once, but
  `lib/omes/module.sh` now also owns a second subsystem: the re-dispatch
  mechanism itself (an internal re-entry point, an environment allow-list
  to preserve across the `sudo -u` boundary, and target-user resolution).
  That subsystem's correctness is load-bearing for the entire
  root/user separation guarantee this ADR is meant to provide, which is a
  larger and riskier surface to keep correct than a filter-and-skip check.
- Scalability: works uniformly regardless of module count, same as
  Option A, but every user-scope module pays the re-dispatch environment-
  correctness risk, not just a fixed one-time cost.
- Compatibility: depends on `sudo -u`'s environment-passing behavior,
  which is configurable and varies by `sudo` version/`sudoers` policy
  (`env_reset`, `env_keep`, etc.) across Ubuntu and Mint — a difference in
  default `sudoers` policy between the two target platforms is exactly
  the kind of drift Option A avoids entirely by never using `sudo -u`.
- Operational complexity: gives the operator one invocation to remember
  for a mixed-scope profile, which is real convenience — but it comes at
  the cost of a single `sudo omes install` run producing **two different
  privilege contexts' worth of mutation** inside one process, one log
  file, and one apparent "run", which is harder to audit after the fact
  than two clearly separate runs (Option A): an incident investigation
  cannot tell, from the log alone, "was this action really taken as the
  user, with the user's actual session environment, or as root pretending
  to be the user" without re-deriving the `sudo -u` environment that was
  in effect at that moment.
- Long-term implications: this is the one design that depends on
  `SUDO_USER` being reliably set by `sudo` and correctly resolvable to a
  real, loginable user; an environment that reaches root by a path other
  than `sudo` (`su -`, a root login shell, some containerized entrypoints)
  cannot be auto-dispatched at all and must fail closed regardless — so
  Option B does not even universally deliver the one-invocation
  convenience it promises, while carrying the added HOME/
  `XDG_RUNTIME_DIR`/`systemctl --user` breakage risk and the weaker
  auditability of mixed-privilege single-process runs. Rejected for these
  reasons: it trades a real, avoidable security/auditability cost (a root
  process implicitly acting on a user's behalf, with a real chance of
  broken `--user` systemd/session-bus environment) for operator
  convenience that Option A recovers most of via explicit, printed
  follow-up commands.

### Option C: No centralized enforcement — trust each module to check `EUID` itself

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

### Option D: Two separate CLI entry points (e.g. `omes` for root-scope, `omes-user` for user-scope), no in-process scope switching

- Security: comparable to Option A for the actual privilege boundary
  (each binary would still refuse the wrong EUID and never escalate/
  de-escalate), but splits enforcement logic across two entry points to
  keep in sync.
- Performance: no meaningful difference.
- Maintainability: worse — two binaries means two argument parsers, two
  help texts, two places implementing overlapping logic (profile loading,
  logging, state access) that must be kept consistent; the brief's CLI
  contract (`bin/omes`) already specifies a single entry point.
- Scalability: no difference.
- Compatibility: no difference.
- Operational complexity: comparable to Option A in that it also requires
  two separate invocations for a mixed-scope profile, but now with two
  different command names (`sudo omes-root install --profile server` then
  `omes-user install --profile server`) instead of one command run twice
  under different privilege — a distinction without a real benefit over
  Option A's single-binary skip-and-instruct model.
- Long-term implications: contradicts the brief's explicit CLI contract
  (a single `bin/omes` with `--profile`/`--module` flags); would require
  re-litigating the whole CLI contract for no meaningful gain over
  Option A, which already achieves the same "two separate, auditable
  invocations" property inside one binary.

## Decision

Enforce `MODULE_SCOPE` centrally in `lib/omes/module.sh` as a **filter,
not an escalation mechanism**: for any `omes check`/`omes install` run,
only modules whose `MODULE_SCOPE` matches the current effective privilege
(root EUID 0, or non-root) are checked/applied; modules of the other
scope are skipped for that run — not failed, and the run does not exit 5
for that reason. `bin/omes` never calls `sudo` to gain root and never
calls `sudo -u` (or any equivalent) to shed root and act as another user.
On completion, the run prints the exact follow-up command to apply any
skipped modules (`Run as your user: omes install --profile <name>` after
a root run skips user-scope modules; `Run with sudo: sudo omes install
--profile <name>` after a non-root run skips root-scope modules). `exit
5` is reserved for the one case that IS an error: an explicit `--module
<name>` request naming a module whose scope does not match the current
effective privilege.

## Consequences

- Every module must declare exactly one `MODULE_SCOPE` value; a module
  needing both root and user actions must be split into two modules
  linked by `MODULE_REQUIRES` (documented in `docs/architecture.md`
  Section 4.1).
- A mixed-scope profile is applied to completion only by two separate
  operator-initiated invocations (one as root, one as the target user);
  `docs/architecture.md` Section 13 records this, and the fact that
  `omes install` does not itself track "the other scope is still
  pending" beyond the printed reminder, as known limitations.
- `bin/omes` still implements a `sudo -n true` preflight check (Section
  10 of `docs/architecture.md`) so an operator learns *before* mutation
  whether they have usable `sudo` for the root-scope follow-up
  invocation — this check is purely informational and never causes
  `bin/omes` to call `sudo` itself.
- A `MODULE_REQUIRES` edge that crosses scopes can only be satisfied by
  an earlier, separate invocation of the required module's own scope,
  since a user-scope module cannot read the root scope's state directory
  (`0700`, Section 6.5) to confirm a root-scope dependency was applied —
  documented as a known limitation in `docs/architecture.md` Section 13,
  not resolved by this ADR.
- This ADR's enforcement point (exit 5) is one of the ten stable exit
  codes (`docs/architecture.md` Section 9) and must not be repurposed for
  any other error class; it now applies narrowly to an explicit
  wrong-scope `--module` request, not to a mixed-scope profile run.
- Test coverage (`tests/integration/*.bats`) must assert: (a) a root
  invocation of a mixed-scope profile applies root-scope modules, skips
  user-scope modules, and prints the correct follow-up command; (b) the
  symmetric non-root case; and (c) `--module <name>` naming a
  wrong-scope module exits 5 immediately with no mutation.
