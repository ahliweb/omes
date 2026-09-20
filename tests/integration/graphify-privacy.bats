#!/usr/bin/env bats
# tests/integration/graphify-privacy.bats - `omes graphify init-ignore`/
# `purge` end to end against bin/omes (issue #55). Needs a real system
# python3 (stdlib json), like tests/integration/graphify-export.bats and
# tests/integration/graphify-sync.bats.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"

  unset OBSIDIAN_VAULT_PATH OMES_GRAPHIFY_VAULT_SUBDIR OMES_GRAPHIFY_PROJECT_NAME || true

  WORK="${OMES_TEST_TMPDIR}/work"
  mkdir -p "${WORK}/repo/src"
  printf 'print(1)\n' >"${WORK}/repo/src/a.py"
  REPO="${WORK}/repo"
}

teardown() {
  omes_test_teardown
}

# ---------------------------------------------------------------------------
# init-ignore
# ---------------------------------------------------------------------------

@test "init-ignore --dry-run writes nothing" {
  run "$OMES_BIN" graphify init-ignore "$REPO" --dry-run
  [ "$status" -eq 0 ]
  [ ! -f "${REPO}/.graphifyignore" ]
  [ ! -f "${REPO}/.gitignore" ]
}

@test "init-ignore creates .graphifyignore with secrets patterns and updates .gitignore" {
  run "$OMES_BIN" graphify init-ignore "$REPO" --yes
  [ "$status" -eq 0 ]
  [ -f "${REPO}/.graphifyignore" ]
  run grep -c '^\.env$' "${REPO}/.graphifyignore"
  [ "$output" -ge 1 ]
  run grep -c 'node_modules/' "${REPO}/.graphifyignore"
  [ "$output" -ge 1 ]
  [ -f "${REPO}/.gitignore" ]
  run grep -c '^graphify-out/$' "${REPO}/.gitignore"
  [ "$output" -ge 1 ]
}

@test "init-ignore is idempotent: a second run changes nothing" {
  "$OMES_BIN" graphify init-ignore "$REPO" --yes >/dev/null
  local sum1_ignore sum1_git
  sum1_ignore="$(sha256sum "${REPO}/.graphifyignore" | awk '{print $1}')"
  sum1_git="$(sha256sum "${REPO}/.gitignore" | awk '{print $1}')"

  run "$OMES_BIN" graphify init-ignore "$REPO" --yes --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"action":"none"'* ]]

  local sum2_ignore sum2_git
  sum2_ignore="$(sha256sum "${REPO}/.graphifyignore" | awk '{print $1}')"
  sum2_git="$(sha256sum "${REPO}/.gitignore" | awk '{print $1}')"
  [ "$sum1_ignore" = "$sum2_ignore" ]
  [ "$sum1_git" = "$sum2_git" ]
}

@test "init-ignore preserves an operator's pre-existing .gitignore content" {
  printf '*.log\n' >"${REPO}/.gitignore"
  run "$OMES_BIN" graphify init-ignore "$REPO" --yes
  [ "$status" -eq 0 ]
  run grep -c '\*\.log' "${REPO}/.gitignore"
  [ "$output" -ge 1 ]
  run grep -c 'graphify-out/' "${REPO}/.gitignore"
  [ "$output" -ge 1 ]
}

@test "init-ignore backs up a pre-existing .graphifyignore before appending to it" {
  printf '# my custom rules\ncustom-dir/\n' >"${REPO}/.graphifyignore"
  run "$OMES_BIN" graphify init-ignore "$REPO" --yes
  [ "$status" -eq 0 ]
  run grep -c 'custom-dir/' "${REPO}/.graphifyignore"
  [ "$output" -ge 1 ]
  run grep -c 'node_modules/' "${REPO}/.graphifyignore"
  [ "$output" -ge 1 ]
}

@test "--json emits a single JSON object on stdout for init-ignore" {
  omes_run_stdout_only "$OMES_BIN" graphify init-ignore "$REPO" --yes --json
  [ "$status" -eq 0 ]
  printf '%s' "$output" | python3 -m json.tool >/dev/null
}

# ---------------------------------------------------------------------------
# purge
# ---------------------------------------------------------------------------

@test "purge on a never-synced path is a no-op" {
  run "$OMES_BIN" graphify purge "$REPO" --yes --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"action":"none"'* ]]
}

@test "purge --dry-run removes nothing" {
  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  run "$OMES_BIN" graphify purge "$REPO" --dry-run
  [ "$status" -eq 0 ]
  [ -d "${REPO}/graphify-out" ]
}

@test "purge removes graphify-out/ entirely" {
  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  [ -d "${REPO}/graphify-out" ]
  run "$OMES_BIN" graphify purge "$REPO" --yes
  [ "$status" -eq 0 ]
  [ ! -d "${REPO}/graphify-out" ]
  [ -f "${REPO}/src/a.py" ]
}

@test "purge with --vault removes only OMES-generated notes, never a user-authored one" {
  VAULT="${WORK}/vault"
  mkdir -p "${VAULT}/.obsidian"

  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  "$OMES_BIN" graphify export "${REPO}/graphify-out" --vault "$VAULT" --yes >/dev/null

  local export_dir="${VAULT}/graphify/$(basename "$REPO")"
  [ -d "$export_dir" ]
  printf 'my own note\n' >"${export_dir}/unrelated.md"

  run "$OMES_BIN" graphify purge "$REPO" --vault "$VAULT" --yes --json
  [ "$status" -eq 0 ]

  [ -f "${export_dir}/unrelated.md" ]
  run grep -c 'my own note' "${export_dir}/unrelated.md"
  [ "$output" -eq 1 ]

  # No OMES-generated file remains.
  run find "$export_dir" -name '*.md' -not -name 'unrelated.md'
  [ -z "$output" ]
}

@test "purge is idempotent: a second run finds nothing left to remove" {
  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  "$OMES_BIN" graphify purge "$REPO" --yes >/dev/null
  run "$OMES_BIN" graphify purge "$REPO" --yes --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"action":"none"'* ]]
}

@test "purge backs up before deleting (restorable via omes restore)" {
  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  local before
  before="$(cat "${REPO}/graphify-out/graph.json")"

  run "$OMES_BIN" graphify purge "$REPO" --yes --json
  [ "$status" -eq 0 ]
  [ ! -d "${REPO}/graphify-out" ]

  run "$OMES_BIN" restore --yes
  [ "$status" -eq 0 ]
  [ -f "${REPO}/graphify-out/graph.json" ]
  local after
  after="$(cat "${REPO}/graphify-out/graph.json")"
  [ "$before" = "$after" ]
}

@test "--json emits a single JSON object on stdout for purge" {
  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  omes_run_stdout_only "$OMES_BIN" graphify purge "$REPO" --yes --json
  [ "$status" -eq 0 ]
  printf '%s' "$output" | python3 -m json.tool >/dev/null
}
