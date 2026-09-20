#!/usr/bin/env bats
# tests/unit/security-baseline.bats - modules/security-baseline/module.sh
# unit tests (#7).
#
# Sources lib/omes/*.sh and modules/security-baseline/module.sh directly
# (module_load), like tests/unit/hermes-gateway.bats. OMES_ETC_DIR is
# ALWAYS overridden to an isolated tmpdir so the module never touches the
# real /etc. tests/shims/{ufw,sshd,who,timedatectl,systemctl,journalctl,
# apt-get,apt-cache,dpkg-query,curl} are on PATH via test_helper.bash's
# omes_test_setup.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  export OMES_ETC_DIR="${OMES_TEST_TMPDIR}/etc"
  export SHIM_UFW_STATE_FILE="${OMES_TEST_TMPDIR}/ufw-state"
  unset SSH_CONNECTION SSH_TTY OMES_ENABLE_SSH OMES_INSTALL_CHRONY \
    OMES_UNATTENDED_AUTO_REBOOT OMES_JOURNALD_MAX_USE SHIM_WHO_AM_I_OUTPUT \
    SHIM_SYSTEM_ACTIVE_FILE SHIM_SYSTEM_ENABLED_FILE SHIM_TIMEDATECTL_NTP_SYNC \
    SHIM_SSHD_PORT || true

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
  module_load security-baseline
  declare -ga OMES_MANAGED_PATHS=()
}

teardown() {
  omes_test_teardown
}

# A small helper so tests do not repeat the "packages already present"
# boilerplate every time they only care about firewall/config behavior.
_mark_packages_installed() {
  printf 'ufw\nunattended-upgrades\n' >>"$SHIM_INSTALLED_PKGS_FILE"
}

# --- SSH lockout guard -----------------------------------------------------

@test "module_apply adds the OpenSSH ufw rule BEFORE enabling, when the ssh service is active" {
  _mark_packages_installed
  export SHIM_SYSTEM_ACTIVE_FILE="${OMES_TEST_TMPDIR}/sys-active"
  printf 'ssh\n' >"$SHIM_SYSTEM_ACTIVE_FILE"

  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"SSH access kept open"* ]]

  run grep -c '^ufw allow OpenSSH$' "$SHIM_LOG"
  [ "$output" -eq 1 ]

  allow_line="$(grep -n '^ufw allow OpenSSH$' "$SHIM_LOG" | head -n1 | cut -d: -f1)"
  enable_line="$(grep -n '^ufw --force enable$' "$SHIM_LOG" | head -n1 | cut -d: -f1)"
  [ -n "$allow_line" ]
  [ -n "$enable_line" ]
  [ "$allow_line" -lt "$enable_line" ]
}

@test "module_apply adds the OpenSSH ufw rule when SSH_CONNECTION is set" {
  _mark_packages_installed
  export SSH_CONNECTION="203.0.113.5 51000 203.0.113.10 22"

  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"SSH_CONNECTION/SSH_TTY set"* ]]
  run grep -c '^ufw allow OpenSSH$' "$SHIM_LOG"
  [ "$output" -eq 1 ]
}

@test "module_apply adds the OpenSSH ufw rule when 'who am i' reports a remote host" {
  _mark_packages_installed
  export SHIM_WHO_AM_I_OUTPUT="operator  pts/0        2026-09-18 10:00 (203.0.113.5)"

  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"'who am i' reports a remote host"* ]]
  run grep -c '^ufw allow OpenSSH$' "$SHIM_LOG"
  [ "$output" -eq 1 ]
}

@test "module_apply adds no SSH rule when neither an SSH session nor OMES_ENABLE_SSH is present" {
  _mark_packages_installed

  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" != *"SSH access kept open"* ]]
  run grep -c 'ufw allow' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "OMES_ENABLE_SSH=1 keeps SSH open even with no detected session" {
  _mark_packages_installed
  export OMES_ENABLE_SSH=1

  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"OMES_ENABLE_SSH=1 was requested"* ]]
  run grep -c '^ufw allow OpenSSH$' "$SHIM_LOG"
  [ "$output" -eq 1 ]
}

