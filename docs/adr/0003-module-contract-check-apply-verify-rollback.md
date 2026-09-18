# ADR-0003: Module contract — check / apply / verify / rollback

- Status: Accepted
- Date: 2026-09-18

## Context

OMES is composed of independent units of work ("modules": `apt-base`,
`hermes`, `hyprland-session`, etc.) that each install/configure one piece
of the system. Every module needs a common interface so `lib/omes/module.sh`
can orchestrate them uniformly (ordering, dry-run, state writes, error
reporting) without special-casing each module's internals. The interface
must support: a non-mutating precondition check, an idempotent mutation,
a proof the mutation worked, and a best-effort undo — because the brief's
acceptance criteria require documented inputs/outputs/dependencies/
ordering, defined idempotency, and separated privileged/user operations
(issue #4), and because reversibility is a hard product requirement
("Everything reversible" — see the brief).

Candidates considered for the module interface shape: the four-function
contract (`check`/`apply`/`verify`/`rollback`) actually adopted, a
two-function contract (`apply`/`verify` only, folding "check" into "apply"
under `--dry-run`), and a single-function contract (`apply` only, with
idempotency as its only guarantee and no explicit verify/rollback step).

## Options considered

### Option A: Four functions — `check` / `apply` / `verify` / `rollback`

- Security: separating `check` (read-only) from `apply` (mutating) means
  the check-all-then-apply run (ADR-0004) can prove, before any host is
  touched, that every module's preconditions hold — reducing the chance of
  a mutation starting on a host that was never going to fully succeed.
  `verify` gives an independent proof step distinct from "apply exited 0",
  catching apply-succeeded-but-effect-absent bugs.
- Performance: four function calls per module is negligible overhead
  compared to the actual work (package installs, service restarts) each
  performs.
- Maintainability: a fixed, small interface every module must implement
  is easy to document, easy to test per-function in isolation (bats can
  unit-test `module_check` without ever calling `module_apply`), and easy
  to review for compliance (a missing function is a load-time/lint-time
  defect, not a runtime surprise).
- Scalability: as the module count grows (issues #6-#14 alone add ~8),
  a uniform interface is what makes `lib/omes/module.sh`'s dependency
  resolution and run sequencing (ADR-0004) scale without per-module
  special cases.
- Compatibility: the interface is pure bash functions and variables — no
  compatibility concerns beyond bash itself (ADR-0001).
- Operational complexity: `rollback` gives operators an explicit, named
  recovery action per module distinct from `omes restore`'s generic
  file-level restore — useful when a module's undo is more than "put the
  file back" (e.g. stopping a service it started).
- Long-term implications: the four-function shape is the natural home for
  future per-module additions (e.g. a `module_upgrade` for issue-tracked
  future work) without redesigning the whole contract.

### Option B: Two functions — `apply` (dry-run-aware) / `verify`

- Security: folding "check" into "apply --dry-run" means the check-all
  phase would call `apply` in dry-run mode for every module before any
  module actually applies — workable, but it means the same function body
  is responsible for both "tell me if this would work" and "do it", which
  is more surface area for a dry-run bug to accidentally mutate (a bug in
  the dry-run branch is now inside the same function as the real mutation
  logic, not an isolated read-only function).
- Performance: similar to Option A.
- Maintainability: fewer functions to write per module (less boilerplate),
  but each `apply` becomes a larger function with an internal branch for
  dry-run vs real, which is harder to keep correct than two separate,
  smaller functions.
- Scalability: fine.
- Compatibility: fine.
- Operational complexity: no separate `rollback` in this option variant
  as considered — recovery would rely entirely on `omes restore`'s
  generic file backup, which cannot express "stop this service" or "remove
  this cron entry" as cleanly as a module-authored `module_rollback` can.
- Long-term implications: conflating check and apply makes "check must
  never mutate" a property of *every apply function's dry-run branch*
  being correct, rather than a property that a wholly separate,
  intrinsically read-only function (`module_check`) can be trivially
  audited (and lint-checked) to have.

### Option C: Single function — `apply` only, idempotent, no explicit verify/rollback

- Security: weakest option — no independent proof that apply's claimed
  success matches reality, and no structured recovery path beyond
  `omes restore`'s file-level restore (which cannot undo a started
  service or a package installation cleanly).
- Performance: least overhead, but the overhead of the other options is
  already negligible, so this is not a meaningful advantage.
- Maintainability: simplest per-module code, but pushes the burden of
  precondition checking and idempotency entirely onto module authors with
  no structural encouragement (no separate `check` to write and test);
  historically this is the pattern that produces "idempotent in theory,
  not in practice" scripts.
- Scalability: fine for a small number of modules; becomes harder to
  reason about "did this actually work" at scale without a `verify` step.
- Compatibility: fine.
- Operational complexity: worst — an operator debugging a failed run has
  only "apply exited non-zero" or "apply exited zero" to go on, no
  distinct verification signal, and no named rollback action.
- Long-term implications: would need to be revisited (i.e., replaced by
  something like Option A) as soon as any module's failure mode was
  costly enough to need explicit rollback — better to adopt that shape
  now than migrate every existing module later.

## Decision

Adopt the four-function module contract: `module_check`, `module_apply`,
`module_verify`, `module_rollback`, plus the five metadata variables
(`MODULE_NAME`, `MODULE_DESCRIPTION`, `MODULE_SCOPE`, `MODULE_REQUIRES`,
`MODULE_PROFILES`), exactly as specified in `docs/architecture.md` Section
4. `lib/omes/module.sh` calls `module_check` for all modules before any
`module_apply` (ADR-0004), then `module_apply` → `module_verify` per
module in dependency order, and reserves `module_rollback` for explicit
recovery flows (`omes uninstall`, `omes restore`, or an operator-invoked
rollback), never as an automatic response to an apply/verify failure.

## Consequences

- Every module must implement all four functions, even if some are
  trivial (`module_rollback() { :; }` is acceptable only if there is
  genuinely nothing to undo beyond what `omes restore`'s generic file
  restore already covers — this must be a documented, deliberate choice
  in the module, not an oversight).
- `tests/unit/*.bats` can and must test `module_check` and `module_verify`
  in isolation, without invoking `module_apply`, because they are pure
  read-only functions.
- The contract is a hard interface: `lib/omes/module.sh` sources each
  module file and calls these exact function names; a module missing one
  is a load-time defect (surfaced as a usage error, exit 2), not a
  runtime crash deep in a run.
- This ADR does not mandate any particular internal structure within a
  function body — only the four-function boundary and each function's
  read-only/mutating classification.

<!-- OMES-MERMAID: docs/adr/0003-module-contract-check-apply-verify-rollback.md -->

## Visual summary

```mermaid
flowchart LR
    A[Check] --> B[Apply]
    B --> C[Verify]
    C --> D[Rollback]
```

