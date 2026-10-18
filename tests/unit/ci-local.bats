#!/usr/bin/env bats
# tests/unit/ci-local.bats - unit tests for scripts/ci-local.sh (issue #312):
# argument parsing, job listing, workflow drift guard, status semantics
# (a skipped blocking job is `incomplete`, never a pass), JSON shape, the
# scrubbed job environment and the opt-in pre-push hook.
#
# Real tools (ShellCheck, Docker, gitleaks, ...) are never invoked here: jobs
# run against a fake repository whose scripts are stubs, and "no Docker" is
# simulated with a PATH that has none.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  CI_LOCAL="${OMES_TEST_ROOT}/scripts/ci-local.sh"
  FAKE_REPO="${OMES_TEST_TMPDIR}/repo"
  make_fake_repo
  make_path_without_docker
}

teardown() {
  omes_test_teardown
}

# make_fake_repo - a directory laid out like the repository whose scripts are
# stubs that exit 0, so the runner's own logic is what is under test. Create
# scripts/fail-<name> to make a stub exit 1.
make_fake_repo() {
  mkdir -p "${FAKE_REPO}/scripts" "${FAKE_REPO}/tests/graphify" "${FAKE_REPO}/.github"
  cp "$CI_LOCAL" "${FAKE_REPO}/scripts/ci-local.sh"
  chmod +x "${FAKE_REPO}/scripts/ci-local.sh"
  cp -R "${OMES_TEST_ROOT}/.github/workflows" "${FAKE_REPO}/.github/workflows"
  local s
  for s in lint.sh check-supply-chain.sh release.sh test-matrix.sh; do
    cat >"${FAKE_REPO}/scripts/${s}" <<'EOF'
#!/usr/bin/env bash
echo "stub $(basename "$0") $*"
[[ -f "$(dirname "$0")/fail-$(basename "$0")" ]] && exit 1
exit 0
EOF
    chmod +x "${FAKE_REPO}/scripts/${s}"
  done
  for s in check-links.py check-contracts.py check-architecture.py generate-control-center-data.py upstream-drift.py; do
    cat >"${FAKE_REPO}/scripts/${s}" <<'EOF'
import os
import sys

print("stub", os.path.basename(__file__), *sys.argv[1:])
marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fail-" + os.path.basename(__file__))
if os.path.exists(marker):
    sys.exit(int(open(marker).read().strip() or "1"))
EOF
  done
}

# make_path_without_docker - NO_DOCKER_PATH: a PATH holding the tools the
# runner needs but no docker (and no shellcheck/bats/shfmt/yamllint).
make_path_without_docker() {
  NO_DOCKER_PATH="${OMES_TEST_TMPDIR}/nodocker-bin"
  mkdir -p "$NO_DOCKER_PATH"
  local t p
  for t in bash env mktemp rm mkdir tr sed grep head tail cat readlink dirname basename awk find sort cut wc \
    tee id stat python3 git chmod mv cp ln touch sleep uname date true false test; do
    p="$(PATH="/usr/local/bin:/usr/bin:/bin" command -v "$t" || true)"
    [[ -n "$p" ]] && ln -sf "$p" "${NO_DOCKER_PATH}/${t}"
  done
}

# ci [args...] - run the fake repo's runner with only stdout in $output.
ci() {
  omes_run_stdout_only env PATH="$NO_DOCKER_PATH" "${FAKE_REPO}/scripts/ci-local.sh" "$@"
}

# ci_both [args...] - run it with stdout+stderr merged in $output.
ci_both() {
  run env PATH="$NO_DOCKER_PATH" "${FAKE_REPO}/scripts/ci-local.sh" "$@"
}

# refute_grep PATTERN FILE - fail unless PATTERN is absent (a bare `! grep`
# never fails a bats test).
refute_grep() {
  if grep -qE -- "$1" "$2"; then
    echo "unexpected match for '$1' in $2" >&2
    return 1
  fi
}

json_get() {
  python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
for key in sys.argv[1].split("."):
    d = d[int(key)] if key.isdigit() else d[key]
print(d if not isinstance(d, bool) else str(d).lower())
' "$1" <<<"$output"
}