@test "a custom sshd port is detected and allowed instead of OpenSSH/22" {
  _mark_packages_installed
  export SSH_CONNECTION="203.0.113.5 51000 203.0.113.10 2222"
  export SHIM_SSHD_PORT=2222

  run module_apply
  [ "$status" -eq 0 ]
  run grep -c '^ufw allow 2222/tcp$' "$SHIM_LOG"
  [ "$output" -eq 1 ]
  run grep -c 'ufw allow OpenSSH' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "module_apply never removes/denies an existing SSH allowance (re-run with SSH no longer detected)" {
  _mark_packages_installed
  export SSH_CONNECTION="203.0.113.5 51000 203.0.113.10 22"
  module_apply >/dev/null
  unset SSH_CONNECTION

  : >"$SHIM_LOG"
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c 'ufw delete' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  # The rule is still present afterwards.
  run ufw status verbose
  [[ "$output" == *"OpenSSH"* ]]
}

# --- Idempotency / dry-run ---------------------------------------------------

@test "ufw --force enable never happens under dry-run" {
  OMES_DRY_RUN=1 run module_apply
  [ "$status" -eq 0 ]
  run grep -c 'ufw --force enable' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "dry-run makes no apt-get install call either" {
  OMES_DRY_RUN=1 run module_apply
  [ "$status" -eq 0 ]
  run grep -c 'apt-get install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "a re-run after a clean apply makes no further mutating ufw or apt-get calls" {
  _mark_packages_installed
  run module_apply
  [ "$status" -eq 0 ]

  : >"$SHIM_LOG"
  run module_apply
  [ "$status" -eq 0 ]

  run grep -Ec 'ufw (allow|default|--force enable|^enable|disable)' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run grep -c 'apt-get install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

# --- Journald hygiene --------------------------------------------------------

@test "journald is restarted only when the drop-in content actually changes" {
  _mark_packages_installed

  run module_apply
  [ "$status" -eq 0 ]
  run grep -c 'systemctl restart systemd-journald' "$SHIM_LOG"
  [ "$output" -eq 1 ]

  : >"$SHIM_LOG"
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c 'systemctl restart systemd-journald' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]

  : >"$SHIM_LOG"
  OMES_JOURNALD_MAX_USE=1G run module_apply
  [ "$status" -eq 0 ]
  run grep -c 'systemctl restart systemd-journald' "$SHIM_LOG"
  [ "$output" -eq 1 ]
}

@test "the journald drop-in sets SystemMaxUse=500M and Compress=yes by default" {
  content="$(_security_journald_conf_content)"
  [[ "$content" == *"SystemMaxUse=500M"* ]]
  [[ "$content" == *"Compress=yes"* ]]
}

# --- Update policy content ---------------------------------------------------

@test "the unattended-upgrades conf targets the security pocket only, no auto-reboot by default" {
  content="$(_security_unattended_conf_content)"
  [[ "$content" == *'"${distro_id}:${distro_codename}-security"'* ]]
  [[ "$content" == *'Automatic-Reboot "false"'* ]]
}

@test "OMES_UNATTENDED_AUTO_REBOOT=1 enables reboot at the configured time" {
  content="$(OMES_UNATTENDED_AUTO_REBOOT=1 OMES_UNATTENDED_REBOOT_TIME=03:30 _security_unattended_conf_content)"
  [[ "$content" == *'Automatic-Reboot "true"'* ]]
  [[ "$content" == *'Automatic-Reboot-Time "03:30"'* ]]
}

@test "module_apply never writes the distro-owned 50unattended-upgrades or 20auto-upgrades files" {
  _mark_packages_installed
  run module_apply
  [ "$status" -eq 0 ]
  [ ! -e "${OMES_ETC_DIR}/apt/apt.conf.d/50unattended-upgrades" ]
  [ ! -e "${OMES_ETC_DIR}/apt/apt.conf.d/20auto-upgrades" ]
}

@test "OMES_INSTALL_CHRONY=1 adds chrony to the install list" {
  _mark_packages_installed
  OMES_INSTALL_CHRONY=1 run module_apply
  [ "$status" -eq 0 ]
  run grep 'apt-get install' "$SHIM_LOG"
  [[ "$output" == *"chrony"* ]]
}

@test "chrony is not installed unless OMES_INSTALL_CHRONY=1" {
  _mark_packages_installed
  run module_apply
  [ "$status" -eq 0 ]
  run grep 'apt-get install' "$SHIM_LOG"
  [[ "$output" != *"chrony"* ]]
}

# --- module_check is mutation-free -------------------------------------------

@test "module_check makes no ufw mutation, no state write, and no config file write" {
  run module_check
  [ "$status" -eq 0 ]
  run grep -Ec 'ufw (allow|default|--force enable|^enable|disable)' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run state_get "module.security-baseline.ufw_enabled_by_omes"
  [ "$status" -eq 1 ]
  [ ! -f "$(_security_apt_conf_path)" ]
  [ ! -f "$(_security_journald_conf_path)" ]
}

@test "module_check warns but does not fail when NTP is not synchronized" {
  export SHIM_TIMEDATECTL_NTP_SYNC=no
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"* ]]
  [[ "$output" == *"NTP"* ]]
}

# --- module_verify ------------------------------------------------------------

@test "module_verify fails when ufw is not active" {
  run module_verify
  [ "$status" -eq 1 ]
  [[ "$output" == *"ufw is not active"* ]]
}

@test "module_verify passes after a full apply" {
  _mark_packages_installed
  module_apply >/dev/null
  run module_verify
  [ "$status" -eq 0 ]
}

@test "module_verify fails when SSH should be allowed but no rule exists" {
  _mark_packages_installed
  module_apply >/dev/null
  export SSH_CONNECTION="203.0.113.5 51000 203.0.113.10 22"
  run module_verify
  [ "$status" -eq 1 ]
  [[ "$output" == *"no matching ufw ALLOW rule"* ]]
}

# --- module_rollback -----------------------------------------------------------

@test "module_rollback removes the OMES-owned config files but leaves packages installed" {
  _mark_packages_installed
  module_apply >/dev/null
  local apt_conf journald_conf
  apt_conf="$(_security_apt_conf_path)"
  journald_conf="$(_security_journald_conf_path)"
  [ -f "$apt_conf" ]
  [ -f "$journald_conf" ]

  run module_rollback
  [ "$status" -eq 0 ]
  [ ! -f "$apt_conf" ]
  [ ! -f "$journald_conf" ]
  [[ "$output" == *"apt-get remove"* ]]
}

@test "module_rollback disables ufw only when OMES itself enabled it" {
  _mark_packages_installed
  module_apply >/dev/null
  run state_get "module.security-baseline.ufw_enabled_by_omes"
  [ "$output" = "1" ]

  run module_rollback
  [ "$status" -eq 0 ]
  run grep -c '^ufw disable$' "$SHIM_LOG"
  [ "$output" -eq 1 ]
  run state_get "module.security-baseline.ufw_enabled_by_omes"
  [ "$status" -eq 1 ]
}

@test "module_rollback leaves ufw alone when it was already active before OMES ran" {
  _mark_packages_installed
  mkdir -p "$(dirname "$SHIM_UFW_STATE_FILE")"
  printf 'enabled=1\ndefault_in=deny\ndefault_out=allow\n' >"$SHIM_UFW_STATE_FILE"

  module_apply >/dev/null
  run state_get "module.security-baseline.ufw_enabled_by_omes"
  [ "$status" -eq 1 ]

  : >"$SHIM_LOG"
  run module_rollback
  [ "$status" -eq 0 ]
  run grep -c 'ufw disable' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "module_rollback honors dry-run: nothing removed, nothing disabled" {
  _mark_packages_installed
  module_apply >/dev/null
  local apt_conf
  apt_conf="$(_security_apt_conf_path)"

  OMES_DRY_RUN=1 run module_rollback
  [ "$status" -eq 0 ]
  [ -f "$apt_conf" ]
  run grep -c 'ufw disable' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

# --- module_doctor (optional) ---------------------------------------------------

@test "module_doctor reports firewall state, pending updates, and NTP state without failing" {
  _mark_packages_installed
  module_apply >/dev/null
  run module_doctor
  [ "$status" -eq 0 ]
  [[ "$output" == *"firewall:"* ]]
  [[ "$output" == *"NTP synchronized:"* ]]
}

# --- fresh install ---------------------------------------------------------

# Regression: every module_check runs before ANY module_apply, so on a
# fresh host ufw is not installed yet when security-baseline is checked.
# The check must pass as long as the packages are installable; requiring
# the ufw binary made a first-ever `omes install --profile server` exit 4.
@test "module_check passes on a fresh host where ufw is not installed yet but is available in repos" {
  # Nothing recorded as installed; the shims report packages as available.
  : >"$SHIM_INSTALLED_PKGS_FILE"
  local bindir="${OMES_TEST_TMPDIR}/no-ufw-bin"
  mkdir -p "$bindir"
  # Build a PATH that has every shim EXCEPT ufw, so `command -v ufw` fails.
  local d
  for d in "${OMES_TEST_ROOT}/tests/shims/"*; do
    [[ "$(basename "$d")" == "ufw" ]] && continue
    ln -s "$d" "${bindir}/$(basename "$d")"
  done
  PATH="${bindir}:$(printf '%s' "$PATH" | tr ':' '\n' | grep -v '/tests/shims$' | paste -sd: -)" \
    run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"missing packages: ufw"* ]]
  [[ "$output" == *"ufw would be enabled"* ]]
}
