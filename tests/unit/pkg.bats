#!/usr/bin/env bats
# tests/unit/pkg.bats - lib/omes/pkg.sh unit tests.
#
# Functions that mutate shell state the caller needs to observe afterward
# (repo_add/repo_remove appending to OMES_MANAGED_PATHS via
# omes_manage_path) are called directly, output redirected but not
# captured, matching the convention documented in tests/unit/backup.bats -
# `run` forks a subshell, which would silently lose that side effect.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/log.sh
  source "${OMES_TEST_ROOT}/lib/omes/log.sh"
  # shellcheck source=../../lib/omes/json.sh
  source "${OMES_TEST_ROOT}/lib/omes/json.sh"
  # shellcheck source=../../lib/omes/detect.sh
  source "${OMES_TEST_ROOT}/lib/omes/detect.sh"
  # shellcheck source=../../lib/omes/state.sh
  source "${OMES_TEST_ROOT}/lib/omes/state.sh"
  # shellcheck source=../../lib/omes/backup.sh
  source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
  # shellcheck source=../../lib/omes/module.sh
  source "${OMES_TEST_ROOT}/lib/omes/module.sh"
  # shellcheck source=../../lib/omes/pkg.sh
  source "${OMES_TEST_ROOT}/lib/omes/pkg.sh"
  state_init >/dev/null
  declare -ga OMES_MANAGED_PATHS=()
}

teardown() {
  omes_test_teardown
}

# --- pkg_is_installed / pkg_missing ---------------------------------------

@test "pkg_is_installed is true for a package the dpkg-query shim reports installed" {
  printf 'curl\n' >>"$SHIM_INSTALLED_PKGS_FILE"
  run pkg_is_installed curl
  [ "$status" -eq 0 ]
}

@test "pkg_is_installed is false for a package the dpkg-query shim has never seen" {
  run pkg_is_installed does-not-exist
  [ "$status" -eq 1 ]
}

@test "pkg_missing echoes only the not-yet-installed subset, preserving order" {
  printf 'curl\n' >>"$SHIM_INSTALLED_PKGS_FILE"
  run pkg_missing curl git jq
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "git" ]
  [ "${lines[1]}" = "jq" ]
}

