# ADR-0004: Check-all-before-mutate execution order

- Status: Accepted
- Date: 2026-09-18

## Context

`omes install` runs multiple modules per invocation (a full profile, or a
`--module`-filtered subset). Given the four-function module contract
(ADR-0003), there is a choice about *when* `module_check` runs relative to
`module_apply`/`module_verify` across the whole batch. The brief is
explicit: "run `module_check` for ALL modules → if any fails, exit 4 with
no mutation → for each module: backup managed paths, `module_apply`,
`module_verify`, write state → on failure stop." This ADR records why that
specific ordering — check-all, then apply-one-at-a-time with fail-fast —
was chosen over the alternatives.

Candidates considered: check-all-then-apply-all (adopted), interleaved
check-then-apply per module (no upfront batch check), and apply-all-then-
verify-all (defer verification to the end).

## Options considered

### Option A: Check all modules, then apply modules one at a time, stop on first apply/verify failure

- Security: maximizes the chance that a partially-capable host is
  detected *before* any mutation starts — e.g. if module 4 of 5 in a
  profile would fail its check (missing network, unsupported kernel), the
  operator finds out with zero mutation having occurred, rather than
  after modules 1-3 already changed the host. This directly serves the
  "no destructive defaults" / "everything reversible" product requirement
  by minimizing the population of "half-applied" states.
- Performance: check phase is fast (no mutation, read-only checks); doing
  it for all modules upfront costs little and can run entirely offline-
  safe checks before any slow package download starts.
- Maintainability: gives module authors a very clean mental model —
  "check is called on everyone before apply is called on anyone" — which
  is simple to document (as done in `docs/architecture.md` Section 3.2)
  and simple to test (a bats integration test can assert zero mutation
  occurred when any one module's check is made to fail).
- Scalability: cost of the check phase scales linearly with module count,
  same as any option; no disadvantage here.
- Compatibility: no platform-specific concern.
- Operational complexity: an operator gets one clear signal — "preflight
  failed, nothing changed" (exit 4) — vs. having to figure out how far a
  partially-applied run got. This is a materially simpler support
  conversation than "which of these 5 modules half-ran."
- Long-term implications: makes `omes check` (the no-mutation command) and
  the check phase of `omes install` literally the same code path, so
  there is only one implementation of "is this profile installable here"
  to maintain and keep correct.

### Option B: Interleaved — check module N, then immediately apply/verify module N, then move to module N+1

- Security: weaker upfront guarantee — module 1 can fully apply before
  module 4's precondition failure is ever discovered, so a run can still
  leave a host partially mutated purely because of an unrelated module
  later in the list. This is a real regression against "no destructive
  defaults" for multi-module profiles.
- Performance: comparable total cost to Option A; the difference is
  ordering, not amount of work.
- Maintainability: slightly simpler runner logic (single loop, no
  separate phases), but pushes more responsibility onto operators/
  authors to reason about "what state is the host in if module K's check
  fails" per profile, since the answer now depends on how many modules
  before K already fully applied.
- Scalability: no difference.
- Compatibility: no difference.
- Operational complexity: worse — "how far did it get" becomes a
  per-incident investigation rather than a structural guarantee ("nothing
  happened" is either true for the whole run or not, under Option A).
- Long-term implications: `omes check` (standalone) and the interleaved
  install's per-module check are no longer the same code path/guarantee
  ("standalone check never mutates" vs. "install's check for module N
  precedes module N's own mutation, but says nothing about module N+1..M"),
  which is a subtler, easier-to-regress contract to maintain over time.

### Option C: Apply all modules first, verify all at the end

- Security: worst option for "no destructive defaults" — every module
  mutates before any verification happens, so a broken module 1 might not
  be caught until after modules 2-5 have also already applied on top of
  it, potentially compounding the failure (e.g. module 3 depending on
  module 1's now-known-broken output).
- Performance: comparable total cost.
- Maintainability: verification failures become harder to attribute
  cleanly to a root cause when several applies have already stacked on
  top of each other before any verify runs.
- Scalability: no difference.
- Compatibility: no difference.
- Operational complexity: the worst rollback story of the three — a
  late-discovered verify failure could implicate several already-applied
  modules, several of which might now need `module_rollback`, in an order
  the operator has to reconstruct rather than the runner having already
  stopped at the first failure.
- Long-term implications: directly conflicts with the fail-fast principle
  and with `MODULE_REQUIRES` ordering guarantees — if module B requires
  module A and both apply before either verifies, module B's apply ran
  against an *unverified* module A, undermining the point of having a
  `verify` step at all ahead of dependents.

## Decision

Adopt check-all-then-apply, exactly as specified in
`docs/architecture.md` Section 3.2: every module's `module_check` runs
before any module's `module_apply`; any check failure aborts the entire
`install` with exit 4 and zero mutation. Within the apply phase, modules
run one at a time, in `MODULE_REQUIRES`-resolved order, each followed
immediately by its own `module_verify`; the run stops at the first
apply/verify failure (fail-fast) rather than attempting all applies
before any verify.

## Consequences

- `omes install`'s check phase and standalone `omes check` share
  identical semantics and, in the implementation, the same underlying
  loop over `module_check` — this must be kept true as the codebase
  evolves (a regression here — e.g. `omes install`'s check phase silently
  diverging from `omes check` — is a defect against this ADR).
- A profile with N modules where module K's `module_check` fails leaves
  the host completely untouched, regardless of K's position in the list.
- A profile where module K's `module_apply`/`module_verify` fails leaves
  modules `1..K-1` applied and module K in `status=failed`; modules
  `K+1..N` never run in that invocation. The operator must re-run
  `install` (after fixing the cause) or explicitly invoke rollback/restore
  — this is documented, not silent, behavior (`docs/architecture.md`
  Section 3.2, step 7f).
- `MODULE_REQUIRES` ordering (ADR-driven by the contract in
  `docs/architecture.md` Section 4.4) is what makes "verify a dependency
  before applying its dependent" true under this model — a cycle is
  rejected before either phase begins (exit 2).
