#!/usr/bin/env bats
# tests/unit/release-ci-gate.bats - the release CI gate of scripts/release.sh
# (issue #315): it must FAIL CLOSED. The workflows are manual-only (issue #312),
# so a commit nobody dispatched them on has zero check runs, and zero failures
# must never read as a pass. Evidence is either GitHub check runs for every
# blocking job (a fake `gh`, tests/shims/gh) or a strictly validated
# scripts/ci-local.sh --profile full --json evidence file.
#
# The sandbox repository carries the REAL .github/workflows, scripts/ci-local.sh
# and scripts/release-ci-gate.py, so the required job names are derived from the
# real workflows exactly as a release does. No Docker, network or token is used.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  WORK_REPO="${OMES_TEST_TMPDIR}/work_repo"
  GEN="${OMES_TEST_TMPDIR}/gen.py"
  CHECKS_FILE="${OMES_TEST_TMPDIR}/check-runs.json"
  export SHIM_GH_CHECK_RUNS_FILE="$CHECKS_FILE"
  export SHIM_GH_STATE_DIR="${OMES_TEST_TMPDIR}"
  unset OMES_CI_WORKFLOWS_DIR || true

  mkdir -p "$WORK_REPO"
  git -C "$WORK_REPO" init -q -b main
  git -C "$WORK_REPO" config user.email "release-test@example.invalid"
  git -C "$WORK_REPO" config user.name "Release Tester"
  git -C "$WORK_REPO" config gc.auto 0
  git -C "$WORK_REPO" config gc.autoDetach false
  git -C "$WORK_REPO" config maintenance.auto false

  mkdir -p "$WORK_REPO/scripts" "$WORK_REPO/changes" "$WORK_REPO/.github"
  cp "${OMES_TEST_ROOT}/scripts/release.sh" "${OMES_TEST_ROOT}/scripts/ci-local.sh" \
    "${OMES_TEST_ROOT}/scripts/release-ci-gate.py" "$WORK_REPO/scripts/"
  chmod +x "$WORK_REPO"/scripts/*
  cp -R "${OMES_TEST_ROOT}/.github/workflows" "$WORK_REPO/.github/workflows"
  printf '/.omes-ci/\n' >"$WORK_REPO/.gitignore"
  echo "0.2.0" >"$WORK_REPO/VERSION"
  printf '# OMES\n**Pre-alpha, version `0.2.0`.**\n' >"$WORK_REPO/README.md"
  cat >"$WORK_REPO/CHANGELOG.md" <<'EOF'
# Changelog

## [0.2.0](https://github.com/ahliweb/omes/releases/tag/v0.2.0) - 2026-09-20

### Added
- Initial baseline release.
EOF
  cat >"$WORK_REPO/changes/100-test.md" <<'EOF'
---
issue: 100
type: added
---
Test feature description.
EOF
  git -C "$WORK_REPO" add -A
  git -C "$WORK_REPO" commit -q -m "initial commit"
  git -C "$WORK_REPO" tag -a "v0.2.0" -m "OMES v0.2.0"
  SHA="$(git -C "$WORK_REPO" rev-parse HEAD)"

  write_gen
  make_path_without_gh
}

teardown() {
  omes_test_teardown
}

# write_gen - a generator for fake check-runs responses and local CI evidence,
# driven by the real `ci-local.sh --list --json --profile full` job table.
#   gen.py checks   OUT SHA JOBS_JSON [opt...]
#   gen.py evidence OUT SHA JOBS_JSON [opt...]
write_gen() {
  cat >"$GEN" <<'EOF'
import datetime
import json
import sys

kind, out, sha, jobs_path = sys.argv[1:5]
opts = dict(a.split("=", 1) if "=" in a else (a, "1") for a in sys.argv[5:])
jobs = [j for j in json.load(open(jobs_path))["jobs"] if j["selected"]]

if kind == "checks":
    runs = []
    next_id = 1
    for j in jobs:
        if j["check_name"] is None:
            continue
        name = j["check_name"]
        if name == opts.get("drop"):
            continue
        if not j["blocking"] and "advisory_fail" not in opts:
            continue
        status, conclusion = "completed", "success"
        if not j["blocking"]:
            conclusion = "failure"
        if name == opts.get("fail"):
            conclusion = "failure"
        if name == opts.get("cancel"):
            conclusion = "cancelled"
        if name == opts.get("pending"):
            status, conclusion = "in_progress", None
        run = {
            "id": next_id,
            "name": name,
            "head_sha": opts.get("head_sha", sha),
            "status": status,
            "conclusion": conclusion,
            "app": {"slug": opts.get("app", "github-actions")},
        }
        next_id += 1
        runs.append(run)
    if "only_gitguardian" in opts:
        runs = []
    if "only_gitguardian" in opts or "with_gitguardian" in opts or "gg_conclusion" in opts:
        # GitGuardian is a GitHub App: it posts a successful check run on every
        # commit by itself, so the run set is never empty even when nobody
        # dispatched the manual workflows.
        runs.append(
            {
                "id": 9000,
                "name": "GitGuardian Security Checks",
                "head_sha": sha,
                "status": "completed",
                "conclusion": opts.get("gg_conclusion", "success"),
                "app": {"slug": "gitguardian"},
            }
        )
    if "pending_other" in opts:
        runs.append(
            {
                "id": 9001,
                "name": "Some Other App Check",
                "head_sha": sha,
                "status": "in_progress",
                "conclusion": None,
                "app": {"slug": "some-other-app"},
            }
        )
    json.dump({"total_count": len(runs), "check_runs": runs}, open(out, "w"))
    sys.exit(0)

jobs_out = []
for j in jobs:
    status = "pass" if j["blocking"] else "advisory_fail"
    if j["name"] == opts.get("drop"):
        continue
    if j["name"] == opts.get("skip"):
        status = "skipped"
    if j["name"] == opts.get("fail"):
        status = "fail"
    jobs_out.append(
        {
            "name": j["name"],
            "workflow": j["workflow"],
            "workflow_job": j["workflow_job"],
            "blocking": j["blocking"],
            "status": status,
            "reason": "",
            "duration_seconds": 1,
            "command": j["command"],
        }
    )

counts = {"pass": 0, "fail": 0, "advisory_fail": 0, "skipped": 0, "blocking_skipped": 0}
for j in jobs_out:
    counts[j["status"]] += 1
    if j["status"] == "skipped" and j["blocking"]:
        counts["blocking_skipped"] += 1

stamp = datetime.datetime.now(datetime.timezone.utc)
if "future" in opts:
    stamp += datetime.timedelta(days=2)
doc = {
    "schema": "omes-ci-local/1",
    "result": opts.get("result", "pass"),
    "exit_code": 0,
    "generated_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "profile": opts.get("profile", "full"),
    "fail_fast": False,
    "duration_seconds": 5,
    "git": {
        "head": opts.get("head", sha),
        "dirty": opts.get("dirty", "false") == "true",
        "shallow": opts.get("shallow", "false") == "true",
    },
    "mirror": None,
    "extra_ca": False,
    "proxy_variables_passed": [],
    "counts": counts,
    "jobs": jobs_out,
}
if "no_timestamp" in opts:
    del doc["generated_at"]
if "result_only" in opts:
    # an incomplete run: result says so, and the counts/exit code agree
    doc["result"] = "incomplete"
    doc["exit_code"] = 3
text = json.dumps(doc, indent=2)
if "duplicate_result" in opts:
    text = text.replace('"result": "pass",', '"result": "fail", "result": "pass",')
if "truncate" in opts:
    text = text[: len(text) // 2]
open(out, "w").write(text)
EOF
}

# make_path_without_gh - NO_GH_PATH: the tools release.sh and the gate need,
# and neither `gh` (even when the host has one) nor the tests/shims directory.
make_path_without_gh() {
  NO_GH_PATH="${OMES_TEST_TMPDIR}/nogh-bin"
  mkdir -p "$NO_GH_PATH"
  local t p
  for t in bash sh env git python3 mktemp rm mkdir tr sed grep head tail cat readlink dirname basename awk find \
    sort cut wc tee id stat chmod mv cp ln touch sleep uname date true false test xargs sed ls printf; do
    p="$(PATH="/usr/local/bin:/usr/bin:/bin" command -v "$t" || true)"
    [[ -n "$p" ]] && ln -sf "$p" "${NO_GH_PATH}/${t}"
  done
}

# jobs_json - the required-job table of the sandbox, as the gate derives it.
jobs_json() {
  JOBS_JSON="${OMES_TEST_TMPDIR}/jobs.json"
  (cd "$WORK_REPO" && bash scripts/ci-local.sh --list --json --profile full >"$JOBS_JSON" 2>/dev/null)
}

# checks [opt...] - write $CHECKS_FILE (a fake gh api response) for $SHA.
checks() {
  jobs_json
  python3 "$GEN" checks "$CHECKS_FILE" "$SHA" "$JOBS_JSON" "$@"
}

# evidence [opt...] - write the default evidence file for $SHA.
evidence() {
  jobs_json
  mkdir -p "$WORK_REPO/.omes-ci"
  EVIDENCE="$WORK_REPO/.omes-ci/ci-local-${SHA}.json"
  python3 "$GEN" evidence "$EVIDENCE" "$SHA" "$JOBS_JSON" "$@"
}

# release [args...] - run the sandbox release.sh with the fake gh on PATH.
release() {
  run bash -c 'cd "$1" && shift && exec bash scripts/release.sh "$@"' _ "$WORK_REPO" 0.3.0 --skip-bundle "$@"
}

# release_no_gh [args...] - the same with a PATH that has no gh at all.
release_no_gh() {
  run env PATH="$NO_GH_PATH" bash -c 'cd "$1" && shift && exec bash scripts/release.sh "$@"' _ "$WORK_REPO" 0.3.0 --skip-bundle "$@"
}

assert_not_released() {
  [ "$(cat "$WORK_REPO/VERSION")" = "0.2.0" ]
  [ -f "$WORK_REPO/changes/100-test.md" ]
  if git -C "$WORK_REPO" rev-parse -q --verify "refs/tags/v0.3.0" >/dev/null; then
    echo "tag v0.3.0 exists but the release must not have been cut" >&2
    return 1
  fi
}

# refute_gh_called - fail when the fake gh was invoked at all (a bare `! grep`
# never fails a bats test, so spell the check out).
refute_gh_called() {
  if grep -q '^gh ' "$SHIM_LOG"; then
    echo "gh was called but must not have been:" >&2
    cat "$SHIM_LOG" >&2
    return 1
  fi
}

assert_released() {
  [ "$(cat "$WORK_REPO/VERSION")" = "0.3.0" ]
  git -C "$WORK_REPO" rev-parse -q --verify "refs/tags/v0.3.0" >/dev/null
}

# ---------------------------------------------------------------------------
# GitHub check runs
# ---------------------------------------------------------------------------

@test "zero check runs FAIL the gate (the #315 regression: manual-only workflows nobody dispatched)" {
  # no SHIM_GH_CHECK_RUNS_FILE content: the default response has zero runs
  rm -f "$CHECKS_FILE"
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"CI gate FAILED"* ]]
  [[ "$output" == *"has no GitHub check runs"* ]]
  [[ "$output" == *"ShellCheck (severity=warning blocks, style is advisory)"* ]]
  [[ "$output" == *"ubuntu:24.04 (tier 1)"* ]]
  [[ "$output" == *"gh workflow run lint.yml --ref main"* ]]
  [[ "$output" == *"gh workflow run compatibility.yml --ref main"* ]]
  [[ "$output" == *"scripts/ci-local.sh --profile full --json > "* ]]
  [[ "$output" == *"--skip-ci-check"* ]]
  assert_not_released
}

@test "only GitGuardian ran: a successful check run from a GitHub App is not evidence for the blocking jobs" {
  checks only_gitguardian=1
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"CI gate FAILED"* ]]
  [[ "$output" == *"ShellCheck (severity=warning blocks, style is advisory): missing"* ]]
  [[ "$output" == *"Real install + idempotency (ubuntu:24.04, root, apt-base): missing"* ]]
  [[ "$output" == *"gh workflow run lint.yml --ref main"* ]]
  assert_not_released
}

@test "GitGuardian running alongside every required job does not break a passing gate" {
  checks with_gitguardian=1
  release
  [ "$status" -eq 0 ]
  [[ "$output" == *"github-check-runs"* ]]
  assert_released
}

@test "a failing GitGuardian run blocks the release even when every required run is green" {
  checks gg_conclusion=failure
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"CI gate BLOCKED"* ]]
  [[ "$output" == *"GitGuardian Security Checks: completed with conclusion failure (app: gitguardian)"* ]]
  assert_not_released
}

@test "a failing GitGuardian run blocks the release even when valid local evidence exists" {
  evidence
  checks gg_conclusion=failure
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"CI gate BLOCKED"* ]]
  [[ "$output" == *"GitGuardian Security Checks"* ]]
  assert_not_released
}

@test "a timed_out or action_required run from any app blocks the release" {
  local c
  for c in timed_out action_required; do
    checks gg_conclusion="$c"
    release
    [ "$status" -eq 1 ]
    [[ "$output" == *"GitGuardian Security Checks: completed with conclusion ${c}"* ]]
    assert_not_released
  done
}

@test "a failing required job blocks the release even when valid local evidence exists" {
  evidence
  checks "fail=bats unit + integration (tests/run.sh)"
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"CI gate BLOCKED"* ]]
  [[ "$output" == *"bats unit + integration (tests/run.sh): completed with conclusion failure"* ]]
  assert_not_released
}

@test "a failing run does not block --skip-ci-check, which is the explicit opt-out" {
  checks gg_conclusion=failure
  release --skip-ci-check
  [ "$status" -eq 0 ]
  assert_released
}

@test "an in-progress run that is not a required job does not block but warns" {
  checks pending_other=1
  release
  [ "$status" -eq 0 ]
  [[ "$output" == *"CI gate passed"* ]]
  [[ "$output" == *"CI gate: Some Other App Check: not finished (status: in_progress"* ]]
  assert_released
}

@test "an in-progress non-required run does not rescue a missing required run" {
  checks pending_other=1 "drop=actionlint"
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"actionlint: missing"* ]]
  assert_not_released
}

@test "an empty check-runs list written as a real response also fails" {
  python3 - "$CHECKS_FILE" <<'EOF'
import json, sys
json.dump({"total_count": 0, "check_runs": []}, open(sys.argv[1], "w"))
EOF
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"has no GitHub check runs"* ]]
  assert_not_released
}

@test "every required check run successful passes and the release is cut" {
  checks
  release
  [ "$status" -eq 0 ]
  [[ "$output" == *"CI gate passed"* ]]
  [[ "$output" == *"github-check-runs"* ]]
  assert_released
  grep -q '^gh api --paginate repos/:owner/:repo/commits/'"$SHA"'/check-runs' "$SHIM_LOG"
}

@test "one required check run missing fails and names it" {
  checks "drop=gitleaks (full-history secret scan)"
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"gitleaks (full-history secret scan): missing"* ]]
  assert_not_released
}

@test "a missing matrix or real-install check run fails" {
  checks "drop=Real install + idempotency (ubuntu:24.04, root, apt-base)"
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"Real install + idempotency (ubuntu:24.04, root, apt-base): missing"* ]]
  assert_not_released

  checks "drop=ubuntu:26.04 (tier 1)"
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"ubuntu:26.04 (tier 1): missing"* ]]
  assert_not_released
}

@test "one required check run failed fails" {
  checks "fail=bats unit + integration (tests/run.sh)"
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"bats unit + integration (tests/run.sh): completed with conclusion failure"* ]]
  assert_not_released
}

@test "one required check run cancelled fails" {
  checks "cancel=yamllint (workflows)"
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"yamllint (workflows): completed with conclusion cancelled"* ]]
  assert_not_released
}

@test "one required check run still in progress fails" {
  checks "pending=actionlint"
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"actionlint: not finished (status: in_progress)"* ]]
  assert_not_released
}

@test "advisory check runs failing while every required one succeeded still passes" {
  checks advisory_fail=1
  python3 - "$CHECKS_FILE" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
names = {r["name"]: r["conclusion"] for r in d["check_runs"]}
assert names["shfmt -i 2 -ci -bn (non-blocking)"] == "failure", names
assert names["ubuntu:22.04 (tier 2)"] == "failure", names
EOF
  release
  [ "$status" -eq 0 ]
  [[ "$output" == *"CI gate passed"* ]]
  assert_released
}

@test "check runs created by an app other than GitHub Actions are not evidence" {
  checks app=some-other-app
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"not GitHub Actions runs"* ]]
  assert_not_released
}

@test "check runs for a different commit are not evidence" {
  checks head_sha=0000000000000000000000000000000000000000
  release
  [ "$status" -eq 1 ]
  assert_not_released
}

@test "a failing gh api call is no evidence and is not mistaken for a pass" {
  checks
  SHIM_GH_API_EXIT=1 release
  [ "$status" -eq 1 ]
  [[ "$output" == *"could not query GitHub check runs"* ]]
  assert_not_released
}

@test "a truncated GitHub response fails closed" {
  checks
  python3 - "$CHECKS_FILE" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
d["total_count"] = len(d["check_runs"]) + 5
json.dump(d, open(sys.argv[1], "w"))
EOF
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"incomplete"* ]]
  assert_not_released
}

@test "an inherited OMES_CI_WORKFLOWS_DIR cannot shrink the required job set" {
  mkdir -p "${OMES_TEST_TMPDIR}/narrow"
  printf 'name: Narrow\non:\n  workflow_dispatch:\njobs:\n  only:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: true\n' \
    >"${OMES_TEST_TMPDIR}/narrow/lint.yml"
  rm -f "$CHECKS_FILE"
  OMES_CI_WORKFLOWS_DIR="${OMES_TEST_TMPDIR}/narrow" release
  [ "$status" -eq 1 ]
  [[ "$output" == *"ShellCheck (severity=warning blocks, style is advisory)"* ]]
  assert_not_released
}

@test "workflow files that differ from the release commit are refused" {
  checks
  echo "# local edit" >>"$WORK_REPO/.github/workflows/lint.yml"
  release --allow-dirty
  [ "$status" -eq 1 ]
  [[ "$output" == *"differ from commit"* ]]
  assert_not_released
}

# ---------------------------------------------------------------------------
# gh absent
# ---------------------------------------------------------------------------

@test "gh absent with no local evidence FAILS (a missing gh must not skip the gate)" {
  release_no_gh
  [ "$status" -eq 1 ]
  [[ "$output" == *"CI gate FAILED"* ]]
  [[ "$output" == *"'gh' is not installed"* ]]
  [[ "$output" == *"no local CI evidence file"* ]]
  assert_not_released
}

@test "gh absent with valid local evidence passes" {
  evidence
  release_no_gh
  [ "$status" -eq 0 ]
  [[ "$output" == *"CI gate passed"* ]]
  [[ "$output" == *"local-evidence"* ]]
  assert_released
}

@test "valid local evidence also passes when gh is present but the commit has zero check runs" {
  evidence
  rm -f "$CHECKS_FILE"
  release
  [ "$status" -eq 0 ]
  [[ "$output" == *"local-evidence"* ]]
  assert_released
}

@test "--ci-evidence PATH reads evidence from a custom (relative) path" {
  evidence
  mv "$EVIDENCE" "${OMES_TEST_TMPDIR}/custom-evidence.json"
  run env PATH="$NO_GH_PATH" bash -c 'cd "$1" && cd .. && exec bash work_repo/scripts/release.sh 0.3.0 --skip-bundle --ci-evidence custom-evidence.json' _ "$WORK_REPO"
  [ "$status" -eq 0 ]
  [[ "$output" == *"local-evidence"* ]]
  assert_released
}

# ---------------------------------------------------------------------------
# Local evidence variants (every one of them is no evidence)
# ---------------------------------------------------------------------------

assert_evidence_rejected() {
  release_no_gh
  [ "$status" -eq 1 ]
  [[ "$output" == *"CI gate FAILED"* ]]
  [[ "$output" == *"$1"* ]]
  assert_not_released
}

@test "local evidence for another commit is rejected" {
  evidence head=1111111111111111111111111111111111111111
  assert_evidence_rejected "evidence is for commit 111111111111"
}

@test "local evidence from a dirty tree is rejected" {
  evidence dirty=true
  assert_evidence_rejected "working tree was not clean"
}

@test "local evidence from a shallow clone is rejected" {
  evidence shallow=true
  assert_evidence_rejected "clone was shallow"
}

@test "local evidence with a skipped blocking job is rejected" {
  evidence skip=bats
  assert_evidence_rejected "blocking job bats is skipped"
}

@test "local evidence with a failed blocking job is rejected" {
  evidence fail=supply-chain
  assert_evidence_rejected "blocking job supply-chain is fail"
}

@test "local evidence whose result is incomplete is rejected" {
  evidence result_only=1
  assert_evidence_rejected "result is 'incomplete'"
}

@test "local evidence missing a matrix job is rejected" {
  evidence drop=matrix:ubuntu:24.04
  assert_evidence_rejected "blocking job matrix:ubuntu:24.04 is missing"
}

@test "local evidence missing a real-install job is rejected" {
  evidence drop=real-install-ubuntu-26-04
  assert_evidence_rejected "blocking job real-install-ubuntu-26-04 is missing"
}

@test "local evidence from the fast profile (no matrix, no real-install) is rejected" {
  evidence profile=fast
  assert_evidence_rejected "profile is 'fast'"
}

@test "malformed (truncated) local evidence JSON is rejected" {
  evidence truncate=1
  assert_evidence_rejected "is not valid evidence"
}

@test "local evidence that is not JSON at all is rejected" {
  mkdir -p "$WORK_REPO/.omes-ci"
  echo "pass" >"$WORK_REPO/.omes-ci/ci-local-${SHA}.json"
  assert_evidence_rejected "is not valid evidence"
}

@test "local evidence with a duplicate key is rejected" {
  evidence duplicate_result=1
  assert_evidence_rejected "duplicate JSON key"
}

@test "local evidence with a timestamp in the future is rejected" {
  evidence future=1
  assert_evidence_rejected "is in the future"
}

@test "local evidence without a timestamp is rejected" {
  evidence no_timestamp=1
  assert_evidence_rejected "generated_at is missing"
}

@test "a rejected evidence file does not stop a valid GitHub source from passing" {
  evidence dirty=true
  checks
  release
  [ "$status" -eq 0 ]
  [[ "$output" == *"github-check-runs"* ]]
  assert_released
}

# ---------------------------------------------------------------------------
# --skip-ci-check and --dry-run
# ---------------------------------------------------------------------------

@test "--skip-ci-check passes with a prominent warning and never queries GitHub" {
  release --skip-ci-check
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARNING: --skip-ci-check is set. The CI gate is SKIPPED."* ]]
  [[ "$output" == *"completed WITHOUT a CI gate"* ]]
  assert_released
  # the skip is visible in the annotated tag
  git -C "$WORK_REPO" tag -l --format='%(contents)' v0.3.0 | grep -q "CI gate: SKIPPED with --skip-ci-check"
  refute_gh_called
}

@test "without --skip-ci-check the annotated tag carries no skip note" {
  checks
  release
  [ "$status" -eq 0 ]
  if git -C "$WORK_REPO" tag -l --format='%(contents)' v0.3.0 | grep -q "SKIPPED"; then
    echo "the tag carries a skip note" >&2
    return 1
  fi
}

@test "--skip-ci-check is recorded in the GitHub Release notes" {
  local bare="${OMES_TEST_TMPDIR}/origin.git"
  git init -q --bare "$bare"
  git -C "$WORK_REPO" remote add origin "$bare"
  export SHIM_GH_NOTES_OUT="${OMES_TEST_TMPDIR}/notes.md"
  release --skip-ci-check --publish
  [ "$status" -eq 0 ]
  assert_released
  grep -q "CI gate: SKIPPED with --skip-ci-check" "$SHIM_GH_NOTES_OUT"
}

@test "--dry-run with zero check runs reports what the gate requires and does not fail or touch the network" {
  release --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"[dry-run] CI gate would currently FAIL"* ]]
  [[ "$output" == *"GitHub check runs are not queried in --dry-run"* ]]
  assert_not_released
  refute_gh_called
}

@test "--dry-run accepts valid local evidence without any network call" {
  evidence
  release --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"CI gate passed"* ]]
  [[ "$output" == *"local-evidence"* ]]
  refute_gh_called
  assert_not_released
}

@test "--dry-run with --skip-ci-check still warns that the gate is skipped" {
  release --dry-run --skip-ci-check
  [ "$status" -eq 0 ]
  [[ "$output" == *"The CI gate is SKIPPED"* ]]
}

@test "a repository without the gate scripts fails closed on a real release and only warns on --dry-run" {
  git -C "$WORK_REPO" rm -q scripts/release-ci-gate.py
  git -C "$WORK_REPO" commit -q -m "remove gate helper"
  release
  [ "$status" -eq 1 ]
  [[ "$output" == *"CI gate cannot be evaluated (failing closed)"* ]]
  assert_not_released

  release --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"[dry-run] CI gate cannot be evaluated here"* ]]
}

@test "--help documents --ci-evidence and the explicit --skip-ci-check opt-out" {
  run bash "$WORK_REPO/scripts/release.sh" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--ci-evidence PATH"* ]]
  [[ "$output" == *"Explicitly skip the CI gate (never the default)"* ]]
  [[ "$output" == *"gh workflow run lint.yml"* ]]
}

@test "--ci-evidence without a value is a usage error" {
  run bash "$WORK_REPO/scripts/release.sh" 0.3.0 --ci-evidence
  [ "$status" -eq 1 ]
  [[ "$output" == *"--ci-evidence requires a path argument"* ]]
}
