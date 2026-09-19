#!/usr/bin/env bats
# tests/unit/containers.bats - modules/containers/module.sh unit tests (#7).
#
# Sources lib/omes/*.sh and modules/containers/module.sh directly
# (module_load), like tests/unit/hermes-gateway.bats. OMES_ETC_DIR and
# OMES_APT_SOURCES_DIR are ALWAYS overridden to isolated tmpdirs so the
# module never touches the real /etc. tests/shims/{docker,getent,
# loginctl,usermod,gpasswd,curl,apt-get,apt-cache,dpkg-query,systemctl}
# are on PATH via test_helper.bash's omes_test_setup.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  export OMES_ETC_DIR="${OMES_TEST_TMPDIR}/etc"
  export OMES_APT_SOURCES_DIR="${OMES_TEST_TMPDIR}/sources.list.d"
  export OMES_OS_ID=ubuntu
  export OMES_OS_CODENAME=noble
  # systemctl shim: statefile-backed is-enabled/is-active for `systemctl
  # enable --now docker` (system scope, no --user flag) to be consistent
  # across calls within one test - see tests/shims/systemctl's header.
  export SHIM_SYSTEM_ENABLED_FILE="${OMES_TEST_TMPDIR}/sys-enabled"
  export SHIM_SYSTEM_ACTIVE_FILE="${OMES_TEST_TMPDIR}/sys-active"
  unset SUDO_USER OMES_ALLOW_DOCKER_GROUP OMES_DOCKER_ROOTLESS \
    OMES_NONINTERACTIVE SHIM_DOCKER_VERSION_EXIT SHIM_GETENT_SUBUID_USERS \
    SHIM_GETENT_SUBGID_USERS SHIM_LOGINCTL_KNOWN_USERS || true

  # A minimal, well-formed ASCII-armored PGP public key block, standing in
  # for Docker's real signing key - only its shape (BEGIN/END markers)
  # matters to _containers module_apply's verification step.
  PEM_FIXTURE="${OMES_TEST_TMPDIR}/docker-gpg.asc"
  {
    printf -- '-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n'
    printf 'mQINBFit2ioBEADhWpZ8/wvZ6hUTiXOwQHXMAlaFHcPH9hAtr4F1y2+OQ0OF\n'
    printf -- '-----END PGP PUBLIC KEY BLOCK-----\n'
  } > "$PEM_FIXTURE"
  export SHIM_CURL_OUTPUT_FILE="$PEM_FIXTURE"

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
  module_load containers
  declare -ga OMES_MANAGED_PATHS=()
}

teardown() {
  omes_test_teardown
}

# --- module_check --------------------------------------------------------------

@test "module_check fails for a codename Docker's repo does not publish" {
  export OMES_OS_CODENAME=totallymadeup
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"does not publish a suite"* ]]
}

@test "module_check warns on Linux Mint but still passes for a supported underlying codename" {
  export OMES_OS_ID=linuxmint
  export OMES_OS_CODENAME=noble
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"Docker officially supports Ubuntu only"* ]]
}

@test "module_check makes no repo/keyring/package mutation" {
  run module_check
  [ "$status" -eq 0 ]
  [ ! -e "${OMES_ETC_DIR}/apt/keyrings/docker.asc" ]
  [ ! -e "${OMES_APT_SOURCES_DIR}/docker.sources" ]
  run grep -c 'apt-get install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

# --- module_apply: repo + keyring -----------------------------------------------

@test "module_apply downloads Docker's signing key as ASCII-armored PEM at mode 0644" {
  run module_apply
  [ "$status" -eq 0 ]
  local keyring="${OMES_ETC_DIR}/apt/keyrings/docker.asc"
  [ -f "$keyring" ]
  run stat -c '%a' "$keyring"
  [ "$output" = "644" ]
  run grep -c 'BEGIN PGP PUBLIC KEY BLOCK' "$keyring"
  [ "$output" -ge 1 ]
}

@test "module_apply adds the docker apt repo via https with a Signed-By keyring" {
  run module_apply
  [ "$status" -eq 0 ]
  local file="${OMES_APT_SOURCES_DIR}/docker.sources"
  [ -f "$file" ]
  run grep -c '^Types: deb$' "$file"
  [ "$output" -eq 1 ]
  run grep -c '^URIs: https://download.docker.com/linux/ubuntu$' "$file"
  [ "$output" -eq 1 ]
  run grep -c '^Suites: noble$' "$file"
  [ "$output" -eq 1 ]
  run grep -c "Signed-By: ${OMES_ETC_DIR}/apt/keyrings/docker.asc" "$file"
  [ "$output" -eq 1 ]
}

@test "on Linux Mint, module_apply uses \$UBUNTU_CODENAME for the suite and warns about non-parity" {
  export OMES_OS_ID=linuxmint
  export OMES_OS_CODENAME=noble
  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"do not support Linux Mint directly"* ]]
  run grep -c '^Suites: noble$' "${OMES_APT_SOURCES_DIR}/docker.sources"
  [ "$output" -eq 1 ]
}

