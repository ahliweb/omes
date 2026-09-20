#!/usr/bin/env bats
# tests/unit/hardening.bats - modules/hermes-gateway/hardening.sh unit tests.
#
# Sources lib/omes/*.sh and modules/hermes-gateway/hardening.sh directly.
# HOME is always overridden to an isolated tmpdir.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"

  export SHIM_USER_ENABLED_FILE="${OMES_TEST_TMPDIR}/user-enabled"
  export SHIM_USER_ACTIVE_FILE="${OMES_TEST_TMPDIR}/user-active"
  export SHIM_SYSTEM_ENABLED_FILE="${OMES_TEST_TMPDIR}/system-enabled"
  export SHIM_SYSTEM_ACTIVE_FILE="${OMES_TEST_TMPDIR}/system-active"

  unset OMES_HERMES_HARDENING OMES_HERMES_MEMORY_MAX OMES_HERMES_CPU_QUOTA OMES_HERMES_RW_PATHS OMES_HERMES_HARDENING_TIMEOUT || true

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
  # shellcheck source=../../modules/hermes-gateway/hardening.sh
  source "${OMES_TEST_ROOT}/modules/hermes-gateway/hardening.sh"

  declare -ga OMES_MANAGED_PATHS=()
}

teardown() {
  omes_test_teardown
}

# --- profile resolution ------------------------------------------------------

@test "hardening_profile defaults to off" {
  run hardening_profile
  [ "$status" -eq 0 ]
  [ "$output" = "off" ]
}

@test "hardening_profile accepts conservative and strict" {
  export OMES_HERMES_HARDENING=conservative
  run hardening_profile
  [ "$output" = "conservative" ]

  export OMES_HERMES_HARDENING=strict
  run hardening_profile
  [ "$output" = "strict" ]
}

@test "hardening_profile falls back to off on an invalid value" {
  export OMES_HERMES_HARDENING=nonsense
  run hardening_profile
  [ "$status" -eq 0 ]
  [[ "$output" == *"off"* ]]
  # The last printed line is the actual resolved value on stdout; the
  # warning above it goes to the same captured stream via log_warn.
  [ "$(printf '%s\n' "$output" | tail -1)" = "off" ]
}

@test "strict is never the resolved profile without explicit opt-in" {
  run hardening_profile
  [ "$output" != "strict" ]
}

# --- rendering ---------------------------------------------------------------

