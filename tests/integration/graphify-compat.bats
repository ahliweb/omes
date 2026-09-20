#!/usr/bin/env bats
# tests/integration/graphify-compat.bats - compatibility/security
# assertions specific to issue #56, exercised against bin/omes with
# tests/shims/{graphify,hermes,graphify-mcp}: no provider credential is
# ever read in code-only mode (canary env var), malformed Markdown /
# symlinks / large files / ignored paths are handled safely, and the
# Hermes CLI + optional MCP health checks both work together.
#
# Fresh/repeat install, upgrade, and uninstall of the graphify CLI itself
# are already covered by tests/integration/graphify.bats; interrupted-run
# recovery is already covered by tests/integration/graphify-sync.bats.
# This file adds the remaining, not-yet-covered issue #56 criteria rather
# than duplicating those.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"

  unset OBSIDIAN_VAULT_PATH OMES_GRAPHIFY_MAX_FILE_MB SHIM_HERMES_VERSION \
    SHIM_HERMES_DOCTOR_EXIT OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY \
    OMES_GRAPHIFY_PROVIDER_ENV || true

  WORK="${OMES_TEST_TMPDIR}/work"
  mkdir -p "${WORK}/repo/src"
  printf 'print(1)\n' >"${WORK}/repo/src/a.py"
  REPO="${WORK}/repo"
}

teardown() {
  omes_test_teardown
}

# ---------------------------------------------------------------------------
# Code-only extraction without provider credentials (canary)
# ---------------------------------------------------------------------------

@test "omes graphify run (code-only default) never reads a provider credential (canary)" {
  export OPENAI_API_KEY=canary
  export ANTHROPIC_API_KEY=canary
  export GEMINI_API_KEY=canary
  run "$OMES_BIN" graphify run "$REPO" --json
  [ "$status" -eq 0 ]

  # The canary value must never leak into the shim invocation log, the
  # provenance sidecar, or command output.
  run grep -c "canary" "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run grep -rc "canary" "${REPO}/graphify-out/omes-provenance.json"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "omes graphify sync never reads a provider credential (canary)" {
  export OPENAI_API_KEY=canary
  run "$OMES_BIN" graphify sync "$REPO" --yes --json
  [ "$status" -eq 0 ]
  run grep -c "canary" "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "omes graphify run --mode semantic requires the pointer variable, canary alone is not enough" {
  export OPENAI_API_KEY=canary
  run "$OMES_BIN" graphify run "$REPO" --mode semantic --yes
  [ "$status" -eq 2 ]
  [[ "$output" == *"OMES_GRAPHIFY_PROVIDER_ENV"* ]]
}

# ---------------------------------------------------------------------------
# Malformed Markdown
# ---------------------------------------------------------------------------

@test "a malformed pre-existing Markdown note (truncated front matter) is treated as a safe conflict, never crashes" {
  local vault="${WORK}/vault"
  mkdir -p "${vault}/.obsidian"
  "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  local export_dir="${vault}/graphify/$(basename "$REPO")"
  mkdir -p "$export_dir"
  # Front matter opened but never closed - has_omes_marker must not crash
  # on this, and must treat it as "no marker" (never overwritten).
  printf -- '---\nomes_generated: true\nsource: "whatever"\n' >"${export_dir}/app.py.md"

  run "$OMES_BIN" graphify export "${REPO}/graphify-out" --vault "$vault" --yes --json
  [ "$status" -eq 0 ]
  run grep -c "omes_generated" "${export_dir}/app.py.md"
  [ "$output" -eq 1 ]
}

# ---------------------------------------------------------------------------
# Symlinks
# ---------------------------------------------------------------------------

@test "a symlinked source file is skipped by sync's change detection, not followed" {
  printf 'print(2)\n' >"${WORK}/outside.py"
  ln -s "${WORK}/outside.py" "${REPO}/src/linked.py"
  run "$OMES_BIN" graphify sync "$REPO" --yes --json
  [ "$status" -eq 0 ]

  # Modifying the symlink target must never register as a source change.
  printf 'print(3)\n' >>"${WORK}/outside.py"
  run "$OMES_BIN" graphify status "$REPO" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"status":"up_to_date"'* ]]
}

@test "a symlinked source directory is skipped entirely, not followed" {
  mkdir -p "${WORK}/outside-dir"
  printf 'print(1)\n' >"${WORK}/outside-dir/x.py"
  ln -s "${WORK}/outside-dir" "${REPO}/linked-dir"
  run "$OMES_BIN" graphify sync "$REPO" --yes --json
  [ "$status" -eq 0 ]
  [ -d "${REPO}/graphify-out" ]
}

# ---------------------------------------------------------------------------
# Large files (size cap)
# ---------------------------------------------------------------------------

@test "OMES_GRAPHIFY_MAX_FILE_MB excludes an oversized file from change detection" {
  # 2 MiB file, 1 MB cap.
  head -c 2097152 /dev/zero >"${REPO}/src/huge.bin" 2>/dev/null || {
    python3 -c "open('${REPO}/src/huge.bin','wb').write(b'0' * 2097152)"
  }
  OMES_GRAPHIFY_MAX_FILE_MB=1 run "$OMES_BIN" graphify sync "$REPO" --yes >/dev/null
  rm -f "${REPO}/src/huge.bin"

  # With the oversized file gone, status must still see the tree as
  # up to date - proving it was never tracked as a source file in the
  # manifest in the first place.
  OMES_GRAPHIFY_MAX_FILE_MB=1 run "$OMES_BIN" graphify status "$REPO" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"status":"up_to_date"'* ]]
}

# ---------------------------------------------------------------------------
# Ignored paths
# ---------------------------------------------------------------------------

@test "sync honors .graphifyignore in addition to .gitignore" {
  "$OMES_BIN" graphify init-ignore "$REPO" --yes >/dev/null
  mkdir -p "${REPO}/node_modules/pkg"
  printf 'module.exports = {}\n' >"${REPO}/node_modules/pkg/index.js"

  run "$OMES_BIN" graphify sync "$REPO" --yes --json
  [ "$status" -eq 0 ]

  # Changing an ignored file must never register as a source change.
  printf 'module.exports = { changed: true }\n' >"${REPO}/node_modules/pkg/index.js"
  run "$OMES_BIN" graphify status "$REPO" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"status":"up_to_date"'* ]]
}

# ---------------------------------------------------------------------------
# Hermes CLI + optional MCP health checks together
# ---------------------------------------------------------------------------

@test "Hermes skill install and graphify-mcp health both work together without touching each other" {
  export SHIM_HERMES_VERSION="1.0.0"
  export SHIM_HERMES_DOCTOR_EXIT=0

  run "$OMES_BIN" graphify skill install --yes --json
  [ "$status" -eq 0 ]
  [ -f "${HOME}/.hermes/skills/graphify/SKILL.md" ]
  [ -f "${HOME}/.hermes/skills/graphify/run.sh" ]

  # tests/shims/graphify-mcp is always present on PATH (command -v
  # succeeds regardless of "installed" state, like every other shim in
  # this suite), so simulate "not installed" the same way
  # tests/integration/graphify.bats does: hide it from PATH entirely.
  local stripped="" dir
  IFS=':' read -ra parts <<<"$PATH"
  for dir in "${parts[@]}"; do
    [[ -x "${dir}/graphify-mcp" ]] && continue
    stripped="${stripped:+${stripped}:}${dir}"
  done
  PATH="$stripped" run "$OMES_BIN" graphify mcp health --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"status":"not_applicable"'* ]]

  # Neither command ever touches systemctl / hermes-gateway.
  run grep -c "systemctl" "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}
