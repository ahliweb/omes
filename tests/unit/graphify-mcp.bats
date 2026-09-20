#!/usr/bin/env bats
# tests/unit/graphify-mcp.bats - modules/graphify-mcp/module.sh unit tests.
#
# Sources lib/omes/*.sh and modules/graphify-mcp/module.sh directly
# (module_load), like tests/unit/graphify.bats. HOME is always overridden
# to an isolated tmpdir. tests/shims/{graphify,graphify-mcp,uv,pipx} are
# already on PATH via test_helper.bash's omes_test_setup.
#
# "Not found" simulations for uv/pipx use the same
# OMES_GRAPHIFY_UV_CMD/OMES_GRAPHIFY_PIPX_CMD testability overrides as
# tests/unit/graphify.bats (see that file's header for why PATH-stripping
# is avoided for those two specifically). graphify-mcp's own "not
# installed" state IS simulated by stripping tests/shims/ from PATH
# (safe: that directory never contains real system coreutils, unlike a
# directory such as /usr/bin that might also hold `python3` or `id`).

setup() {
  load '../test_helper.bash'
  omes_test_setup

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"

  unset OMES_GRAPHIFY_VERSION OMES_GRAPHIFY_UV_CMD OMES_GRAPHIFY_PIPX_CMD \
    SHIM_GRAPHIFY_ABSENT SHIM_GRAPHIFY_MCP_HELP_EXIT SHIM_UV_EXIT SHIM_PIPX_EXIT || true

  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/log.sh
  source "${OMES_TEST_ROOT}/lib/omes/log.sh"
  # shellcheck source=../../lib/omes/state.sh
  source "${OMES_TEST_ROOT}/lib/omes/state.sh"
  # shellcheck source=../../lib/omes/backup.sh
  source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
  # shellcheck source=../../lib/omes/detect.sh
  source "${OMES_TEST_ROOT}/lib/omes/detect.sh"
  # shellcheck source=../../lib/omes/module.sh
  source "${OMES_TEST_ROOT}/lib/omes/module.sh"

  module_load graphify-mcp

  declare -ga OMES_MANAGED_PATHS=()
}

teardown() {
  omes_test_teardown
}

# _strip_shims
# Removes tests/shims/ entirely from PATH - safe because that directory
# never contains real system coreutils, only OMES's own test shims.
_strip_shims() {
  local shims="${OMES_TEST_ROOT}/tests/shims"
  local stripped="" dir
  IFS=':' read -ra parts <<<"$PATH"
  for dir in "${parts[@]}"; do
    [[ "$dir" == "$shims" ]] && continue
    stripped="${stripped:+${stripped}:}${dir}"
  done
  PATH="$stripped"
}

# --- module_check ------------------------------------------------------------

@test "module_check refuses to run as root" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  run module_check
  [ "$status" -eq 1 ]
}

@test "module_check fails when the graphify CLI itself is not installed" {
  _strip_shims
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"graphify CLI is not installed"* ]]
}

@test "module_check fails when neither uv nor pipx is present" {
  export OMES_GRAPHIFY_UV_CMD="/nonexistent/uv" OMES_GRAPHIFY_PIPX_CMD="/nonexistent/pipx"
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"neither uv nor pipx found"* ]]
}

@test "module_check reports already installed when graphify-mcp is present" {
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"already installed"* ]]
}

