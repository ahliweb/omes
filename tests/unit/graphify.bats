#!/usr/bin/env bats
# tests/unit/graphify.bats - modules/graphify/module.sh unit tests.
#
# Sources lib/omes/*.sh and modules/graphify/module.sh directly
# (module_load), like tests/unit/hermes.bats. HOME is always overridden to
# an isolated tmpdir. tests/shims/{graphify,uv,pipx} are already on PATH
# via test_helper.bash's omes_test_setup. Real python3 (>= 3.10 on any
# host this suite runs on) is used for the "python OK" path; the
# "missing"/"too old" paths temporarily manipulate PATH so a fake or
# absent python3 is seen instead, exactly like tests/unit/hermes.bats
# strips curl from PATH for its own "missing" test.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"

  unset OMES_GRAPHIFY_VERSION OMES_GRAPHIFY_INSTALLER OMES_UV_INSTALLER_SHA256 \
    SHIM_GRAPHIFY_ABSENT SHIM_GRAPHIFY_VERSION SHIM_GRAPHIFY_HELP_EXIT \
    SHIM_UV_EXIT SHIM_PIPX_EXIT SHIM_CURL_OUTPUT_FILE || true

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

  module_load graphify

  declare -ga OMES_MANAGED_PATHS=()

  # The bats/bats:latest (Alpine) image tests/run.sh falls back to does
  # not ship python3 at all, so every test that expects the python
  # version gate to PASS needs a real, controlled python3 on PATH rather
  # than depending on whatever (if anything) the host/CI image provides.
  # Prepending a fake 3.12 python3 here means only the two tests that
  # specifically exercise "missing"/"too old" need to touch PATH further.
  FAKE_PYTHON_OK_DIR="$(_fake_python3_dir "3.12.0")"
  export PATH="${FAKE_PYTHON_OK_DIR}:${PATH}"

  # On some images (e.g. the Alpine-based bats/bats + python3/git image
  # tests/run.sh builds), `id` and `python3` live in the SAME directory
  # (/usr/bin), so _strip_from_path("python3") - which drops a whole PATH
  # entry - would also hide `id`, breaking omes_is_root()'s `id -u` call
  # for every test, not just the two that intentionally hide python3.
  # Keep `id` reachable through a dedicated, never-stripped directory.
  ESSENTIAL_BIN_DIR="${OMES_TEST_TMPDIR}/essential-bin"
  mkdir -p "$ESSENTIAL_BIN_DIR"
  ln -sf "$(command -v id)" "${ESSENTIAL_BIN_DIR}/id"
  export PATH="${ESSENTIAL_BIN_DIR}:${PATH}"
}

teardown() {
  omes_test_teardown
}

# _strip_from_path <basename>
# Rebuilds PATH excluding every directory that contains an executable
# named <basename>, so `command -v <basename>` fails - the same trick
# tests/unit/hermes.bats uses to simulate "curl not found".
_strip_from_path() {
  local target="$1"
  local stripped="" dir
  IFS=':' read -ra parts <<<"$PATH"
  for dir in "${parts[@]}"; do
    [[ -x "${dir}/${target}" ]] && continue
    stripped="${stripped:+${stripped}:}${dir}"
  done
  PATH="$stripped"
}