job_status() {
  python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
print(next(j["status"] for j in d["jobs"] if j["name"] == sys.argv[1]))
' "$1" <<<"$output"
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

@test "--help prints usage and the exit-code table, exits 0" {
  ci_both --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"usage: scripts/ci-local.sh"* ]]
  [[ "$output" == *"3 incomplete"* ]]
}

@test "an unknown option exits 2" {
  ci_both --frobnicate
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown option: --frobnicate"* ]]
}

@test "an invalid --profile exits 2" {
  ci_both --profile turbo --list
  [ "$status" -eq 2 ]
  [[ "$output" == *"invalid --profile"* ]]
}

@test "--job without a value exits 2" {
  ci_both --job
  [ "$status" -eq 2 ]
}

@test "an unknown --job exits 2 and points at --list" {
  ci_both --job no-such-job
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown job 'no-such-job'"* ]]
  [[ "$output" == *"--list"* ]]
}

@test "--keep-going and --fail-fast are mutually exclusive (exit 2)" {
  ci_both --keep-going --fail-fast --list
  [ "$status" -eq 2 ]
}

@test "hook options cannot be combined with job selection (exit 2)" {
  ci_both --install-hook --job check-links
  [ "$status" -eq 2 ]
}

@test "--job accepts a comma-separated list and is repeatable" {
  ci --job check-links,check-contracts --job supply-chain --json
  [ "$status" -eq 0 ]
  run python3 -c 'import json,sys; print(",".join(j["name"] for j in json.loads(sys.stdin.read())["jobs"]))' <<<"$output"
  [ "$output" = "check-links,check-contracts,supply-chain" ]
}

# ---------------------------------------------------------------------------
# Listing and the workflow drift guard
# ---------------------------------------------------------------------------

@test "--list prints every lint.yml job with its blocking status" {
  ci --list
  [ "$status" -eq 0 ]
  local j
  for j in shellcheck shfmt bats gitleaks yamllint actionlint supply-chain check-links check-contracts \
    check-architecture check-control-center-data validate-change-fragments workflow-sync; do
    [[ "$output" == *"$j"* ]]
  done
  [[ "$output" =~ shfmt[[:space:]]+lint.yml[[:space:]]+fast[[:space:]]+advisory ]]
}

@test "--list --json is valid JSON naming workflow, profile, blocking and command per job" {
  ci --list --json
  [ "$status" -eq 0 ]
  run python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
assert d["schema"] == "omes-ci-local/1"
for j in d["jobs"]:
    for k in ("name", "workflow", "workflow_job", "profile", "blocking", "selected", "command"):
        assert k in j, (j, k)
    assert isinstance(j["blocking"], bool) and isinstance(j["selected"], bool)
by = {j["name"]: j for j in d["jobs"]}
assert by["shfmt"]["blocking"] is False
assert by["shellcheck"]["blocking"] is True
assert by["shellcheck"]["command"] == "scripts/lint.sh shellcheck"
' <<<"$output"
  [ "$status" -eq 0 ]
}

@test "the default profile is fast: lint jobs selected, compatibility and opt-in jobs not" {
  ci --list --json
  run python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
sel = {j["name"] for j in d["jobs"] if j["selected"]}
assert "shellcheck" in sel and "workflow-sync" in sel and "bats" in sel
assert not any(n.startswith("matrix") or n.startswith("real-install") for n in sel), sel
assert "graphify-real-smoke" not in sel and "upstream-drift" not in sel
' <<<"$output"
  [ "$status" -eq 0 ]
}

@test "--profile full and --with-matrix select the compatibility jobs, never the opt-in ones" {
  local flag
  for flag in "--profile full" "--with-matrix"; do
    # shellcheck disable=SC2086
    ci --list --json $flag
    run python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
sel = {j["name"] for j in d["jobs"] if j["selected"]}
assert any(n.startswith("matrix:") for n in sel), sel
assert "real-install-ubuntu-24-04" in sel and "real-install-ubuntu-26-04" in sel
assert "graphify-real-smoke" not in sel and "upstream-drift" not in sel
' <<<"$output"
    [ "$status" -eq 0 ]
  done
}