@test "module_check requires network only when graphify-mcp is not yet installed" {
  local shims="${OMES_TEST_ROOT}/tests/shims"
  local filtered="${OMES_TEST_TMPDIR}/shims-without-mcp"
  mkdir -p "$filtered"
  local f base
  for f in "${shims}"/*; do
    base="$(basename "$f")"
    [[ "$base" == "graphify-mcp" ]] && continue
    ln -s "$f" "${filtered}/${base}"
  done
  local rebuilt="" dir
  IFS=':' read -ra parts <<<"$PATH"
  for dir in "${parts[@]}"; do
    [[ "$dir" == "$shims" ]] && continue
    rebuilt="${rebuilt:+${rebuilt}:}${dir}"
  done
  PATH="${filtered}:${rebuilt}"

  export OMES_ASSUME_OFFLINE=1
  unset OMES_ASSUME_ONLINE || true
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"network required"* ]]
}

# --- module_apply --------------------------------------------------------------

@test "module_apply installs graphifyy[mcp] via uv when not yet installed" {
  local shims="${OMES_TEST_ROOT}/tests/shims"
  local filtered="${OMES_TEST_TMPDIR}/shims-without-mcp"
  mkdir -p "$filtered"
  local f base
  for f in "${shims}"/*; do
    base="$(basename "$f")"
    [[ "$base" == "graphify-mcp" ]] && continue
    ln -s "$f" "${filtered}/${base}"
  done
  local rebuilt="" dir
  IFS=':' read -ra parts <<<"$PATH"
  for dir in "${parts[@]}"; do
    [[ "$dir" == "$shims" ]] && continue
    rebuilt="${rebuilt:+${rebuilt}:}${dir}"
  done
  PATH="${filtered}:${rebuilt}"

  run module_apply
  [ "$status" -eq 0 ]
  run grep -c '^uv tool install graphifyy\[mcp\]$' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

@test "module_apply skips install when graphify-mcp is already present" {
  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipping install"* ]]
  run grep -c '^uv tool install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "module_apply never invokes pip install" {
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c 'pip install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "module_apply pins the version spec when OMES_GRAPHIFY_VERSION is set and reinstall is needed" {
  local shims="${OMES_TEST_ROOT}/tests/shims"
  local filtered="${OMES_TEST_TMPDIR}/shims-without-mcp"
  mkdir -p "$filtered"
  local f base
  for f in "${shims}"/*; do
    base="$(basename "$f")"
    [[ "$base" == "graphify-mcp" ]] && continue
    ln -s "$f" "${filtered}/${base}"
  done
  local rebuilt="" dir
  IFS=':' read -ra parts <<<"$PATH"
  for dir in "${parts[@]}"; do
    [[ "$dir" == "$shims" ]] && continue
    rebuilt="${rebuilt:+${rebuilt}:}${dir}"
  done
  PATH="${filtered}:${rebuilt}"

  export OMES_GRAPHIFY_VERSION="0.9.64"
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c '^uv tool install graphifyy\[mcp\]==0.9.64$' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

# --- module_verify --------------------------------------------------------------

@test "module_verify passes when graphify-mcp --help exits 0" {
  run module_verify
  [ "$status" -eq 0 ]
}

@test "module_verify fails when graphify-mcp is not on PATH" {
  _strip_shims
  run module_verify
  [ "$status" -eq 1 ]
}

# --- module_rollback --------------------------------------------------------------

@test "module_rollback reinstalls graphifyy without the mcp extra via uv" {
  run module_rollback
  [ "$status" -eq 0 ]
  [[ "$output" == *"WITHOUT the [mcp] extra"* ]]
  run grep -c '^uv tool install --reinstall graphifyy$' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

@test "module_rollback never touches graphify-out or hermes-gateway" {
  mkdir -p "${OMES_TEST_TMPDIR}/project/graphify-out"
  : >"${OMES_TEST_TMPDIR}/project/graphify-out/graph.json"
  run module_rollback
  [ "$status" -eq 0 ]
  [ -f "${OMES_TEST_TMPDIR}/project/graphify-out/graph.json" ]
  run grep -c 'systemctl\|hermes-gateway' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "module_rollback is a no-op (not a failure) when neither uv nor pipx is present" {
  export OMES_GRAPHIFY_UV_CMD="/nonexistent/uv" OMES_GRAPHIFY_PIPX_CMD="/nonexistent/pipx"
  run module_rollback
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing to reinstall"* ]]
}