@test "pkg_missing prints nothing when every package is installed" {
  printf 'curl\ngit\n' >>"$SHIM_INSTALLED_PKGS_FILE"
  run pkg_missing curl git
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

# --- pkg_map -----------------------------------------------------------------

@test "pkg_map resolves bat/fd/eza divergences on Ubuntu 24.04" {
  OMES_OS_RELEASE_FILE="${OMES_TEST_ROOT}/tests/fixtures/os-release/ubuntu-24.04" detect_os >/dev/null
  [ "$(pkg_map bat)" = "bat" ]
  [ "$(pkg_map fd)" = "fd-find" ]
  [ "$(pkg_map eza)" = "eza" ]
  [ "$(pkg_map_binary bat)" = "batcat" ]
  [ "$(pkg_map_binary fd)" = "fdfind" ]
}

@test "pkg_map resolves the same divergences on Linux Mint 22" {
  OMES_OS_RELEASE_FILE="${OMES_TEST_ROOT}/tests/fixtures/os-release/linuxmint-22" detect_os >/dev/null
  [ "$(pkg_map bat)" = "bat" ]
  [ "$(pkg_map fd)" = "fd-find" ]
}

@test "pkg_map resolves the same divergences on Linux Mint 22.3" {
  OMES_OS_RELEASE_FILE="${OMES_TEST_ROOT}/tests/fixtures/os-release/linuxmint-22.3" detect_os >/dev/null
  [ "$(pkg_map bat)" = "bat" ]
  [ "$(pkg_map fd)" = "fd-find" ]
}

@test "pkg_map resolves the same divergences on Ubuntu 22.04" {
  OMES_OS_RELEASE_FILE="${OMES_TEST_ROOT}/tests/fixtures/os-release/ubuntu-22.04" detect_os >/dev/null
  [ "$(pkg_map bat)" = "bat" ]
  [ "$(pkg_map fd)" = "fd-find" ]
}

@test "pkg_map passes through an unmapped logical name unchanged" {
  OMES_OS_RELEASE_FILE="${OMES_TEST_ROOT}/tests/fixtures/os-release/ubuntu-22.04" detect_os >/dev/null
  [ "$(pkg_map curl)" = "curl" ]
}

@test "pkg_map honors a per-release override when one is registered" {
  OMES_OS_RELEASE_FILE="${OMES_TEST_ROOT}/tests/fixtures/os-release/ubuntu-22.04" detect_os >/dev/null
  PKG_MAP_RELEASE_OVERRIDES["ubuntu:22.04:eza"]="eza-legacy"
  [ "$(pkg_map eza)" = "eza-legacy" ]
  # A different release is unaffected by the override.
  OMES_OS_RELEASE_FILE="${OMES_TEST_ROOT}/tests/fixtures/os-release/ubuntu-24.04" detect_os >/dev/null
  [ "$(pkg_map eza)" = "eza" ]
}

# --- pkg_apt_update ------------------------------------------------------------

@test "pkg_apt_update runs apt-get update and records the timestamp on first call" {
  run pkg_apt_update
  [ "$status" -eq 0 ]
  run grep -c '^apt-get update$' "$SHIM_LOG"
  [ "$output" -eq 1 ]
  run state_get "pkg.apt_update.last_run"
  [ "$status" -eq 0 ]
}

@test "pkg_apt_update skips a second call within the max-age window" {
  pkg_apt_update >/dev/null
  : >"$SHIM_LOG"
  run pkg_apt_update
  [ "$status" -eq 0 ]
  run grep -c 'apt-get update' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "pkg_apt_update re-runs once OMES_PKG_APT_UPDATE_MAX_AGE has elapsed" {
  pkg_apt_update >/dev/null
  local past=$(($(date -u +%s) - 10))
  state_set "pkg.apt_update.last_run" "$past"
  : >"$SHIM_LOG"
  OMES_PKG_APT_UPDATE_MAX_AGE=5 run pkg_apt_update
  [ "$status" -eq 0 ]
  run grep -c '^apt-get update$' "$SHIM_LOG"
  [ "$output" -eq 1 ]
}

@test "pkg_apt_update honors dry-run: no apt-get call, no state write" {
  OMES_DRY_RUN=1 run pkg_apt_update
  [ "$status" -eq 0 ]
  run grep -c 'apt-get update' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run state_get "pkg.apt_update.last_run"
  [ "$status" -eq 1 ]
}

@test "pkg_apt_update returns OMES_EX_NETWORK (8) when offline" {
  OMES_ASSUME_ONLINE=0 OMES_ASSUME_OFFLINE=1 run pkg_apt_update
  [ "$status" -eq 8 ]
}

@test "pkg_apt_update returns OMES_EX_MODULE_APPLY (6) when apt-get itself fails" {
  SHIM_APT_GET_FAIL_UPDATE=1 run pkg_apt_update
  [ "$status" -eq 6 ]
}

# --- pkg_exists_in_repos / pkg_candidate_version --------------------------------

@test "pkg_exists_in_repos succeeds for a package the apt-cache shim knows" {
  run pkg_exists_in_repos curl
  [ "$status" -eq 0 ]
}

@test "pkg_exists_in_repos returns OMES_EX_PREFLIGHT (4) for an unknown package" {
  SHIM_APT_CACHE_UNKNOWN_PKGS="totally-made-up-package" run pkg_exists_in_repos totally-made-up-package
  [ "$status" -eq 4 ]
}

@test "pkg_exists_in_repos returns OMES_EX_NETWORK (8) when offline, without misreporting unknown" {
  OMES_ASSUME_ONLINE=0 OMES_ASSUME_OFFLINE=1 run pkg_exists_in_repos curl
  [ "$status" -eq 8 ]
}

@test "pkg_candidate_version prints the apt-cache Candidate line" {
  SHIM_APT_CACHE_VERSION="2.3.4-1" run pkg_candidate_version curl
  [ "$status" -eq 0 ]
  [ "$output" = "2.3.4-1" ]
}

# --- pkg_install ---------------------------------------------------------------

@test "pkg_install is a no-op and returns 0 when everything is already installed" {
  printf 'curl\n' >>"$SHIM_INSTALLED_PKGS_FILE"
  run pkg_install curl
  [ "$status" -eq 0 ]
  run grep -c 'apt-get install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "pkg_install installs only the missing subset and records installed_packages for MODULE_NAME" {
  printf 'curl\n' >>"$SHIM_INSTALLED_PKGS_FILE"
  MODULE_NAME="demo"
  run pkg_install curl git jq
  [ "$status" -eq 0 ]
  run grep -c 'apt-get install' "$SHIM_LOG"
  [ "$output" -eq 1 ]
  run grep 'apt-get install' "$SHIM_LOG"
  [[ "$output" != *" curl"* ]]
  [[ "$output" == *"git"* ]]
  [[ "$output" == *"jq"* ]]
  run state_get "module.demo.installed_packages"
  [ "$status" -eq 0 ]
  [ "$output" = "git:jq" ]
}

@test "pkg_install merges with a prior installed_packages record instead of overwriting it" {
  MODULE_NAME="demo"
  state_set "module.demo.installed_packages" "curl"
  run pkg_install git
  [ "$status" -eq 0 ]
  run state_get "module.demo.installed_packages"
  [ "$output" = "curl:git" ]
}

@test "pkg_install honors dry-run: no apt-get call, no state write" {
  MODULE_NAME="demo"
  OMES_DRY_RUN=1 run pkg_install git
  [ "$status" -eq 0 ]
  run grep -c 'apt-get install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run state_get "module.demo.installed_packages"
  [ "$status" -eq 1 ]
}

@test "pkg_install returns OMES_EX_NETWORK (8) when offline and packages are missing" {
  MODULE_NAME="demo"
  OMES_ASSUME_ONLINE=0 OMES_ASSUME_OFFLINE=1 run pkg_install git
  [ "$status" -eq 8 ]
}

@test "pkg_install returns OMES_EX_MODULE_APPLY (6) when apt-get install itself fails" {
  MODULE_NAME="demo"
  SHIM_APT_GET_FAIL_INSTALL=1 run pkg_install git
  [ "$status" -eq 6 ]
  run state_get "module.demo.installed_packages"
  [ "$status" -eq 1 ]
}

# --- repo_validate ---------------------------------------------------------------

@test "repo_validate rejects an http:// URI" {
  local keyring="${OMES_TEST_TMPDIR}/keyring.gpg"
  : >"$keyring"
  chmod 644 "$keyring"
  run repo_validate demo "http://example.com/repo" noble "$keyring"
  [ "$status" -eq 1 ]
}

@test "repo_validate rejects a missing keyring file" {
  run repo_validate demo "https://example.com/repo" noble "${OMES_TEST_TMPDIR}/does-not-exist.gpg"
  [ "$status" -eq 1 ]
}

@test "repo_validate rejects a keyring with the wrong mode" {
  local keyring="${OMES_TEST_TMPDIR}/keyring.gpg"
  : >"$keyring"
  chmod 600 "$keyring"
  run repo_validate demo "https://example.com/repo" noble "$keyring"
  [ "$status" -eq 1 ]
}

@test "repo_validate accepts a well-formed https repo with a 0644 keyring" {
  local keyring="${OMES_TEST_TMPDIR}/keyring.gpg"
  : >"$keyring"
  chmod 644 "$keyring"
  run repo_validate demo "https://example.com/repo" noble "$keyring"
  [ "$status" -eq 0 ]
}

# --- repo_add / repo_remove -------------------------------------------------------

@test "repo_add writes a deb822 .sources file and registers it via omes_manage_path" {
  local keyring="${OMES_TEST_TMPDIR}/keyring.gpg"
  : >"$keyring"
  chmod 644 "$keyring"
  export OMES_APT_SOURCES_DIR="${OMES_TEST_TMPDIR}/sources.list.d"

  repo_add demo "https://example.com/repo" noble "$keyring" >/dev/null

  local file="${OMES_APT_SOURCES_DIR}/demo.sources"
  [ -f "$file" ]
  grep -q '^Types: deb$' "$file"
  grep -q '^URIs: https://example.com/repo$' "$file"
  grep -q '^Suites: noble$' "$file"
  grep -q '^Components: main$' "$file"
  grep -qF "Signed-By: ${keyring}" "$file"
  [ "${#OMES_MANAGED_PATHS[@]}" -eq 1 ]
  [ "${OMES_MANAGED_PATHS[0]}" = "$file" ]
}

@test "repo_add refuses an invalid repo and writes nothing" {
  export OMES_APT_SOURCES_DIR="${OMES_TEST_TMPDIR}/sources.list.d"
  run repo_add demo "http://example.com/repo" noble "${OMES_TEST_TMPDIR}/missing.gpg"
  [ "$status" -eq 1 ]
  [ ! -e "${OMES_APT_SOURCES_DIR}/demo.sources" ]
}

@test "repo_add honors dry-run: no file written" {
  local keyring="${OMES_TEST_TMPDIR}/keyring.gpg"
  : >"$keyring"
  chmod 644 "$keyring"
  export OMES_APT_SOURCES_DIR="${OMES_TEST_TMPDIR}/sources.list.d"
  OMES_DRY_RUN=1 run repo_add demo "https://example.com/repo" noble "$keyring"
  [ "$status" -eq 0 ]
  [ ! -e "${OMES_APT_SOURCES_DIR}/demo.sources" ]
}

@test "repo_add on Linux Mint with --ubuntu-only warns about non-parity" {
  OMES_OS_RELEASE_FILE="${OMES_TEST_ROOT}/tests/fixtures/os-release/linuxmint-22" detect_os >/dev/null
  local keyring="${OMES_TEST_TMPDIR}/keyring.gpg"
  : >"$keyring"
  chmod 644 "$keyring"
  export OMES_APT_SOURCES_DIR="${OMES_TEST_TMPDIR}/sources.list.d"
  run repo_add docker "https://download.docker.com/linux/ubuntu" "$OMES_OS_CODENAME" "$keyring" --ubuntu-only
  [ "$status" -eq 0 ]
  [[ "$output" == *"do not support Linux Mint directly"* ]]
  grep -q '^Suites: noble$' "${OMES_APT_SOURCES_DIR}/docker.sources"
}

@test "repo_remove deletes an existing .sources file and registers it via omes_manage_path" {
  export OMES_APT_SOURCES_DIR="${OMES_TEST_TMPDIR}/sources.list.d"
  mkdir -p "$OMES_APT_SOURCES_DIR"
  local file="${OMES_APT_SOURCES_DIR}/demo.sources"
  printf 'Types: deb\n' >"$file"

  repo_remove demo >/dev/null

  [ ! -e "$file" ]
  [ "${#OMES_MANAGED_PATHS[@]}" -eq 1 ]
}

@test "repo_remove is a no-op (exit 0) when the file does not exist" {
  export OMES_APT_SOURCES_DIR="${OMES_TEST_TMPDIR}/sources.list.d"
  run repo_remove does-not-exist
  [ "$status" -eq 0 ]
}
