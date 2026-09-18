#!/usr/bin/env bats
# tests/unit/hermes.bats - modules/hermes/module.sh unit tests.
#
# Sources lib/omes/*.sh and modules/hermes/module.sh directly (module_load),
# like tests/unit/module.bats. HOME is always overridden to an isolated
# tmpdir so nothing here ever touches the real developer's home directory
# (~/.bashrc, ~/.profile, ~/.hermes, ...). tests/shims/hermes is already on
# PATH via test_helper.bash's omes_test_setup.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  unset XDG_DATA_HOME || true

  export OMES_HERMES_HOME="${HOME}/.hermes"
  unset OMES_HERMES_VERSION OMES_HERMES_INSTALLER_SHA256 SHIM_HERMES_VERSION \
    SHIM_HERMES_DOCTOR_EXIT SHIM_HERMES_DOCTOR_OUTPUT || true

  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/log.sh
  source "${OMES_TEST_ROOT}/lib/omes/log.sh"
  # shellcheck source=../../lib/omes/state.sh
  source "${OMES_TEST_ROOT}/lib/omes/state.sh"
  # shellcheck source=../../lib/omes/backup.sh
  source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
  # shellcheck source=../../lib/omes/module.sh
  source "${OMES_TEST_ROOT}/lib/omes/module.sh"

  module_load hermes

  declare -ga OMES_MANAGED_PATHS=()
}

teardown() {
  omes_test_teardown
}

# _install_fake_curl <installer-body>
# Puts a fake curl on the front of PATH that writes <installer-body> (a
# heredoc-provided script) to whatever -o target it is given, and logs
# "fake-curl-fetched <url>" to $SHIM_LOG. Used to exercise the
# download-then-run path without any real network access or a committed
# tests/shims/curl (curl is intentionally not a shared shim in this repo).
_install_fake_curl() {
  local body="$1"
  mkdir -p "${OMES_TEST_TMPDIR}/fakebin"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'set -Eeuo pipefail\n'
    printf 'out=""\n'
    printf 'url=""\n'
    printf 'args=("$@")\n'
    printf 'for ((i=0;i<${#args[@]};i++)); do\n'
    printf '  if [[ "${args[$i]}" == "-o" ]]; then out="${args[$((i+1))]}"; fi\n'
    printf '  if [[ "${args[$i]}" == http* ]]; then url="${args[$i]}"; fi\n'
    printf 'done\n'
    printf 'if [[ -n "${SHIM_LOG:-}" ]]; then printf "fake-curl-fetched %%s\\n" "$url" >> "$SHIM_LOG"; fi\n'
    printf 'if [[ -n "$out" ]]; then\n'
    printf 'cat > "$out" <<'"'"'INSTALLER'"'"'\n'
    printf '%s\n' "$body"
    printf 'INSTALLER\n'
    printf 'fi\n'
    printf 'exit "${SHIM_CURL_EXIT:-0}"\n'
  } > "${OMES_TEST_TMPDIR}/fakebin/curl"
  chmod +x "${OMES_TEST_TMPDIR}/fakebin/curl"
  export PATH="${OMES_TEST_TMPDIR}/fakebin:${PATH}"
}

_fake_installer_body() {
  cat <<'EOF'
#!/usr/bin/env bash
if [[ -n "${SHIM_LOG:-}" ]]; then
  printf 'installer-ran HERMES_HOME=%s\n' "${HERMES_HOME:-}" >> "$SHIM_LOG"
fi
exit 0
EOF
}

# --- module_check ----------------------------------------------------------

@test "module_check refuses to run as root" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  run module_check
  [ "$status" -eq 1 ]
}

@test "module_check fails when curl is not on PATH" {
  local stripped=""
  local dir
  IFS=':' read -ra parts <<< "$PATH"
  for dir in "${parts[@]}"; do
    [[ -x "${dir}/curl" ]] && continue
    stripped="${stripped:+${stripped}:}${dir}"
  done
  PATH="$stripped" run module_check
  [ "$status" -eq 1 ]
}

@test "module_check reports already installed when the hermes shim reports a version" {
  export SHIM_HERMES_VERSION="1.2.3"
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"already installed"* ]]
}

@test "module_check requires network when not yet installed" {
  export OMES_ASSUME_OFFLINE=1
  unset OMES_ASSUME_ONLINE || true
  run module_check
  [ "$status" -eq 1 ]
}

# --- module_apply: idempotency ---------------------------------------------

