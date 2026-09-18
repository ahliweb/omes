# ADR-0001: Bash as the implementation language

- Status: Accepted
- Date: 2026-09-18

## Context

OMES needs an implementation language for `bin/omes`, `lib/omes/*`, and
`modules/*/module.sh`. The tool's job is fundamentally to orchestrate other
system-level tools (`apt`, `systemctl`, `curl`, the Hermes CLI, Docker) on
Ubuntu Server 24.04 LTS and Linux Mint 22.x. It must run correctly on a
freshly provisioned server with a minimal package set, without requiring
the operator to first install a language runtime just to run the
installer, and it must be auditable by an operator who is not a
professional software engineer but is comfortable reading shell.

Candidates considered: plain Bash (bash 5.x, present by default on both
target platforms), Python 3, and Ansible.

## Options considered

### Option A: Bash

- Security: no additional interpreter/runtime to trust or patch; every
  action maps directly and visibly to the underlying command it runs
  (`apt-get install ...`, `systemctl enable ...`), which is easy to audit
  line-by-line and easy to run through ShellCheck. Risk: bash's quoting
  and error-handling pitfalls are a real footgun if strict mode
  (`set -Eeuo pipefail`) and ShellCheck are not enforced everywhere.
- Performance: negligible interpreter startup cost; process-per-command
  overhead (forking `apt-get`, `systemctl`, etc.) dominates regardless of
  the orchestration language, so bash's own overhead is not a bottleneck.
- Maintainability: shell is verbose for anything beyond string/process
  orchestration (no real data structures, weak error propagation without
  discipline), but the *whole job* here is string/process orchestration.
  A consistent module contract (Section 4 of `docs/architecture.md`) and a
  shared `lib/omes/*.sh` mitigate the lack of language-level structure.
- Scalability: fine for OMES's scale (tens of modules, not thousands); not
  a general-purpose application runtime, which is the right amount of
  power for this problem.
- Compatibility: bash 5.x ships by default on both Ubuntu 24.04/22.04 and
  Linux Mint 22.x — zero additional runtime dependency, works identically
  on a minimal server install.
- Operational complexity: lowest — no venv, no interpreter version
  matrix, no dependency lockfile to manage in production; `curl | file |
  bash` (downloaded-to-file, not piped, per ADR-0006) is the only
  distribution mechanism needed.
- Long-term implications: bash's ecosystem for structured testing (bats)
  and linting (ShellCheck) is mature and already assumed by the testing
  conventions in the brief; ties OMES's contributor pool to people
  comfortable with shell, which matches the sysadmin/DevOps audience OMES
  targets.

### Option B: Python 3

- Security: stronger language-level safety (typing, exceptions, no word
  splitting/globbing footguns) than bash, but every action still ultimately
  shells out to `apt`/`systemctl`/etc., so the security benefit is
  concentrated in the orchestration logic, not the actions themselves.
- Performance: interpreter startup (~50-100ms) is irrelevant at OMES's
  scale.
- Maintainability: much better for complex logic (topological sort,
  JSON handling, structured config) — this is Python's strongest
  argument. Testing story is also more standard (pytest).
- Scalability: comfortably handles OMES's scope and far beyond.
- Compatibility: Python 3 ships on both platforms by default, but *exact*
  version and available third-party packages differ across 24.04/22.04/
  Mint, and installing third-party packages (`pip`) on a system Python is
  itself a packaging problem OMES would have to solve on every target
  before OMES could run at all — a bootstrapping dependency the brief's
  "curl-able `install/bootstrap.sh`" entry point does not want.
- Operational complexity: higher — need to pin a Python version story,
  decide on stdlib-only vs a vendored dependency policy, and the
  installer becomes "a Python program that needs Python" rather than "a
  shell script that needs a shell," which is a weaker bootstrapping
  story for a tool whose first job is to prepare a fresh host.
- Long-term implications: better for a large, growing codebase; OMES's
  target complexity (module orchestration, not application logic) does
  not clearly justify the added runtime dependency at MVP scale.

### Option C: Ansible

- Security: mature, widely audited tool; but its execution model (control
  node + modules, often over SSH even for localhost) is heavier than what
  a single-host CLI needs, and its idempotency guarantees are only as
  good as the specific modules used, similar to OMES's own module
  contract.
- Performance: noticeably slower per-task overhead than direct shell/
  systemctl calls, which matters less for occasional installs but adds
  friction to `omes check`'s "no mutation, fast preflight" use case.
- Maintainability: YAML playbooks plus Jinja2 templating are good for
  declarative package/file/service state, but expressing the check/apply/
  verify/rollback contract (Section 4) and DIY state semantics (Section 6)
  on top of Ansible's own state model doubles the amount of state
  machinery to reason about.
- Scalability: designed for fleets; OMES targets a single host per
  invocation, so Ansible's fleet-oriented strengths (inventories, parallel
  fan-out) are not exercised.
- Compatibility: requires installing Ansible itself (and Python) on the
  target or a control machine before anything else can run — the same
  bootstrapping problem as Option B, worse.
- Operational complexity: adds a dependency (Ansible + Python + its own
  version compatibility matrix) that OMES would then need to preflight-
  check before OMES's own preflight can run.
- Long-term implications: would tie OMES's roadmap to Ansible's release
  cadence and module ecosystem; makes "read the module and know exactly
  what command runs" (an explicit design goal, see `docs/architecture.md`
  Section 10 on `curl | bash` avoidance) harder, since Ansible modules
  abstract the underlying command away.

## Decision

Implement OMES in Bash (bash 5.x, `set -Eeuo pipefail` strict mode, one
`shellcheck shell=bash` directive per file, enforced by CI). No language
runtime beyond what ships by default on Ubuntu Server 24.04/22.04 and
Linux Mint 22.x is required to run OMES.

## Consequences

- Every module and library file must pass `shellcheck -x -S style`; this
  is a CI gate (`.github/workflows/lint.yml`), not a suggestion.
- Complex data structures (dependency graphs, JSON) are handled with
  small, well-tested helper functions in `lib/omes/*.sh`
  (`lib/omes/module.sh` for the topological sort, `lib/omes/json.sh` for
  JSON) rather than a general-purpose data-structure library.
- Contributors are expected to be comfortable reading/writing POSIX-ish
  bash with bash 5 extensions; this is documented in `CONTRIBUTING.md`.
- If OMES's orchestration logic later outgrows what is comfortably
  expressible in bash (e.g. a genuinely large dependency graph, or complex
  templating), that would motivate a new ADR reconsidering this decision
  for the affected subsystem specifically — this ADR is not a permanent
  ban on ever introducing another language, only the MVP default.