@test "module_apply refuses to install a key that is not ASCII-armored/PEM" {
  printf 'not a real key\n' > "$PEM_FIXTURE"
  run module_apply
  [ "$status" -ne 0 ]
  [[ "$output" == *"does not look like an ASCII-armored"* ]]
  [ ! -f "${OMES_ETC_DIR}/apt/keyrings/docker.asc" ]
}

@test "module_apply installs the documented docker package set and enables the service" {
  run module_apply
  [ "$status" -eq 0 ]
  run grep 'apt-get install' "$SHIM_LOG"
  [[ "$output" == *"docker-ce"* ]]
  [[ "$output" == *"docker-ce-cli"* ]]
  [[ "$output" == *"containerd.io"* ]]
  [[ "$output" == *"docker-buildx-plugin"* ]]
  [[ "$output" == *"docker-compose-plugin"* ]]
  run grep -c 'systemctl enable --now docker' "$SHIM_LOG"
  [ "$output" -eq 1 ]
}

@test "module_apply records apt package-manager provenance (issue #84) for each Docker package" {
  export SHIM_DPKG_VERSION="5:27.0.0-1~ubuntu.24.04~noble"
  run module_apply
  [ "$status" -eq 0 ]

  local target="${OMES_STATE_DIR}/provenance/docker-ce.json"
  [ -f "$target" ]

  OMES_TEST_JSON="$(cat "$target")" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["component"] == "docker-ce"
assert d["resolved_version"] == "5:27.0.0-1~ubuntu.24.04~noble"
assert d["checksum"]["status"] == "package_manager_verified"
assert d["package_manager"]["name"] == "apt"
'
  [ "$status" -eq 0 ]

  for pkg in docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; do
    [ -f "${OMES_STATE_DIR}/provenance/${pkg}.json" ]
  done
}

# --- Access policy ---------------------------------------------------------------

@test "default access: no group change, prints the sudo docker usage note" {
  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"sudo docker"* ]]
  run grep -c 'usermod' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run state_get "module.containers.docker_group_user"
  [ "$status" -eq 1 ]
}

@test "the invoking user is added to the docker group only with --allow-docker-group AND --yes, and it is recorded" {
  export SUDO_USER=alice
  export OMES_ALLOW_DOCKER_GROUP=1
  export OMES_NONINTERACTIVE=1
  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"ROOT-EQUIVALENT"* ]]
  run grep -c 'usermod -aG docker alice' "$SHIM_LOG"
  [ "$output" -eq 1 ]
  run state_get "module.containers.docker_group_user"
  [ "$output" = "alice" ]
}

@test "the docker group is NOT changed when --allow-docker-group is given without confirmation" {
  export SUDO_USER=alice
  export OMES_ALLOW_DOCKER_GROUP=1
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c 'usermod' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run state_get "module.containers.docker_group_user"
  [ "$status" -eq 1 ]
}