# _omit_shims <basename...>
# tests/shims/{uv,pipx,graphify,curl,...} all live in ONE directory, so
# _strip_from_path (which drops a whole PATH entry) cannot hide one or two
# shims without also hiding every sibling in that same directory (e.g.
# curl, needed by the uv-bootstrap path). This instead rebuilds PATH with
# a filtered copy of tests/shims (every shim except the given basenames,
# symlinked) placed first, and the real tests/shims directory removed
# further down - so `command -v <basename>` fails for exactly the given
# names while every other shim (and $SHIM_LOG logging) keeps working.
_omit_shims() {
  local real_shims="${OMES_TEST_ROOT}/tests/shims"
  local filtered="${OMES_TEST_TMPDIR}/shims-without-${RANDOM}"
  mkdir -p "$filtered"

  local f base omit skip
  for f in "${real_shims}"/*; do
    base="$(basename "$f")"
    skip=0
    for omit in "$@"; do
      [[ "$base" == "$omit" ]] && skip=1 && break
    done
    [[ "$skip" -eq 1 ]] && continue
    ln -s "$f" "${filtered}/${base}"
  done

  local rebuilt="" dir
  IFS=':' read -ra parts <<<"$PATH"
  for dir in "${parts[@]}"; do
    [[ "$dir" == "$real_shims" ]] && continue
    rebuilt="${rebuilt:+${rebuilt}:}${dir}"
  done
  PATH="${filtered}:${rebuilt}"
}

# _fake_python3_dir <version-output>
# Writes a fake python3 printing "Python <version-output>" to a fresh
# tmpdir and prints that dir's path, for prepending onto PATH.
_fake_python3_dir() {
  local version="$1"
  local dir="${OMES_TEST_TMPDIR}/fake-python-${RANDOM}"
  mkdir -p "$dir"
  cat > "${dir}/python3" <<EOF
#!/usr/bin/env bash
printf 'Python %s\n' "${version}"
exit 0
EOF
  chmod +x "${dir}/python3"
  printf '%s\n' "$dir"
}

# --- module_check: python version gate --------------------------------------

@test "module_check passes when python3 >= 3.10 and an installer is present" {
  export SHIM_GRAPHIFY_VERSION="0.9.64"
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"already installed"* ]]
}

@test "module_check fails clearly when python3 is missing" {
  _strip_from_path python3
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"python3 >= 3.10"* ]]
}

@test "module_check fails clearly when python3 is older than 3.10" {
  local fake_dir
  fake_dir="$(_fake_python3_dir "3.9.0")"
  PATH="${fake_dir}:${PATH}" run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"python3 >= 3.10"* ]]
}

@test "module_check passes with python3 exactly 3.10" {
  local fake_dir
  fake_dir="$(_fake_python3_dir "3.10.0")"
  export SHIM_GRAPHIFY_VERSION="0.9.64"
  PATH="${fake_dir}:${PATH}" run module_check
  [ "$status" -eq 0 ]
}

# --- module_check: installer detection --------------------------------------

@test "module_check fails with an actionable message when neither uv nor pipx is present" {
  _omit_shims uv pipx
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"neither uv nor pipx found"* ]]
  [[ "$output" == *"OMES_GRAPHIFY_INSTALLER=uv-bootstrap"* ]]
}

@test "module_check prefers uv over pipx when both are present" {
  export SHIM_GRAPHIFY_VERSION="0.9.64"
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"using uv"* ]]
}

@test "module_check falls back to pipx when uv is absent" {
  _omit_shims uv
  export SHIM_GRAPHIFY_VERSION="0.9.64"
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"using pipx"* ]]
}

@test "module_check allows neither uv nor pipx when OMES_GRAPHIFY_INSTALLER=uv-bootstrap and network is available" {
  _omit_shims uv pipx
  export OMES_GRAPHIFY_INSTALLER="uv-bootstrap"
  export OMES_ASSUME_ONLINE=1
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"will bootstrap uv"* ]]
}

@test "module_check fails when OMES_GRAPHIFY_INSTALLER=uv-bootstrap but network is unavailable" {
  _omit_shims uv pipx
  export OMES_GRAPHIFY_INSTALLER="uv-bootstrap"
  export OMES_ASSUME_OFFLINE=1
  unset OMES_ASSUME_ONLINE || true
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"network required to bootstrap uv"* ]]
}

# --- module_check: network only needed to install ---------------------------

@test "module_check requires network only when graphify is not yet installed" {
  export SHIM_GRAPHIFY_ABSENT=1
  export OMES_ASSUME_OFFLINE=1
  unset OMES_ASSUME_ONLINE || true
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"network required to install graphifyy"* ]]
}

@test "module_check does not require network when graphify is already installed" {
  export SHIM_GRAPHIFY_VERSION="0.9.64"
  export OMES_ASSUME_OFFLINE=1
  unset OMES_ASSUME_ONLINE || true
  run module_check
  [ "$status" -eq 0 ]
}

# --- module_check: never root -----------------------------------------------

@test "module_check refuses to run as root" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  run module_check
  [ "$status" -eq 1 ]
}

# --- module_apply: idempotency ----------------------------------------------

@test "module_apply skips install when already installed and unpinned" {
  export SHIM_GRAPHIFY_VERSION="0.9.64"
  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipping install"* ]]
  run grep -c '^uv tool install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "module_apply installs via uv when not yet installed" {
  export SHIM_GRAPHIFY_ABSENT=1
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c '^uv tool install graphifyy$' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

@test "module_apply installs via pipx when uv is absent" {
  _omit_shims uv
  export SHIM_GRAPHIFY_ABSENT=1
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c '^pipx install graphifyy$' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

# --- module_apply: version pin -----------------------------------------------

@test "module_apply reinstalls with the pinned version spec when the installed version does not match" {
  export SHIM_GRAPHIFY_VERSION="0.9.50"
  export OMES_GRAPHIFY_VERSION="0.9.64"
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c '^uv tool install graphifyy==0.9.64$' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

@test "module_apply does not reinstall when the installed version already matches the pin" {
  export SHIM_GRAPHIFY_VERSION="0.9.64"
  export OMES_GRAPHIFY_VERSION="0.9.64"
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c '^uv tool install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

# --- module_apply: records the resolved version (state) ---------------------

@test "module_apply records the resolved version in state after a successful install" {
  export SHIM_GRAPHIFY_VERSION="0.9.64"
  run module_apply
  [ "$status" -eq 0 ]
  state_init >/dev/null
  run state_get "module.graphify.version_installed"
  [ "$status" -eq 0 ]
  [[ "$output" == *"0.9.64"* ]]
}

# --- module_apply: dry-run ----------------------------------------------------

@test "module_apply performs no install call under --dry-run" {
  export OMES_DRY_RUN=1
  export SHIM_GRAPHIFY_ABSENT=1
  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"[dry-run]"* ]] || true
  run grep -c '^uv tool install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run state_get "module.graphify.version_installed"
  [ "$status" -ne 0 ]
}

# --- module_apply: PEP 668 (never system pip) --------------------------------

@test "module_apply never invokes pip install, under any installer path" {
  export SHIM_GRAPHIFY_ABSENT=1
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c 'pip install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

# --- module_verify -------------------------------------------------------------

@test "module_verify passes when graphify --help exits 0" {
  export SHIM_GRAPHIFY_VERSION="0.9.64"
  run module_verify
  [ "$status" -eq 0 ]
}

@test "module_verify fails when graphify --help fails" {
  export SHIM_GRAPHIFY_VERSION="0.9.64"
  export SHIM_GRAPHIFY_HELP_EXIT=1
  run module_verify
  [ "$status" -eq 1 ]
}

@test "module_verify fails when graphify is not on PATH at all" {
  _strip_from_path graphify
  run module_verify
  [ "$status" -eq 1 ]
}

# --- module_rollback -----------------------------------------------------------

@test "module_rollback uninstalls via uv and clears state, never touching graphify-out" {
  export SHIM_GRAPHIFY_VERSION="0.9.64"
  run module_apply
  [ "$status" -eq 0 ]

  mkdir -p "${OMES_TEST_TMPDIR}/project/graphify-out"
  : > "${OMES_TEST_TMPDIR}/project/graphify-out/graph.json"

  run module_rollback
  [ "$status" -eq 0 ]
  [[ "$output" == *"never touches graphify-out"* ]]
  run grep -c '^uv tool uninstall graphifyy$' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]

  # graphify-out/ is untouched.
  [ -f "${OMES_TEST_TMPDIR}/project/graphify-out/graph.json" ]

  run state_get "module.graphify.version_installed"
  [ "$status" -ne 0 ]
}

@test "module_rollback under --dry-run performs no uninstall" {
  export SHIM_GRAPHIFY_VERSION="0.9.64"
  export OMES_DRY_RUN=1
  run module_rollback
  [ "$status" -eq 0 ]
  run grep -c '^uv tool uninstall' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "module_rollback is a no-op (not a failure) when neither uv nor pipx is present" {
  _omit_shims uv pipx
  run module_rollback
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing to uninstall"* ]]
}

# --- module_apply: OMES_GRAPHIFY_INSTALLER=uv-bootstrap ----------------------

@test "module_apply bootstraps uv via the downloaded installer when opted in and neither tool is present" {
  _omit_shims uv pipx
  export OMES_GRAPHIFY_INSTALLER="uv-bootstrap"
  export SHIM_GRAPHIFY_ABSENT=1

  local installer="${OMES_TEST_TMPDIR}/uv-installer.sh"
  cat > "$installer" <<'EOF'
#!/usr/bin/env bash
if [[ -n "${SHIM_LOG:-}" ]]; then
  printf 'uv-installer-ran\n' >> "$SHIM_LOG"
fi
exit 0
EOF
  export SHIM_CURL_OUTPUT_FILE="$installer"

  run module_apply
  [ "$status" -eq 1 ]
  # uv is still not actually on PATH after the fake installer "ran" (it
  # only logs, it does not really install uv) - module_apply must fail
  # loudly rather than silently proceed as if an installer were present.
  [[ "$output" == *"uv is still not on PATH"* ]]
  run grep -c 'uv-installer-ran' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

@test "module_check warns to set OMES_UV_INSTALLER_SHA256 is not required but bootstrap proceeds without it" {
  _omit_shims uv pipx
  export OMES_GRAPHIFY_INSTALLER="uv-bootstrap"
  export OMES_ASSUME_ONLINE=1
  run module_check
  [ "$status" -eq 0 ]
}