@test "the matrix expands to the images (and advisory flags) in compatibility.yml" {
  ci --list --json
  local listed
  listed="$(python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
print(sorted((j["image"], j["blocking"]) for j in d["jobs"] if j["name"].startswith("matrix:")))
' <<<"$output")"
  local expected
  expected="$(python3 - "${OMES_TEST_ROOT}/.github/workflows/compatibility.yml" <<'EOF'
import re, sys
text = open(sys.argv[1]).read()
rows = re.findall(r'- image: "([^"]+)"\s+tier: "(\d)"\s+advisory: (true|false)', text)
assert len(rows) >= 4, rows
print(sorted((i, a == "false") for i, _, a in rows))
EOF
)"
  [ -n "$expected" ]
  [ "$listed" = "$expected" ]
}

@test "every job of every workflow has a local counterpart (independent of the runner's own check)" {
  ci --list --json
  printf '%s' "$output" >"${OMES_TEST_TMPDIR}/list.json"
  run python3 - "${OMES_TEST_ROOT}/.github/workflows" "${OMES_TEST_TMPDIR}/list.json" <<'EOF'
import json, pathlib, re, sys
listing = json.load(open(sys.argv[2]))
have = {(j["workflow"], j["workflow_job"]) for j in listing["jobs"]}
missing = []
total = 0
for wf in sorted(pathlib.Path(sys.argv[1]).glob("*.yml")):
    jobs = wf.read_text().split("\njobs:\n", 1)[1]
    for m in re.finditer(r"^  ([A-Za-z0-9_-]+):\s*(#.*)?$", jobs, re.M):
        total += 1
        if (wf.name, m.group(1)) not in have:
            missing.append((wf.name, m.group(1)))
assert total >= 17, total  # 12 lint + 5 compatibility/upstream-drift jobs at the time of writing
assert not missing, missing
EOF
  [ "$status" -eq 0 ]
}

@test "workflow-sync passes on the repository's own workflows" {
  run env PATH="$NO_DOCKER_PATH" "${CI_LOCAL}" --job workflow-sync --json
  [ "$status" -eq 0 ]
}

@test "workflow-sync fails (exit 1) when a workflow gains a job with no local counterpart" {
  local wf="${OMES_TEST_TMPDIR}/wf"
  cp -R "${FAKE_REPO}/.github/workflows" "$wf"
  cat >>"${wf}/lint.yml" <<'EOF'

  brand-new-job:
    name: brand new
    runs-on: ubuntu-24.04
    steps:
      - name: noop
        run: echo hi
EOF
  OMES_CI_WORKFLOWS_DIR="$wf" ci --job workflow-sync --json
  [ "$status" -eq 1 ]
  [ "$(job_status workflow-sync)" = "fail" ]
  [ "$(json_get result)" = "fail" ]
  [[ "$(json_get jobs.0.reason)" == *"drift error"* ]]
}

@test "workflow-sync names the unmapped job on stderr" {
  local wf="${OMES_TEST_TMPDIR}/wf"
  cp -R "${FAKE_REPO}/.github/workflows" "$wf"
  printf '\n  brand-new-job:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo hi\n' >>"${wf}/lint.yml"
  OMES_CI_WORKFLOWS_DIR="$wf" ci_both --job workflow-sync
  [ "$status" -eq 1 ]
  [[ "$output" == *"lint.yml: job 'brand-new-job' has no local counterpart"* ]]
}

@test "workflow-sync fails when a local job no longer exists in the workflow" {
  local wf="${OMES_TEST_TMPDIR}/wf"
  cp -R "${FAKE_REPO}/.github/workflows" "$wf"
  # Rename the yamllint job id: the registry row now points at nothing.
  sed -i 's/^  yamllint:$/  yamllint-renamed:/' "${wf}/lint.yml"
  OMES_CI_WORKFLOWS_DIR="$wf" ci_both --job workflow-sync
  [ "$status" -eq 1 ]
  [[ "$output" == *"has no job 'yamllint'"* ]]
  [[ "$output" == *"job 'yamllint-renamed' has no local counterpart"* ]]
}