@test "off renders nothing" {
  run hardening_render "off" "${HOME}/.hermes"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "conservative renders expected directives and never sets RestrictNamespaces/PrivateDevices/ProtectSystem=strict" {
  run hardening_render "conservative" "${HOME}/.hermes"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Restart=on-failure"* ]]
  [[ "$output" == *"RestartSec=5"* ]]
  [[ "$output" == *"TasksMax=512"* ]]
  [[ "$output" == *"MemoryMax=2G"* ]]
  [[ "$output" == *"NoNewPrivileges=yes"* ]]
  [[ "$output" == *"UMask=0077"* ]]
  [[ "$output" == *"PrivateTmp=yes"* ]]
  [[ "$output" == *"ProtectSystem=full"* ]]
  [[ "$output" != *"RestrictNamespaces"* ]]
  [[ "$output" != *"PrivateDevices"* ]]
  [[ "$output" != *"ProtectSystem=strict"* ]]
  [[ "$output" != *"ProtectHome"* ]]
}

@test "MemoryMax/MemoryHigh are configurable via OMES_HERMES_MEMORY_MAX without secrets" {
  export OMES_HERMES_MEMORY_MAX="4G"
  run hardening_render "conservative" "${HOME}/.hermes"
  [[ "$output" == *"MemoryMax=4G"* ]]
  [[ "$output" == *"MemoryHigh=4G"* ]]
}

@test "CPUQuota is included only when OMES_HERMES_CPU_QUOTA is set" {
  run hardening_render "conservative" "${HOME}/.hermes"
  [[ "$output" != *"CPUQuota"* ]]

  export OMES_HERMES_CPU_QUOTA="50%"
  run hardening_render "conservative" "${HOME}/.hermes"
  [[ "$output" == *"CPUQuota=50%"* ]]
}

@test "strict adds ProtectHome=read-only with ReadWritePaths including HERMES_HOME" {
  run hardening_render "strict" "${HOME}/.hermes"
  [[ "$output" == *"ProtectHome=read-only"* ]]
  [[ "$output" == *"ReadWritePaths=${HOME}/.hermes"* ]]
  [[ "$output" == *"ProtectKernelTunables=yes"* ]]
  [[ "$output" == *"ProtectKernelModules=yes"* ]]
  [[ "$output" == *"ProtectLogs=yes"* ]]
  [[ "$output" == *"RestrictSUIDSGID=yes"* ]]
  [[ "$output" == *"LockPersonality=yes"* ]]
  [[ "$output" == *"SystemCallArchitectures=native"* ]]
  [[ "$output" == *"CapabilityBoundingSet="* ]]
}

@test "strict includes configured extra RW paths" {
  export OMES_HERMES_RW_PATHS="/opt/shared:/tmp/extra"
  run hardening_render "strict" "${HOME}/.hermes"
  [[ "$output" == *"ReadWritePaths=${HOME}/.hermes /opt/shared /tmp/extra"* ]]
}

# --- module_check-style prediction -------------------------------------------

@test "hardening_check never fails (advisory only) when off" {
  run hardening_check "user" "${HOME}/.hermes"
  [ "$status" -eq 0 ]
  [[ "$output" == *"off (default)"* ]]
}

@test "hardening_check warns about browser incompatibility for strict when a browser is present" {
  mkdir -p "${OMES_TEST_TMPDIR}/bin"
  cat >"${OMES_TEST_TMPDIR}/bin/chromium" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "${OMES_TEST_TMPDIR}/bin/chromium"
  export PATH="${OMES_TEST_TMPDIR}/bin:${PATH}"
  export OMES_HERMES_HARDENING=strict

  run hardening_check "user" "${HOME}/.hermes"
  [ "$status" -eq 0 ]
  [[ "$output" == *"browser"* ]] || [[ "$output" == *"Playwright"* ]]
}

@test "hardening_check tolerates missing optional tools (no browser, no docker group)" {
  export OMES_HERMES_HARDENING=conservative
  run hardening_check "user" "${HOME}/.hermes"
  [ "$status" -eq 0 ]
}

# --- apply / rollback --------------------------------------------------------

@test "off does not write a drop-in" {
  export OMES_NONINTERACTIVE=1
  local dropin
  dropin="$(hardening_dropin_file "user")"
  run hardening_apply "user" "${HOME}/.hermes"
  [ "$status" -eq 0 ]
  [ ! -f "$dropin" ]
}

@test "conservative writes the managed drop-in separate from the PATH drop-in" {
  export OMES_NONINTERACTIVE=1
  export OMES_HERMES_HARDENING=conservative
  run hardening_apply "user" "${HOME}/.hermes"
  [ "$status" -eq 0 ]

  local dropin
  dropin="$(hardening_dropin_file "user")"
  [ -f "$dropin" ]
  [[ "$dropin" == *"20-omes-hardening.conf" ]]
  grep -q "Restart=on-failure" "$dropin"
}

@test "failed restart triggers automatic drop-in rollback" {
  export OMES_NONINTERACTIVE=1
  export OMES_HERMES_HARDENING=conservative
  export OMES_HERMES_HARDENING_TIMEOUT=1
  export SHIM_RESTART_FAILS=1
  # The shim's restart never marks the unit active: is-active keeps
  # failing, forcing the bounded-timeout auto-rollback path.
  run hardening_apply "user" "${HOME}/.hermes"
  [ "$status" -eq 1 ]

  local dropin
  dropin="$(hardening_dropin_file "user")"
  [ ! -f "$dropin" ]
}

@test "rollback removes only the OMES hardening drop-in" {
  export OMES_NONINTERACTIVE=1
  export OMES_HERMES_HARDENING=conservative
  local dropin_dir path_dropin
  dropin_dir="$(hardening_dropin_dir "user")"
  mkdir -p "$dropin_dir"
  path_dropin="${dropin_dir}/omes-path.conf"
  printf '[Service]\nEnvironment=PATH=/x\n' >"$path_dropin"

  run hardening_apply "user" "${HOME}/.hermes"
  [ "$status" -eq 0 ]

  run hardening_rollback "user"
  [ "$status" -eq 0 ]

  local hardening_dropin
  hardening_dropin="$(hardening_dropin_file "user")"
  [ ! -f "$hardening_dropin" ]
  [ -f "$path_dropin" ]
}

@test "system-mode path uses /etc/systemd/system (overridable for tests)" {
  export OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR="${OMES_TEST_TMPDIR}/etc/systemd/system"
  export OMES_NONINTERACTIVE=1
  export OMES_HERMES_HARDENING=conservative

  run hardening_apply "system" "${HOME}/.hermes"
  [ "$status" -eq 0 ]

  local dropin
  dropin="$(hardening_dropin_file "system")"
  [[ "$dropin" == "${OMES_TEST_TMPDIR}/etc/systemd/system/"* ]]
  [ -f "$dropin" ]
}

@test "limits are configurable via env without secrets in the rendered drop-in" {
  export OMES_NONINTERACTIVE=1
  export OMES_HERMES_HARDENING=conservative
  export OMES_HERMES_MEMORY_MAX="1500M"
  run hardening_apply "user" "${HOME}/.hermes"
  [ "$status" -eq 0 ]

  local dropin
  dropin="$(hardening_dropin_file "user")"
  grep -q "MemoryMax=1500M" "$dropin"
  ! grep -qiE 'token|secret|password' "$dropin"
}
