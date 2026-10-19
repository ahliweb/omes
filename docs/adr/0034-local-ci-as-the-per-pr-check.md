# ADR-0034 — Local CI as the Per-PR Check; GitHub Actions Manual-Only

- **Status:** Accepted
- **Date:** 2026-10-15
- **Decision maker:** @ahliweb (decision recorded 2026-10-14 in [Issue #312](https://github.com/ahliweb/omes/issues/312))
- **Related:** ADR-0009 (testing with bats in containers), ADR-0010, ADR-0026 (upstream drift automation), [docs/ci.md](../ci.md) §8, §8.1 and §10, [docs/business/release-gates.md](../business/release-gates.md), [Issue #312](https://github.com/ahliweb/omes/issues/312), [Issue #315](https://github.com/ahliweb/omes/issues/315).
- **Supersedes / Amends:** Amends ADR-0009, ADR-0010 and ADR-0026, which assumed CI runs automatically on every pull request, push and weekly schedule. The amendments in those ADRs point here.

---

## Context

Before #312 every check in `.github/workflows/lint.yml` and `compatibility.yml` ran on GitHub-hosted runners on every pull request and push, and `upstream-drift.yml` ran weekly. There was no single command that reproduced those jobs locally. The owner decided that verification should not depend on GitHub Actions.

## Options considered

| Option | Security | Cost / operations | Long-term |
|---|---|---|---|
| A. Keep Actions automatic; local CI only as a pre-push aid | Strongest: GitHub enforces every check | Actions minutes on every push | No change |
| **B. Local CI is the per-PR check; workflows manual-only (`workflow_dispatch`)** | Per-PR results are self-reported, not enforced by GitHub. The GitGuardian App check stays automatic, and the release gate requires SHA-bound evidence (#315) | No Actions minutes unless dispatched | Workflows stay available and unchanged for a release gate or on demand |
| C. Delete the workflows | Weakest: no enforceable path at all | None | Hard to reverse; contradicts AGENTS.md §3 |

## Decision

Option B.

1. `scripts/ci-local.sh` reproduces every workflow job with the same commands, pinned images and blocking rules. A skipped blocking job makes the run `incomplete` (exit 3) and is never reported as a pass. A `workflow-sync` job fails when a workflow job has no local counterpart.
2. `lint.yml`, `compatibility.yml` and `upstream-drift.yml` trigger only on `workflow_dispatch`. Job names, steps, pins and permissions are unchanged, so a dispatch reports the same check runs.
3. Every pull request carries a `scripts/ci-local.sh` summary. That summary includes the commit SHA, a clean-tree flag and a shallow-clone flag.
4. The release gate (`scripts/release.sh`, #315) fails closed. A release needs either a successful GitHub Actions run for every blocking job on the release commit, or a `ci-local.sh --profile full --json` evidence file for exactly that commit. In both cases, any failing check run on the commit blocks the release.

## Consequences

- **Branch protection is outside the repository.** Required status checks that name workflow jobs never report unless someone dispatches the workflow on the PR head. The owner must either update the required checks or dispatch per PR. This is an owner action that no PR can perform.
- **Per-PR results are self-reported.** A reviewer must check that the pasted summary names the PR's head SHA. Only the release gate verifies evidence mechanically.
- **Drift tracking is manual.** `upstream-drift` runs locally in report-only mode. Updating the tracking issue needs a manual dispatch.
- **Dependabot PRs are not checked automatically.** They no longer get automatic check runs.
- **Reversing this decision** means restoring the `pull_request`, `push` and `schedule` triggers, a one-line change per workflow, and removing the bats guard that forbids automatic triggers.