@test "workflow-sync fails when blocking semantics drift (continue-on-error removed from shfmt)" {
  local wf="${OMES_TEST_TMPDIR}/wf"
  cp -R "${FAKE_REPO}/.github/workflows" "$wf"
  sed -i '/^    continue-on-error: true$/d' "${wf}/lint.yml"
  OMES_CI_WORKFLOWS_DIR="$wf" ci_both --job workflow-sync
  [ "$status" -eq 1 ]
  [[ "$output" == *"shfmt: workflow says blocking"* ]]
}

@test "workflow-sync fails when a workflow command changes" {
  local wf="${OMES_TEST_TMPDIR}/wf"
  cp -R "${FAKE_REPO}/.github/workflows" "$wf"
  sed -i 's#run: ./scripts/lint.sh yamllint#run: ./scripts/lint.sh yamllint --strict#' "${wf}/lint.yml"
  OMES_CI_WORKFLOWS_DIR="$wf" ci_both --job workflow-sync
  [ "$status" -eq 1 ]
  [[ "$output" == *"yamllint: workflow does not run 'scripts/lint.sh yamllint'"* ]]
}

@test "the workflows are manual-only: workflow_dispatch is the single trigger (issue #312)" {
  run python3 - "${OMES_TEST_ROOT}/.github/workflows" <<'EOF'
import pathlib, re, sys
seen = 0
for wf in sorted(pathlib.Path(sys.argv[1]).glob("*.yml")):
    lines = wf.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if re.match(r"^on:\s*$", l))
    triggers = []
    for l in lines[start + 1:]:
        if l and not l.startswith((" ", "#")):
            break
        m = re.match(r"^  ([a-z_]+):", l)
        if m:
            triggers.append(m.group(1))
    assert triggers == ["workflow_dispatch"], (wf.name, triggers)
    seen += 1