@test "docker group membership is refused when SUDO_USER is empty, even with --allow-docker-group --yes" {
  export OMES_ALLOW_DOCKER_GROUP=1
  export OMES_NONINTERACTIVE=1
  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"SUDO_USER is empty"* ]]
  run grep -c 'usermod' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "the docker group is never changed under dry-run, even with --allow-docker-group --yes" {
  export SUDO_USER=alice
  export OMES_ALLOW_DOCKER_GROUP=1
  export OMES_NONINTERACTIVE=1
  OMES_DRY_RUN=1 run module_apply
  [ "$status" -eq 0 ]
  run grep -c 'usermod' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

# --- Rootless ---------------------------------------------------------------------

@test "rootless: satisfied prerequisites install docker-ce-rootless-extras and print the user-level setup command" {
  export SUDO_USER=alice
  export OMES_DOCKER_ROOTLESS=1
  printf 'uidmap\ndbus-user-session\n' >> "$SHIM_INSTALLED_PKGS_FILE"
  export SHIM_GETENT_SUBUID_USERS=alice
  export SHIM_GETENT_SUBGID_USERS=alice
  export SHIM_LOGINCTL_KNOWN_USERS=alice

  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"dockerd-rootless-setuptool.sh install"* ]]
  run grep -c 'apt-get install' "$SHIM_LOG"
  [ "$output" -ge 1 ]
  run grep 'apt-get install' "$SHIM_LOG"
  [[ "$output" == *"docker-ce-rootless-extras"* ]]
  run state_get "module.containers.rootless_prereqs_user"
  [ "$output" = "alice" ]
}

@test "rootless: missing prerequisites are reported clearly, and rootless-extras is not installed" {
  export SUDO_USER=alice
  export OMES_DOCKER_ROOTLESS=1
  # uidmap/dbus-user-session not installed; no subuid/subgid/session.

  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"rootless prerequisites are not met"* ]]
  [[ "$output" == *"uidmap"* ]]
  [[ "$output" == *"dbus-user-session"* ]]
  run grep 'apt-get install' "$SHIM_LOG"
  [[ "$output" != *"docker-ce-rootless-extras"* ]]
}

@test "rootless is not attempted at all unless OMES_DOCKER_ROOTLESS=1" {
  export SUDO_USER=alice
  run module_apply
  [ "$status" -eq 0 ]
  run grep 'apt-get install' "$SHIM_LOG"
  [[ "$output" != *"docker-ce-rootless-extras"* ]]
  run state_get "module.containers.rootless_prereqs_user"
  [ "$status" -eq 1 ]
}

# --- module_verify -----------------------------------------------------------------

@test "module_verify passes when docker is reachable and the service is enabled" {
  module_apply >/dev/null
  run module_verify
  [ "$status" -eq 0 ]
}

@test "module_verify fails when docker is unreachable" {
  module_apply >/dev/null
  export SHIM_DOCKER_VERSION_EXIT=1
  run module_verify
  [ "$status" -eq 1 ]
  [[ "$output" == *"docker version"* ]]
}

# --- module_rollback -----------------------------------------------------------------

@test "module_rollback removes the docker group membership OMES added" {
  export SUDO_USER=alice
  export OMES_ALLOW_DOCKER_GROUP=1
  export OMES_NONINTERACTIVE=1
  module_apply >/dev/null
  run state_get "module.containers.docker_group_user"
  [ "$output" = "alice" ]

  run module_rollback
  [ "$status" -eq 0 ]
  run grep -c 'gpasswd -d alice docker' "$SHIM_LOG"
  [ "$output" -eq 1 ]
  run state_get "module.containers.docker_group_user"
  [ "$status" -eq 1 ]
}

@test "module_rollback removes the docker apt repo and signing key, and leaves packages installed" {
  run module_apply
  [ "$status" -eq 0 ]
  local keyring="${OMES_ETC_DIR}/apt/keyrings/docker.asc"
  local repo_file="${OMES_APT_SOURCES_DIR}/docker.sources"
  [ -f "$keyring" ]
  [ -f "$repo_file" ]

  run module_rollback
  [ "$status" -eq 0 ]
  [ ! -f "$keyring" ]
  [ ! -f "$repo_file" ]
  [[ "$output" == *"apt-get remove"* ]]
  [[ "$output" == *"docker-ce"* ]]
}

# --- Dry-run -------------------------------------------------------------------------

@test "dry-run makes no curl, apt-get install, usermod, or systemctl enable call" {
  export SUDO_USER=alice
  export OMES_ALLOW_DOCKER_GROUP=1
  export OMES_NONINTERACTIVE=1
  OMES_DRY_RUN=1 run module_apply
  [ "$status" -eq 0 ]
  run grep -c '^curl ' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run grep -c 'apt-get install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run grep -c 'systemctl enable' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run grep -c 'usermod' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}