@test "module_apply skips the installer download when already installed (no version pin)" {
  export SHIM_HERMES_VERSION="1.2.3"
  : > "$SHIM_LOG"
  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipping installer download"* ]]
  run grep -c 'fake-curl-fetched\|^curl ' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "module_apply re-downloads when OMES_HERMES_VERSION does not match the installed version" {
  export SHIM_HERMES_VERSION="1.2.3"
  export OMES_HERMES_VERSION="9.9.9"
  _install_fake_curl "$(_fake_installer_body)"
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c 'fake-curl-fetched' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

# --- module_apply: supply-chain (sha256 pin) --------------------------------

@test "module_apply proceeds with a WARN when OMES_HERMES_INSTALLER_SHA256 is not set" {
  _install_fake_curl "$(_fake_installer_body)"
  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"OMES_HERMES_INSTALLER_SHA256"* ]]
  run grep -c 'installer-ran' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

@test "module_apply aborts and does not execute the installer on a sha256 mismatch" {
  _install_fake_curl "$(_fake_installer_body)"
  export OMES_HERMES_INSTALLER_SHA256="0000000000000000000000000000000000000000000000000000000000000000"
  run module_apply
  [ "$status" -eq 1 ]
  [[ "$output" == *"sha256 mismatch"* ]]
  run grep -c 'installer-ran' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "module_apply executes the installer when the sha256 pin matches" {
  _install_fake_curl "$(_fake_installer_body)"

  # Compute the pin the same way the fake curl assembles the installer file,
  # by writing it once ourselves and hashing it.
  local tmp="${OMES_TEST_TMPDIR}/expected-installer.sh"
  _fake_installer_body > "$tmp"
  local expected
  expected="$(sha256sum "$tmp" | awk '{print $1}')"

  export OMES_HERMES_INSTALLER_SHA256="$expected"
  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" != *"sha256 mismatch"* ]]
  run grep -c "installer-ran HERMES_HOME=${OMES_HERMES_HOME}" "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

# --- module_apply: dry-run ---------------------------------------------------

@test "module_apply performs no download and writes nothing under --dry-run" {
  export OMES_DRY_RUN=1
  _install_fake_curl "$(_fake_installer_body)"
  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"[dry-run]"* ]]
  run grep -c 'fake-curl-fetched' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  [ ! -e "${OMES_HERMES_HOME}/.env" ]
  [ ! -e "$(_hermes_path_snippet_file)" ]
}

# --- module_apply: .env handling --------------------------------------------

@test "module_apply creates \$HERMES_HOME/.env with mode 0600 when absent" {
  export SHIM_HERMES_VERSION="1.2.3"
  run module_apply
  [ "$status" -eq 0 ]
  [ -f "${OMES_HERMES_HOME}/.env" ]
  local mode
  mode="$(stat -c '%a' "${OMES_HERMES_HOME}/.env")"
  [ "$mode" = "600" ]
}

@test "module_apply never overwrites an existing .env" {
  export SHIM_HERMES_VERSION="1.2.3"
  mkdir -p "$OMES_HERMES_HOME"
  printf 'TELEGRAM_BOT_TOKEN=do-not-clobber\n' > "${OMES_HERMES_HOME}/.env"
  chmod 600 "${OMES_HERMES_HOME}/.env"
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c 'do-not-clobber' "${OMES_HERMES_HOME}/.env"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

# --- module_apply: PATH snippet idempotency ---------------------------------

@test "module_apply wires the PATH snippet into .bashrc and .profile exactly once across re-runs" {
  export SHIM_HERMES_VERSION="1.2.3"
  : > "${HOME}/.bashrc"
  : > "${HOME}/.profile"

  run module_apply
  [ "$status" -eq 0 ]
  run module_apply
  [ "$status" -eq 0 ]

  run grep -c 'BEGIN OMES hermes PATH' "${HOME}/.bashrc"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
  run grep -c 'BEGIN OMES hermes PATH' "${HOME}/.profile"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

# --- module_verify -----------------------------------------------------------

@test "module_verify fails when hermes doctor fails, with actionable output" {
  export SHIM_HERMES_VERSION="1.2.3"
  export SHIM_HERMES_DOCTOR_EXIT=1
  export SHIM_HERMES_DOCTOR_OUTPUT="FAIL: telegram gateway unreachable"
  run module_verify
  [ "$status" -eq 1 ]
  [[ "$output" == *"telegram gateway unreachable"* ]]
}

@test "module_verify passes but warns when hermes doctor reports a WARN" {
  export SHIM_HERMES_VERSION="1.2.3"
  export SHIM_HERMES_DOCTOR_EXIT=0
  export SHIM_HERMES_DOCTOR_OUTPUT="WARN: provider api key not configured"
  run module_verify
  [ "$status" -eq 0 ]
  [[ "$output" == *"warnings"* ]]
}

@test "module_verify fails when hermes --version fails" {
  unset SHIM_HERMES_VERSION || true
  run module_verify
  [ "$status" -eq 1 ]
}

# --- module_rollback ---------------------------------------------------------

@test "module_rollback removes the PATH snippet and marker block but never \$HERMES_HOME" {
  export SHIM_HERMES_VERSION="1.2.3"
  : > "${HOME}/.bashrc"
  run module_apply
  [ "$status" -eq 0 ]
  [ -f "$(_hermes_path_snippet_file)" ]

  mkdir -p "$OMES_HERMES_HOME"
  : > "${OMES_HERMES_HOME}/marker-user-data"

  run module_rollback
  [ "$status" -eq 0 ]
  [ ! -f "$(_hermes_path_snippet_file)" ]
  run grep -c 'BEGIN OMES hermes PATH' "${HOME}/.bashrc"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]

  # Hermes' own data is untouched.
  [ -f "${OMES_HERMES_HOME}/marker-user-data" ]
  [[ "$output" == *"rm -rf"* ]]
}