assert seen >= 3, seen
EOF
  [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# Status semantics
# ---------------------------------------------------------------------------

@test "all selected blocking jobs passing exits 0 with result pass" {
  ci --job check-links --job check-contracts --job check-architecture --job supply-chain --json
  [ "$status" -eq 0 ]
  [ "$(json_get result)" = "pass" ]
  [ "$(json_get exit_code)" = "0" ]
  [ "$(json_get counts.pass)" = "4" ]
}

@test "a failing blocking job exits 1 with result fail" {
  printf '1' >"${FAKE_REPO}/scripts/fail-check-contracts.py"
  ci --job check-contracts --job check-links --json
  [ "$status" -eq 1 ]
  [ "$(json_get result)" = "fail" ]
  [ "$(job_status check-contracts)" = "fail" ]
  [ "$(job_status check-links)" = "pass" ]
}

@test "keep-going is the default: jobs after a failure still run" {
  printf '1' >"${FAKE_REPO}/scripts/fail-check-contracts.py"
  ci --job check-contracts --job check-links --job supply-chain --json
  [ "$status" -eq 1 ]
  [ "$(json_get counts.pass)" = "2" ]
  [ "$(json_get counts.fail)" = "1" ]
}

@test "--fail-fast stops after the first blocking failure and marks the rest skipped" {
  printf '1' >"${FAKE_REPO}/scripts/fail-check-contracts.py"
  ci --fail-fast --job check-contracts --job check-links --job supply-chain --json
  [ "$status" -eq 1 ]
  [ "$(json_get result)" = "fail" ]
  [ "$(json_get fail_fast)" = "true" ]
  [ "$(job_status check-links)" = "skipped" ]
  [[ "$(json_get jobs.1.reason)" == *"--fail-fast after check-contracts failed"* ]]
}

@test "an advisory failure is reported as advisory_fail and does not fail the run" {
  # A local shfmt makes the shfmt job runnable without Docker; the stub
  # scripts/lint.sh then fails for shfmt.
  mkdir -p "${OMES_TEST_TMPDIR}/fakebin"
  printf '#!/usr/bin/env bash\nexit 0\n' >"${OMES_TEST_TMPDIR}/fakebin/shfmt"
  chmod +x "${OMES_TEST_TMPDIR}/fakebin/shfmt"
  printf '#!/usr/bin/env bash\necho "stub lint.sh $*"\n[[ "$1" == shfmt ]] && exit 1\nexit 0\n' >"${FAKE_REPO}/scripts/lint.sh"
  omes_run_stdout_only env PATH="${OMES_TEST_TMPDIR}/fakebin:${NO_DOCKER_PATH}" "${FAKE_REPO}/scripts/ci-local.sh" --job shfmt --json
  [ "$status" -eq 0 ]
  [ "$(json_get result)" = "pass" ]
  [ "$(job_status shfmt)" = "advisory_fail" ]
  [ "$(json_get counts.advisory_fail)" = "1" ]
}

@test "a skipped BLOCKING job (no Docker) makes the run incomplete: exit 3, never a pass" {
  ci --job actionlint --json
  [ "$status" -eq 3 ]
  [ "$(json_get result)" = "incomplete" ]
  [ "$(json_get exit_code)" = "3" ]
  [ "$(job_status actionlint)" = "skipped" ]
  [ "$(json_get counts.pass)" = "0" ]
  [ "$(json_get counts.blocking_skipped)" = "1" ]
  [[ "$(json_get jobs.0.reason)" == *"docker not found"* ]]
}

@test "an unreachable Docker daemon skips blocking Docker jobs (exit 3)" {
  export SHIM_DOCKER_VERSION_EXIT=1
  omes_run_stdout_only env PATH="${OMES_TEST_ROOT}/tests/shims:${NO_DOCKER_PATH}" SHIM_DOCKER_VERSION_EXIT=1 \
    "${FAKE_REPO}/scripts/ci-local.sh" --job actionlint --json
  [ "$status" -eq 3 ]
  [ "$(job_status actionlint)" = "skipped" ]
  [[ "$(json_get jobs.0.reason)" == *"daemon not reachable"* ]]
}

@test "skipped jobs next to passing ones still yield incomplete, and failures beat incompleteness" {
  ci --job check-links --job actionlint --json
  [ "$status" -eq 3 ]
  [ "$(job_status check-links)" = "pass" ]

  printf '1' >"${FAKE_REPO}/scripts/fail-check-contracts.py"
  ci --job check-contracts --job actionlint --json
  [ "$status" -eq 1 ]
  [ "$(json_get result)" = "fail" ]
}

@test "a skipped ADVISORY job does not make the run incomplete" {
  ci --job graphify-real-smoke --json
  [ "$status" -eq 0 ]
  [ "$(job_status graphify-real-smoke)" = "skipped" ]
  [ "$(json_get result)" = "pass" ]
}

@test "the opt-in upstream-drift job runs report-only: exit 2 from the script means skipped, not failed" {
  printf '2' >"${FAKE_REPO}/scripts/fail-upstream-drift.py"
  ci --job upstream-drift --json
  [ "$status" -eq 0 ]
  [ "$(job_status upstream-drift)" = "skipped" ]
  [[ "$(json_get jobs.0.reason)" == *"upstream inspection blocked"* ]]
}

@test "upstream-drift never passes --update-issue and never receives a token" {
  export GH_TOKEN="ghp_fake_token_for_test" GITHUB_TOKEN="fake_github_token_for_test"
  ci_both --job upstream-drift
  [ "$status" -eq 0 ]
  # the stub echoes its argv: exactly the script name, so no --update-issue and no --token
  grep -qxF "stub upstream-drift.py" <<<"$output"
  [[ "$output" == *"report only"* ]]
}

@test "environment failures are told apart from code failures (classify_env_failure)" {
  local log="${OMES_TEST_TMPDIR}/job.log"

  printf '%s\n' 'not ok 12 bootstrap fails on unsupported platform' \
    '# (from function `setup'"'"' in test file tests/unit/bootstrap.bats, line 23)' \
    '# /code/tests/unit/bootstrap.bats: line 23: git: command not found' >"$log"
  run bash -c "source '${CI_LOCAL}'; classify_env_failure bats '${log}'"
  [[ "$output" == "environment: the bats container image lacks git/python3"* ]]

  printf '%s\n' 'Error response from daemon: toomanyrequests: You have reached your pull rate limit.' >"$log"
  run bash -c "source '${CI_LOCAL}'; classify_env_failure actionlint '${log}'"
  [[ "$output" == "environment: registry rate limit"* ]]

  printf '%s\n' 'Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?' >"$log"
  run bash -c "source '${CI_LOCAL}'; classify_env_failure 'matrix:ubuntu:24.04' '${log}'"
  [[ "$output" == "environment: the Docker daemon is not reachable" ]]

  # A genuine failure, or the same words in a job that does not use containers, is left alone.
  printf '%s\n' 'not ok 7 something real' '# expected 0, got 1' >"$log"
  run bash -c "source '${CI_LOCAL}'; classify_env_failure bats '${log}'"
  [ -z "$output" ]
  printf '%s\n' 'Cannot connect to the Docker daemon' >"$log"
  run bash -c "source '${CI_LOCAL}'; classify_env_failure check-links '${log}'"
  [ -z "$output" ]
}

# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------

@test "--json prints exactly one JSON document on stdout; logs stay on stderr" {
  ci --job check-links --json
  [ "$status" -eq 0 ]
  python3 -c 'import json,sys; json.loads(sys.stdin.read())' <<<"$output"
  [[ "$output" != *"[ci-local]"* ]]
  [[ "$output" != *"stub check-links"* ]]
}

@test "the JSON summary has the documented shape" {
  ci --job check-links --job actionlint --json
  run python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
for k in ("schema", "result", "exit_code", "profile", "fail_fast", "duration_seconds", "git", "mirror",
          "extra_ca", "proxy_variables_passed", "counts", "jobs"):
    assert k in d, k
assert d["profile"] == "custom"
assert set(d["counts"]) == {"pass", "fail", "advisory_fail", "skipped", "blocking_skipped"}
for j in d["jobs"]:
    assert set(j) == {"name", "workflow", "workflow_job", "blocking", "status", "reason", "duration_seconds", "command"}, j
    assert j["status"] in ("pass", "fail", "advisory_fail", "skipped")
    assert isinstance(j["duration_seconds"], int)
assert set(d["git"]) == {"head", "dirty", "shallow"}
' <<<"$output"
  [ "$status" -eq 0 ]
}

@test "the human summary is a Markdown table on stdout that can be pasted into a PR" {
  ci --job check-links --job actionlint
  [ "$status" -eq 3 ]
  [[ "$output" == *"## Local CI summary (scripts/ci-local.sh)"* ]]
  [[ "$output" == *"| job | blocking | status | seconds | note |"* ]]
  [[ "$output" == *"| check-links | blocking | pass |"* ]]
  [[ "$output" == *"| actionlint | blocking | skipped |"* ]]
  [[ "$output" == *"INCOMPLETE"* ]]
}

@test "--log-dir keeps one log per job" {
  ci --job check-links --log-dir "${OMES_TEST_TMPDIR}/logs"
  [ "$status" -eq 0 ]
  [ -s "${OMES_TEST_TMPDIR}/logs/check-links.log" ]
  grep -q "stub check-links.py" "${OMES_TEST_TMPDIR}/logs/check-links.log"
}

@test "a relative --log-dir is resolved against the starting directory, not the repository" {
  mkdir -p "${OMES_TEST_TMPDIR}/cwd"
  cd "${OMES_TEST_TMPDIR}/cwd"
  ci --job check-links --log-dir logs-here
  [ "$status" -eq 0 ]
  [ -s "${OMES_TEST_TMPDIR}/cwd/logs-here/check-links.log" ]
  [ ! -e "${FAKE_REPO}/logs-here" ]
}

@test "the temp directory is removed when the run ends" {
  mkdir -p "${OMES_TEST_TMPDIR}/tmp"
  TMPDIR="${OMES_TEST_TMPDIR}/tmp" ci --job check-links --json
  [ "$status" -eq 0 ]
  [ "$(json_get counts.pass)" = "1" ]
  [ -z "$(ls -A "${OMES_TEST_TMPDIR}/tmp")" ]
}

# ---------------------------------------------------------------------------
# Job environment
# ---------------------------------------------------------------------------

@test "jobs run without credentials or OMES_* variables, but keep OMES_CI_* settings" {
  cat >"${FAKE_REPO}/scripts/check-supply-chain.sh" <<'EOF'
#!/usr/bin/env bash
env | sort >"${ENV_DUMP:?}"
EOF
  export ENV_DUMP="${OMES_TEST_TMPDIR}/job-env.txt"
  export GH_TOKEN="x-gh" GITHUB_TOKEN="x-github" GITLEAKS_LICENSE="x-license" OMES_STATE_DIR="/tmp/should-not-leak" OMES_CI_NONROOT_UID=4242
  ci --job supply-chain --json
  [ "$status" -eq 0 ]
  refute_grep '^GH_TOKEN=' "$ENV_DUMP"
  refute_grep '^GITHUB_TOKEN=' "$ENV_DUMP"
  refute_grep '^GITLEAKS_LICENSE=' "$ENV_DUMP"
  refute_grep '^OMES_STATE_DIR=' "$ENV_DUMP"
  grep -q '^OMES_CI_NONROOT_UID=4242$' "$ENV_DUMP"
}

# ---------------------------------------------------------------------------
# Opt-in pre-push hook
# ---------------------------------------------------------------------------

make_hook_repo() {
  HOOK_REPO="${OMES_TEST_TMPDIR}/hookrepo"
  mkdir -p "${HOOK_REPO}/scripts"
  cp "$CI_LOCAL" "${HOOK_REPO}/scripts/ci-local.sh"
  git -C "$HOOK_REPO" init -q -b main
  git -C "$HOOK_REPO" config user.email "ci-local-test@example.invalid"
  git -C "$HOOK_REPO" config user.name "CI Local Test"
  # Never let git spawn detached auto-gc/maintenance after a commit or worktree
  # add: it keeps writing into .git after the test body returns and races the
  # teardown's rm -rf (the same guard tests/unit/release.bats uses).
  git -C "$HOOK_REPO" config gc.auto 0
  git -C "$HOOK_REPO" config gc.autoDetach false
  git -C "$HOOK_REPO" config maintenance.auto false
  git -C "$HOOK_REPO" add -A
  git -C "$HOOK_REPO" commit -q -m "initial"
}

@test "the hook is never installed by a normal run" {
  make_hook_repo
  run env PATH="$NO_DOCKER_PATH" "${HOOK_REPO}/scripts/ci-local.sh" --job workflow-sync
  [ ! -e "${HOOK_REPO}/.git/hooks/pre-push" ]
}

@test "--install-hook writes an executable pre-push hook that runs the fast profile" {
  make_hook_repo
  run env PATH="$NO_DOCKER_PATH" "${HOOK_REPO}/scripts/ci-local.sh" --install-hook
  [ "$status" -eq 0 ]
  [ -x "${HOOK_REPO}/.git/hooks/pre-push" ]
  grep -qF "omes-ci-local: managed pre-push hook" "${HOOK_REPO}/.git/hooks/pre-push"
  grep -q 'scripts/ci-local.sh" --profile fast' "${HOOK_REPO}/.git/hooks/pre-push"
}

@test "the installed hook runs the fast profile and passes its exit code through (a failing run blocks the push)" {
  make_hook_repo
  env PATH="$NO_DOCKER_PATH" "${HOOK_REPO}/scripts/ci-local.sh" --install-hook
  # Swap in a stub runner that reports how it was called, then fire the hook the
  # way git does (cwd = repository root, remote name and URL as arguments).
  printf '#!/usr/bin/env bash\necho "ARGS: $*"\nexit 3\n' >"${HOOK_REPO}/scripts/ci-local.sh"
  chmod +x "${HOOK_REPO}/scripts/ci-local.sh"
  cd "$HOOK_REPO"
  run env PATH="$NO_DOCKER_PATH" "${HOOK_REPO}/.git/hooks/pre-push" origin https://example.invalid/repo.git
  [ "$status" -eq 3 ]
  [[ "$output" == *"ARGS: --profile fast"* ]]
}

@test "the installed hook does nothing (and does not block) in a checkout without the runner" {
  make_hook_repo
  env PATH="$NO_DOCKER_PATH" "${HOOK_REPO}/scripts/ci-local.sh" --install-hook
  rm -f "${HOOK_REPO}/scripts/ci-local.sh"
  cd "$HOOK_REPO"
  run env PATH="$NO_DOCKER_PATH" "${HOOK_REPO}/.git/hooks/pre-push" origin https://example.invalid/repo.git
  [ "$status" -eq 0 ]
  [[ "$output" == *"scripts/ci-local.sh not found"* ]]
}

@test "--install-hook is idempotent for its own hook" {
  make_hook_repo
  env PATH="$NO_DOCKER_PATH" "${HOOK_REPO}/scripts/ci-local.sh" --install-hook
  run env PATH="$NO_DOCKER_PATH" "${HOOK_REPO}/scripts/ci-local.sh" --install-hook
  [ "$status" -eq 0 ]
  [ -x "${HOOK_REPO}/.git/hooks/pre-push" ]
}

@test "--install-hook refuses to overwrite a hook that is not its own" {
  make_hook_repo
  mkdir -p "${HOOK_REPO}/.git/hooks"
  printf '#!/bin/sh\necho custom hook\n' >"${HOOK_REPO}/.git/hooks/pre-push"
  chmod +x "${HOOK_REPO}/.git/hooks/pre-push"
  run env PATH="$NO_DOCKER_PATH" "${HOOK_REPO}/scripts/ci-local.sh" --install-hook
  [ "$status" -eq 1 ]
  [[ "$output" == *"refusing to overwrite"* ]]
  grep -q "custom hook" "${HOOK_REPO}/.git/hooks/pre-push"
}

@test "--uninstall-hook removes its own hook, leaves a foreign one, and is a no-op when absent" {
  make_hook_repo
  run env PATH="$NO_DOCKER_PATH" "${HOOK_REPO}/scripts/ci-local.sh" --uninstall-hook
  [ "$status" -eq 0 ]

  env PATH="$NO_DOCKER_PATH" "${HOOK_REPO}/scripts/ci-local.sh" --install-hook
  run env PATH="$NO_DOCKER_PATH" "${HOOK_REPO}/scripts/ci-local.sh" --uninstall-hook
  [ "$status" -eq 0 ]
  [ ! -e "${HOOK_REPO}/.git/hooks/pre-push" ]

  mkdir -p "${HOOK_REPO}/.git/hooks"
  printf '#!/bin/sh\necho custom hook\n' >"${HOOK_REPO}/.git/hooks/pre-push"
  run env PATH="$NO_DOCKER_PATH" "${HOOK_REPO}/scripts/ci-local.sh" --uninstall-hook
  [ "$status" -eq 1 ]
  [ -e "${HOOK_REPO}/.git/hooks/pre-push" ]
}

@test "the hook installs from a linked worktree (where .git is a file) into the shared hooks dir" {
  make_hook_repo
  git -C "$HOOK_REPO" worktree add -q "${OMES_TEST_TMPDIR}/wt" -b wt-branch
  [ -f "${OMES_TEST_TMPDIR}/wt/.git" ]
  run env PATH="$NO_DOCKER_PATH" "${OMES_TEST_TMPDIR}/wt/scripts/ci-local.sh" --install-hook
  [ "$status" -eq 0 ]
  [ -x "${HOOK_REPO}/.git/hooks/pre-push" ]
}

@test "the hook goes where git looks for it when core.hooksPath is set" {
  make_hook_repo
  git -C "$HOOK_REPO" config core.hooksPath .githooks
  run env PATH="$NO_DOCKER_PATH" "${HOOK_REPO}/scripts/ci-local.sh" --install-hook
  [ "$status" -eq 0 ]
  [ -x "${HOOK_REPO}/.githooks/pre-push" ]
  [ ! -e "${HOOK_REPO}/.git/hooks/pre-push" ]
}

@test "the runner works from a linked worktree (.git is a file)" {
  make_hook_repo
  git -C "$HOOK_REPO" worktree add -q "${OMES_TEST_TMPDIR}/wt2" -b wt2-branch
  cp -R "${OMES_TEST_ROOT}/.github" "${OMES_TEST_TMPDIR}/wt2/.github"
  run env PATH="$NO_DOCKER_PATH" "${OMES_TEST_TMPDIR}/wt2/scripts/ci-local.sh" --job workflow-sync --json
  [ "$status" -eq 0 ]
}

@test "the runner contains no pipe-to-shell download and no GitHub API call" {
  refute_grep '\b(curl|wget)\b[^|]*\|[[:space:]]*(sudo[[:space:]]+)?(bash|sh)([[:space:]]|$)' "$CI_LOCAL"
  refute_grep 'api\.github\.com|gh api|gh auth|/repos/' "$CI_LOCAL"
}
