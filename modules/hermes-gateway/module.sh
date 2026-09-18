#!/usr/bin/env bash
# shellcheck shell=bash
# modules/hermes-gateway/module.sh - Hermes gateway as a per-user systemd
# --user service (the default, least-privilege path).
#
# The MODULE_* metadata variables below are read by lib/omes/module.sh
# after sourcing this file; ShellCheck cannot see that cross-file usage
# when analyzing this file on its own.
#
# Scope: MODULE_SCOPE=user. This module NEVER runs the gateway as root.
# System-wide gateway installation (`sudo hermes gateway install
# --system`, running under a dedicated service account, never root) is a
# SEPARATE module, modules/hermes-gateway-system/, because MODULE_SCOPE is
# a single fixed value read once at module_load time and the runner
# refuses (exit 5) to run a root-scope module as non-root or a user-scope
# module as root (docs/architecture.md §4.5) - one module cannot
# legitimately serve both privilege levels. OMES_GATEWAY_MODE documents
# and guards this split (see module_check below); it does not change
# which module the runner invokes (that is decided by which module name
# is in the resolved profile/--module set, per the architecture's
# scope-filtering model).

# shellcheck disable=SC2034
MODULE_NAME="hermes-gateway"
# shellcheck disable=SC2034
MODULE_DESCRIPTION="Hermes gateway as a per-user systemd --user service (default: user mode)"
# shellcheck disable=SC2034
MODULE_SCOPE="user"
# shellcheck disable=SC2034
MODULE_REQUIRES=(hermes)
# shellcheck disable=SC2034
MODULE_PROFILES=(server desktop hermes)

HERMES_GATEWAY_UNIT="hermes-gateway"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_hgw_ensure_runtime_path() {
  case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) ;;
    *)
      PATH="${HOME}/.local/bin:${PATH}"
      export PATH
      ;;
  esac
}

# _hgw_dropin_dir
# Prints the systemd --user drop-in directory OMES manages for the unit.
_hgw_dropin_dir() {
  printf '%s/.config/systemd/user/%s.service.d\n' "${HOME}" "$HERMES_GATEWAY_UNIT"
}

# _hgw_dropin_file
# Prints the path to the OMES-managed drop-in file itself.
_hgw_dropin_file() {
  printf '%s/omes-path.conf\n' "$(_hgw_dropin_dir)"
}

# _hgw_dropin_content
# Prints the drop-in content: an explicit PATH including ~/.local/bin plus
# any operator-supplied extra directories (OMES_HERMES_GATEWAY_EXTRA_PATH,
# colon-separated - e.g. where a Node/ffmpeg install actually lives, since
# a systemd user unit does not inherit an interactive shell's PATH).
_hgw_dropin_content() {
  local path="%h/.local/bin:/usr/local/bin:/usr/bin:/bin"
  if [[ -n "${OMES_HERMES_GATEWAY_EXTRA_PATH:-}" ]]; then
    path="${OMES_HERMES_GATEWAY_EXTRA_PATH}:${path}"
  fi
  printf '[Service]\nEnvironment=PATH=%s\n' "$path"
}

# _hgw_headless
# True (0) when the current session is server/headless per
# lib/omes/detect.sh's detect_session (desktop vs server).
_hgw_headless() {
  local session
  session="$(detect_session)"
  [[ "$session" == "server" ]]
}

# _hgw_doctor_caveat <status-output>
# Always logs the "green systemctl signal does not prove the messaging
# adapter is connected" caveat (docs/hermes-integration.md part 2), and
# additionally warns when <status-output> itself looks like a disconnected
# adapter rather than a healthy one. This is the module_doctor-style check
# called from module_verify; it is intentionally advisory (never fails
# verification on its own - `hermes gateway status`'s own exit code is
# what module_verify treats as authoritative).
_hgw_doctor_caveat() {
  local status_output="$1"
  log_warn "hermes-gateway: 'systemctl --user is-active' proves the process is running, NOT that the messaging adapter (e.g. Telegram) is connected - see docs/hermes-integration.md part 2 ('green signals can lie')"
  if grep -qi 'disconnected\|not connected\|unauthorized\|unreachable' <<<"$status_output"; then
    log_warn "hermes-gateway: 'hermes gateway status' output suggests the messaging adapter may not be connected:"
    log_warn "${status_output}"
  fi
}

# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------

module_check() {
  if omes_is_root; then
    log_error "hermes-gateway: must not run as root - this module manages a per-user systemd --user service; re-run as the target user without sudo (see docs/hermes-integration.md)"
    return 1
  fi

  if [[ "${OMES_GATEWAY_MODE:-user}" == "system" ]]; then
    log_error "hermes-gateway: OMES_GATEWAY_MODE=system was requested, but this is the user-mode module; use --module hermes-gateway-system as root instead (see docs/hermes-integration.md)"
    return 1
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    log_error "hermes-gateway: systemctl not found (required to manage the --user service)"
    return 1
  fi

  _hgw_ensure_runtime_path
  if ! command -v hermes >/dev/null 2>&1 || ! hermes --version >/dev/null 2>&1; then
    log_error "hermes-gateway: the 'hermes' CLI is not installed/runnable; apply the 'hermes' module first"
    return 1
  fi

  if _hgw_headless; then
    log_info "hermes-gateway: headless/server session detected; lingering may be offered during apply"
  fi

  return 0
}

