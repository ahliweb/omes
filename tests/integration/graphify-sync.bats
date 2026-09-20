#!/usr/bin/env bats
# tests/integration/graphify-sync.bats - `omes graphify sync`/`status` end
# to end against bin/omes, executing the real lib/omes/py/graphify/cli.py
# (issue #54). Like tests/integration/graphify-export.bats, this needs a
# real system python3 (stdlib json/hashlib) - no fake python3 shim on
# PATH.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"

  unset SHIM_GRAPHIFY_EXTRACT_FAIL SHIM_GRAPHIFY_UPDATE_FAIL SHIM_GRAPHIFY_UPDATE_CORRUPT \
    OBSIDIAN_VAULT_PATH OMES_GRAPHIFY_PROJECT_NAME || true

  WORK="${OMES_TEST_TMPDIR}/work"
  mkdir -p "${WORK}/repo/src"
  printf 'print(1)\n' >"${WORK}/repo/src/a.py"
  REPO="${WORK}/repo"
}

teardown() {
  omes_test_teardown
}

@test "status on a never-synced path reports no_manifest" {
  run "$OMES_BIN" graphify status "$REPO" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"status":"no_manifest"'* ]]
}

@test "sync --dry-run performs no extraction and writes no manifest" {
  run "$OMES_BIN" graphify sync "$REPO" --dry-run
  [ "$status" -eq 0 ]
  [ ! -d "${REPO}/graphify-out" ]
  run grep -c '^graphify extract' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "first sync runs extract, writes graph.json and a manifest" {
  run "$OMES_BIN" graphify sync "$REPO" --yes --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"action":"extract"'* ]]
  [ -f "${REPO}/graphify-out/graph.json" ]
  [ -f "${REPO}/graphify-out/omes-sync.json" ]
}

@test "status after a first sync reports up_to_date" {
  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  run "$OMES_BIN" graphify status "$REPO" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"status":"up_to_date"'* ]]
}

@test "a second sync with no changes takes no action and does not call graphify again" {
  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  : >"$SHIM_LOG"
  run "$OMES_BIN" graphify sync "$REPO" --yes --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"action":"none"'* ]]
  run grep -c '^graphify extract\|^graphify update' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "a change triggers 'graphify update' (not a full extract) on the second sync" {
  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  printf 'print(2)\n' >>"${REPO}/src/a.py"
  run "$OMES_BIN" graphify sync "$REPO" --yes --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"action":"update"'* ]]
  run grep -c '^graphify update' "$SHIM_LOG"
  [ "$output" -ge 1 ]
}

@test "status reports stale after a source change, before the next sync" {
  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  printf 'print(2)\n' >>"${REPO}/src/a.py"
  run "$OMES_BIN" graphify status "$REPO" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"status":"stale"'* ]]
}

@test "sync --min-interval debounces a rapid repeat call without invoking graphify" {
  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  printf 'print(2)\n' >>"${REPO}/src/a.py"
  : >"$SHIM_LOG"
  run "$OMES_BIN" graphify sync "$REPO" --min-interval 3600 --yes --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'skipped_debounced'* ]]
  run grep -c '^graphify update\|^graphify extract' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "sync ignores its own graphify-out/ directory (no loop)" {
  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  run "$OMES_BIN" graphify status "$REPO" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"status":"up_to_date"'* ]]
}

@test "a vault nested inside the source path is refused without --allow-nested-vault" {
  mkdir -p "${REPO}/vault/.obsidian"
  run "$OMES_BIN" graphify sync "$REPO" --vault "${REPO}/vault" --yes
  [ "$status" -eq 2 ]
  [ ! -d "${REPO}/graphify-out" ]
}

@test "--allow-nested-vault permits a nested vault and still excludes it from the scan" {
  mkdir -p "${REPO}/vault/.obsidian"
  run "$OMES_BIN" graphify sync "$REPO" --vault "${REPO}/vault" --allow-nested-vault --yes --json
  [ "$status" -eq 0 ]
  run "$OMES_BIN" graphify status "$REPO" --vault "${REPO}/vault" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"status":"up_to_date"'* ]]
}

@test "a failed first-run extract leaves no partial graphify-out/ behind" {
  SHIM_GRAPHIFY_EXTRACT_FAIL=1 run "$OMES_BIN" graphify sync "$REPO" --yes --json
  [ "$status" -eq 1 ]
  [ ! -d "${REPO}/graphify-out" ]
}

@test "an update that leaves a corrupt graph.json is recovered from the pre-sync backup" {
  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  local before
  before="$(cat "${REPO}/graphify-out/graph.json")"

  printf 'print(2)\n' >>"${REPO}/src/a.py"
  SHIM_GRAPHIFY_UPDATE_CORRUPT=1 run "$OMES_BIN" graphify sync "$REPO" --yes --json
  [ "$status" -eq 1 ]

  local after
  after="$(cat "${REPO}/graphify-out/graph.json")"
  [ "$before" = "$after" ]
  run python3 -m json.tool <<<"$after"
  [ "$status" -eq 0 ]
}

@test "an update command failure (nonzero exit) is also recovered" {
  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  local before
  before="$(cat "${REPO}/graphify-out/graph.json")"

  printf 'print(2)\n' >>"${REPO}/src/a.py"
  SHIM_GRAPHIFY_UPDATE_FAIL=1 run "$OMES_BIN" graphify sync "$REPO" --yes --json
  [ "$status" -eq 1 ]

  local after
  after="$(cat "${REPO}/graphify-out/graph.json")"
  [ "$before" = "$after" ]
}

@test "sync refuses a path that is itself a graphify-out/ directory" {
  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  run "$OMES_BIN" graphify sync "${REPO}/graphify-out" --yes
  [ "$status" -eq 2 ]
}

@test "sync without a path is a usage error" {
  run "$OMES_BIN" graphify sync
  [ "$status" -eq 2 ]
}

@test "sync refuses when graphify is not installed" {
  # The static shim always exists on PATH (command -v graphify succeeds
  # regardless of SHIM_GRAPHIFY_ABSENT), so "not installed" is simulated
  # the same way tests/integration/graphify.bats does for `run`: hide the
  # shim from PATH entirely.
  local stripped="" dir
  IFS=':' read -ra parts <<<"$PATH"
  for dir in "${parts[@]}"; do
    [[ -x "${dir}/graphify" ]] && continue
    stripped="${stripped:+${stripped}:}${dir}"
  done
  PATH="$stripped" run "$OMES_BIN" graphify sync "$REPO" --yes
  [ "$status" -eq 2 ]
  [[ "$output" == *"not installed"* ]]
}

@test "--json emits a single JSON object on stdout for sync" {
  omes_run_stdout_only "$OMES_BIN" graphify sync "$REPO" --yes --json
  [ "$status" -eq 0 ]
  printf '%s' "$output" | python3 -m json.tool >/dev/null
}

@test "--json emits a single JSON object on stdout for status" {
  omes_run_stdout_only "$OMES_BIN" graphify status "$REPO" --json
  [ "$status" -eq 0 ]
  printf '%s' "$output" | python3 -m json.tool >/dev/null
}
