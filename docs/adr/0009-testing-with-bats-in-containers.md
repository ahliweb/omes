# ADR-0009: Testing with bats-core, in containers

- Status: Accepted
- Date: 2026-09-18

## Context

OMES's implementation is bash (ADR-0001), and its correctness properties
that matter most are exactly the ones bash tooling struggles to verify by
inspection alone: idempotency, fail-fast ordering, exit codes, and
"no mutation during check." The brief's testing conventions already
specify bats-core, with unit tests sourcing `lib/omes/*.sh` directly and
integration tests executing `bin/omes` against `tests/shims/` (fake
`apt-get`, `systemctl`, `sudo`, `hermes`, `curl`), run via
`docker run --rm -v "$PWD:/mnt" bats/bats:latest tests/unit` when bats is
not installed locally, alongside ShellCheck via the `koalaman/shellcheck`
image. This ADR records why that combination (bats-core, run in
containers when needed) is the right testing approach rather than an
incidental detail — this is a genuine architectural choice with security
and maintainability consequences.

Candidates considered: bats-core with Docker-based fallback execution
(adopted), a custom bash test harness written for OMES, and skipping
automated shell tests in favor of manual QA / VM testing only.

## Options considered

### Option A: bats-core (TAP-producing bash test framework) for unit + integration, executed locally when available or via Docker when not, plus ShellCheck via its own Docker image

- Security: running tests against `tests/shims/` fakes for `apt-get`,
  `systemctl`, `sudo`, `hermes`, `curl` means integration tests never
  actually install packages, start services, or hit the network — a test
  run cannot accidentally mutate the CI host or a developer's machine,
  which matters because OMES's own subject matter is "safely mutate a
  real host." ShellCheck run at `-x -S style` catches a wide class of
  quoting/globbing bugs before they reach a real host at all.
- Performance: bats/ShellCheck runs are fast (seconds), and the Docker
  fallback means CI does not depend on the runner image happening to
  have bats/shellcheck preinstalled at a particular version — the
  pinned images (`bats/bats:latest`, `koalaman/shellcheck:stable`) give
  reproducible behavior across contributors' machines and CI.
- Maintainability: bats' `.bats` files are close to plain bash (a
  `@test "description" { ... }` block), so module authors writing bash
  can write tests in the same language and mental model, without learning
  a second test framework's DSL; `tests/run.sh` centralizes "how do I run
  everything" into one command.
- Scalability: works the same whether there are 5 or 50 modules; each
  module simply gets its own `tests/unit/<name>.bats`.
- Compatibility: bats-core and ShellCheck both work identically on
  Ubuntu/Mint or any CI runner with Docker, avoiding "works on my machine"
  drift from host-installed tool version differences.
- Operational complexity: contributors without bats/ShellCheck installed
  locally can still run the full suite via the documented Docker
  commands, lowering the bar to contribute correctly-tested code; CI uses
  the same commands, so local and CI results should not diverge.
- Long-term implications: because integration tests run against shims
  rather than real `apt`/`systemctl`, they cannot alone prove real-host
  behavior — that is explicitly the job of `tests/vm/` (VM-based
  compatibility testing per the compatibility matrix, issue #3), which
  this ADR treats as a complementary, not competing, layer.

### Option B: A custom bash test harness written specifically for OMES

- Security: could be built to have the same shim-based isolation as
  Option A, so no inherent security disadvantage — but every safety
  property (test isolation, TAP-like reporting, assertion helpers) would
  need to be built and maintained by OMES itself rather than reused from
  a mature, widely-used project.
- Performance: comparable to Option A once built.
- Maintainability: strictly worse — OMES would own an entire test
  framework as a side project, competing for maintenance time against the
  actual product; bats-core already solves this problem well for shell
  projects and is a known quantity to many bash-comfortable contributors.
- Scalability: no material advantage over Option A; more modules simply
  means more custom-harness test files instead of more `.bats` files.
- Compatibility: a custom harness is untested by the broader community,
  so edge cases (subshell exit-code propagation, `set -e` interactions in
  test blocks) that bats-core has already encountered and fixed would need
  to be independently rediscovered.
- Operational complexity: contributors would need to learn OMES's own
  bespoke test DSL instead of a framework many shell-project contributors
  already know.
- Long-term implications: pure maintenance cost with no corresponding
  benefit over an existing, adopted tool; rejected.

### Option C: No automated shell test suite — rely on manual QA and VM testing only

- Security: much weaker regression protection — a change that
  accidentally makes `module_check` mutate, or breaks fail-fast ordering,
  or flips an exit code, would only be caught by a human manually
  re-running scenarios, which does not scale with the number of modules
  and is exactly the kind of subtle bash bug ShellCheck/bats are good at
  catching mechanically and quickly.
- Performance: no CI test time cost, but at the cost of slower human
  feedback loops (manual QA per PR) and higher risk of regressions
  reaching a VM test or, worse, a real operator's host.
- Maintainability: appears cheaper short-term (no test files to write)
  but accumulates risk as module count grows (issues #6-#14 alone add
  ~8 modules) with no mechanical safety net.
- Scalability: does not scale — manual QA effort grows at least linearly
  with surface area, with no reuse across changes the way a test suite
  provides.
- Compatibility: no material difference in this dimension specifically,
  though it makes the VM-based compatibility matrix (issue #3) carry a
  disproportionate share of defect-catching that unit/integration tests
  could catch faster and cheaper.
- Operational complexity: raises the bar for confidently merging any
  change, since there is no fast, repeatable signal that "the check-all-
  then-apply contract still holds" beyond a human re-reading the diff.
- Long-term implications: directly conflicts with the brief's own
  mandatory testing conventions (bats + ShellCheck are specified as
  "mandatory working rules," not optional); rejected.

## Decision

Use bats-core for both unit tests (`tests/unit/*.bats`, sourcing
`lib/omes/*.sh` directly) and integration tests (`tests/integration/*.bats`,
executing `bin/omes` with `OMES_DRY_RUN=1` and `PATH` pointed at
`tests/shims/`), plus ShellCheck (`-x -S style`) on all shell files. Both
tools run via Docker (`bats/bats:latest`, `koalaman/shellcheck:stable`)
when not available locally, driven by a single `tests/run.sh` entry point
that CI and contributors both use.

## Consequences

- Every module PR must add or update `tests/unit/<name>.bats` and, when
  it touches packages/services, `tests/integration/<name>.bats`
  (Section 12.1 of `docs/architecture.md`).
- `tests/shims/` fakes must be kept realistic enough (matching real
  `apt-get`/`systemctl`/`hermes` CLI surfaces closely enough) that passing
  integration tests are meaningful signal, not just "the shim did
  whatever the test expected" — shim drift from real tool behavior is a
  known risk this ADR accepts and expects `tests/vm/` to catch.
- `.github/workflows/lint.yml` runs ShellCheck + bats on every PR; a
  failing run blocks merge per the brief's working rules.
- VM-based testing (`tests/vm/`) remains the authority for real-host
  behavior (actual `apt`, actual `systemctl`, actual hardware for the
  desktop profile) and is not replaced by the shim-based suite this ADR
  governs.
