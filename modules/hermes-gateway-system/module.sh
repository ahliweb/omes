#!/usr/bin/env bash
# shellcheck shell=bash
# modules/hermes-gateway-system/module.sh - Hermes gateway as a SYSTEM
# systemd service, for a headless host where the gateway must survive
# reboot independent of any single user's session/lingering.
#
# The MODULE_* metadata variables below are read by lib/omes/module.sh
# after sourcing this file; ShellCheck cannot see that cross-file usage
# when analyzing this file on its own.
#
# Scope: MODULE_SCOPE=root (the `--system` install itself requires root:
# `sudo hermes gateway install --system`). This is a SEPARATE module from
# modules/hermes-gateway (MODULE_SCOPE=user, the default path) rather than
# one module with an internal root/user branch, because MODULE_SCOPE is a
# single fixed value the runner enforces before module_check ever runs
# (docs/architecture.md §4.5) - a module cannot legitimately serve both
# privilege levels. This module is intentionally NOT wired into any
# profiles/*.profile by default (see docs/hermes-integration.md part 2's
# decision table); an operator opts in explicitly with
# `sudo omes install --module hermes-gateway-system`.
#
# Least privilege: this module never runs the gateway process itself as
# root. Upstream `hermes gateway install --system` runs the systemd
# SYSTEM unit under a dedicated, non-root service account (see upstream
# Hermes docs) - this module only performs the root-required installation
# step and refuses outright to target the root account as that service
# identity (OMES_HERMES_GATEWAY_SYSTEM_USER must name a real, non-root,
# existing user).

# shellcheck disable=SC2034
MODULE_NAME="hermes-gateway-system"
# shellcheck disable=SC2034
MODULE_DESCRIPTION="Hermes gateway as a SYSTEM systemd service (opt-in; headless hosts without a persistent login session)"
# shellcheck disable=SC2034
MODULE_SCOPE="root"
# MODULE_REQUIRES intentionally empty: the `hermes` module is user-scope
# and lives in a different (unreadable-to-root) state directory, so a
# cross-scope MODULE_REQUIRES edge cannot be satisfied here - see
# docs/architecture.md §13's documented limitation. module_check instead
# verifies the observable effect (a `hermes` binary reachable for the
# target user).
# shellcheck disable=SC2034
MODULE_REQUIRES=()
# shellcheck disable=SC2034
MODULE_PROFILES=(server)

HERMES_GATEWAY_UNIT="hermes-gateway"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# _hgws_target_user
# Prints OMES_HERMES_GATEWAY_SYSTEM_USER (no default - the operator must
# name the account explicitly; see module_check).
_hgws_target_user() {
  printf '%s\n' "${OMES_HERMES_GATEWAY_SYSTEM_USER:-}"
}

# _hgws_user_exists_and_not_root <user>
# True when <user> resolves to a real account with a non-zero UID.
_hgws_user_exists_and_not_root() {
  local user="$1"
  [[ -n "$user" ]] || return 1
  [[ "$user" != "root" ]] || return 1
  local uid
  uid="$(id -u "$user" 2>/dev/null)" || return 1
  [[ "$uid" != "0" ]]
}

# _hgws_user_home <user>
_hgws_user_home() {
  getent passwd "$1" 2>/dev/null | awk -F: '{print $6}'
}

# _hgws_dropin_dir
# Prints the system drop-in directory. Honors
# OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR (tests only; production always
# uses the real /etc/systemd/system location) - same pattern as
# lib/omes/pkg.sh's OMES_APT_SOURCES_DIR, so unit tests never need real
# root/filesystem access to /etc to exercise this module.
_hgws_dropin_dir() {
  printf '%s/%s.service.d\n' "${OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR:-/etc/systemd/system}" "$HERMES_GATEWAY_UNIT"
}

_hgws_dropin_file() {
  printf '%s/omes-path.conf\n' "$(_hgws_dropin_dir)"
}

# _hgws_dropin_content <user-home>
_hgws_dropin_content() {
  local home="$1"
  local path="${home}/.local/bin:/usr/local/bin:/usr/bin:/bin"
  if [[ -n "${OMES_HERMES_GATEWAY_EXTRA_PATH:-}" ]]; then
    path="${OMES_HERMES_GATEWAY_EXTRA_PATH}:${path}"
  fi
  printf '[Service]\nEnvironment=PATH=%s\n' "$path"
}