module_apply() {
  _hgw_ensure_runtime_path

  if omes_dry_run; then
    log_info "[dry-run] would run: hermes gateway install"
    log_info "[dry-run] would ensure drop-in at $(_hgw_dropin_file) and systemctl --user enable --now ${HERMES_GATEWAY_UNIT}"
    if _hgw_headless; then
      log_info "[dry-run] would offer to enable lingering via: sudo loginctl enable-linger $(id -un)"
    fi
    return 0
  fi

  if ! omes_run hermes gateway install; then
    log_error "hermes-gateway: 'hermes gateway install' failed"
    return 1
  fi

  local dropin_dir dropin_file
  dropin_dir="$(_hgw_dropin_dir)"
  dropin_file="$(_hgw_dropin_file)"
  mkdir -p "$dropin_dir"
  omes_manage_path "$dropin_file"
  _hgw_dropin_content > "$dropin_file"
  chmod 644 "$dropin_file"
  log_info "hermes-gateway: wrote PATH drop-in at ${dropin_file}"

  omes_run systemctl --user daemon-reload
  if ! omes_run systemctl --user enable --now "$HERMES_GATEWAY_UNIT"; then
    log_error "hermes-gateway: failed to enable/start the --user ${HERMES_GATEWAY_UNIT} unit"
    return 1
  fi

  if _hgw_headless; then
    local already
    already="$(state_get "module.hermes-gateway.linger_enabled" 2>/dev/null || echo "")"
    if [[ "$already" != "true" ]]; then
      local user
      user="$(id -un)"
      if omes_confirm "This is a headless/server host. Enable lingering for '${user}' (sudo loginctl enable-linger ${user}) so the gateway survives logout/reboot?"; then
        if omes_run sudo loginctl enable-linger "$user"; then
          state_set "module.hermes-gateway.linger_enabled" "true"
          log_info "hermes-gateway: lingering enabled for ${user}"
        else
          log_warn "hermes-gateway: 'sudo loginctl enable-linger ${user}' failed; the gateway will stop when you log out"
          state_set "module.hermes-gateway.linger_enabled" "false"
        fi
      else
        log_warn "hermes-gateway: lingering not enabled (declined); the gateway will stop when you log out of this headless host. Enable it later with: sudo loginctl enable-linger ${user}"
        state_set "module.hermes-gateway.linger_enabled" "false"
      fi
    fi
  fi

  state_set "module.hermes-gateway.mode" "user"
  return 0
}

module_verify() {
  if omes_dry_run; then
    log_info "[dry-run] would verify: systemctl --user is-enabled/is-active ${HERMES_GATEWAY_UNIT}, hermes gateway status"
    return 0
  fi

  _hgw_ensure_runtime_path

  if ! systemctl --user is-enabled "$HERMES_GATEWAY_UNIT" >/dev/null 2>&1; then
    log_error "hermes-gateway: --user unit '${HERMES_GATEWAY_UNIT}' is not enabled"
    return 1
  fi

  if ! systemctl --user is-active "$HERMES_GATEWAY_UNIT" >/dev/null 2>&1; then
    log_error "hermes-gateway: --user unit '${HERMES_GATEWAY_UNIT}' is not active"
    return 1
  fi

  local status_output
  if ! status_output="$(hermes gateway status 2>&1)"; then
    log_error "hermes-gateway: 'hermes gateway status' failed:"
    log_error "${status_output}"
    return 1
  fi
  log_info "hermes-gateway: status: ${status_output}"

  _hgw_doctor_caveat "$status_output"

  return 0
}

module_rollback() {
  log_warn "hermes-gateway: rollback stops/disables the OMES-managed --user service and removes OMES's drop-in; it never removes Hermes itself"

  if omes_dry_run; then
    log_info "[dry-run] would stop/disable ${HERMES_GATEWAY_UNIT} (--user), remove $(_hgw_dropin_file), and disable lingering if OMES enabled it"
    return 0
  fi

  _hgw_ensure_runtime_path

  systemctl --user stop "$HERMES_GATEWAY_UNIT" >/dev/null 2>&1 || true
  systemctl --user disable "$HERMES_GATEWAY_UNIT" >/dev/null 2>&1 || true

  local dropin_file
  dropin_file="$(_hgw_dropin_file)"
  if [[ -f "$dropin_file" ]]; then
    rm -f "$dropin_file"
    log_info "hermes-gateway: removed ${dropin_file}"
  fi
  rmdir "$(_hgw_dropin_dir)" 2>/dev/null || true

  local linger
  linger="$(state_get "module.hermes-gateway.linger_enabled" 2>/dev/null || echo "")"
  if [[ "$linger" == "true" ]]; then
    local user
    user="$(id -un)"
    if omes_run sudo loginctl disable-linger "$user"; then
      log_info "hermes-gateway: disabled lingering for ${user} (OMES had enabled it)"
    else
      log_warn "hermes-gateway: failed to disable lingering for ${user}; run manually: sudo loginctl disable-linger ${user}"
    fi
  fi

  state_unset "module.hermes-gateway.linger_enabled"
  state_unset "module.hermes-gateway.mode"

  log_warn "hermes-gateway: Hermes itself and \$HERMES_HOME are untouched; to fully remove the gateway's own state, see 'hermes gateway --help'"
  return 0
}