_hgws_doctor_caveat() {
  log_warn "hermes-gateway-system: a green 'systemctl is-active' does NOT prove the messaging adapter (e.g. Telegram) is connected - it only proves the process is running. See docs/hermes-integration.md part 2 ('green signals can lie'). Verify with: sudo -u <user> hermes gateway status, or journalctl -u ${HERMES_GATEWAY_UNIT} -f"
}

# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------

module_check() {
  if ! omes_is_root; then
    log_error "hermes-gateway-system: requires root - run via: sudo omes install --module hermes-gateway-system"
    return 1
  fi

  local user
  user="$(_hgws_target_user)"
  if [[ -z "$user" ]]; then
    log_error "hermes-gateway-system: OMES_HERMES_GATEWAY_SYSTEM_USER must be set to the non-root account whose Hermes install the system service will run (see docs/hermes-integration.md)"
    return 1
  fi

  if ! _hgws_user_exists_and_not_root "$user"; then
    log_error "hermes-gateway-system: refusing to configure a system gateway for user '${user}' - it must be an existing, non-root account (OMES never runs the gateway as root)"
    return 1
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    log_error "hermes-gateway-system: systemctl not found"
    return 1
  fi

  if ! command -v hermes >/dev/null 2>&1; then
    log_error "hermes-gateway-system: 'hermes' is not reachable on root's PATH. The Hermes CLI installs per-user (modules/hermes, issue #11); make it reachable for a system install (e.g. a symlink documented in docs/hermes-integration.md) before applying this module"
    return 1
  fi

  return 0
}

module_apply() {
  local user home
  user="$(_hgws_target_user)"
  home="$(_hgws_user_home "$user")"

  if omes_dry_run; then
    log_info "[dry-run] would run: hermes gateway install --system (target user: ${user})"
    log_info "[dry-run] would ensure drop-in at $(_hgws_dropin_file) and systemctl enable --now ${HERMES_GATEWAY_UNIT}"
    return 0
  fi

  if ! omes_run hermes gateway install --system; then
    log_error "hermes-gateway-system: 'hermes gateway install --system' failed"
    return 1
  fi

  local dropin_dir dropin_file
  dropin_dir="$(_hgws_dropin_dir)"
  dropin_file="$(_hgws_dropin_file)"
  mkdir -p "$dropin_dir"
  omes_manage_path "$dropin_file"
  _hgws_dropin_content "$home" >"$dropin_file"
  chmod 644 "$dropin_file"
  log_info "hermes-gateway-system: wrote PATH drop-in at ${dropin_file} for user ${user}"

  omes_run systemctl daemon-reload
  if ! omes_run systemctl enable --now "$HERMES_GATEWAY_UNIT"; then
    log_error "hermes-gateway-system: failed to enable/start the system ${HERMES_GATEWAY_UNIT} unit"
    return 1
  fi

  state_set "module.hermes-gateway-system.mode" "system"
  state_set "module.hermes-gateway-system.target_user" "$user"
  return 0
}

module_verify() {
  if omes_dry_run; then
    log_info "[dry-run] would verify: systemctl is-enabled/is-active ${HERMES_GATEWAY_UNIT}"
    return 0
  fi

  if ! systemctl is-enabled "$HERMES_GATEWAY_UNIT" >/dev/null 2>&1; then
    log_error "hermes-gateway-system: system unit '${HERMES_GATEWAY_UNIT}' is not enabled"
    return 1
  fi

  if ! systemctl is-active "$HERMES_GATEWAY_UNIT" >/dev/null 2>&1; then
    log_error "hermes-gateway-system: system unit '${HERMES_GATEWAY_UNIT}' is not active"
    return 1
  fi

  _hgws_doctor_caveat
  return 0
}

module_rollback() {
  log_warn "hermes-gateway-system: rollback stops/disables the OMES-managed system service and removes OMES's drop-in; it never removes Hermes itself or the target user's data"

  if omes_dry_run; then
    log_info "[dry-run] would stop/disable ${HERMES_GATEWAY_UNIT} (system) and remove $(_hgws_dropin_file)"
    return 0
  fi

  systemctl stop "$HERMES_GATEWAY_UNIT" >/dev/null 2>&1 || true
  systemctl disable "$HERMES_GATEWAY_UNIT" >/dev/null 2>&1 || true

  local dropin_file
  dropin_file="$(_hgws_dropin_file)"
  if [[ -f "$dropin_file" ]]; then
    rm -f "$dropin_file"
    log_info "hermes-gateway-system: removed ${dropin_file}"
  fi
  rmdir "$(_hgws_dropin_dir)" 2>/dev/null || true

  state_unset "module.hermes-gateway-system.mode"
  state_unset "module.hermes-gateway-system.target_user"

  return 0
}
