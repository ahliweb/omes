#!/usr/bin/env bash
# shellcheck shell=bash
# modules/hyprland-session/module.sh - installs the opt-in Hyprland/Wayland
# session as an ADDITIVE session entry, never touching Cinnamon (issue #8).
#
# The MODULE_* metadata variables below are read by lib/omes/module.sh
# after sourcing this file; ShellCheck cannot see that cross-file usage
# when analyzing this file on its own.
#
# Scope: MODULE_SCOPE=root (package installs, /usr/share/wayland-sessions,
# /usr/local/bin). MODULE_REQUIRES=(desktop-preflight apt-base) documents
# the intended ordering, but note: MODULE_REQUIRES only reorders modules
# WITHIN a single privilege-scope run (lib/omes/module.sh's
# module_filter_by_scope drops other-scope modules from run_checks/
# run_apply entirely - see bin/omes's cmd_check/cmd_install). Since
# desktop-preflight is user-scope and this module is root-scope, they never
# execute in the same `omes install` invocation, and root-scope state
# cannot assume it can read user-scope state (different state directories -
# /var/lib/omes vs ${XDG_STATE_HOME}/omes, see lib/omes/core.sh
# omes_state_dir). This module is therefore SELF-SUFFICIENT: module_check
# re-runs the full read-only battery from modules/desktop-preflight/checks.sh
# itself rather than trusting a prior desktop-preflight run's state.
# shellcheck disable=SC2034
MODULE_NAME="hyprland-session"
# shellcheck disable=SC2034
MODULE_DESCRIPTION="Opt-in Hyprland/Wayland session (additive; installs the desktop toolset and registers a session entry, never touches Cinnamon)"
# shellcheck disable=SC2034
MODULE_SCOPE="root"
# shellcheck disable=SC2034
MODULE_REQUIRES=(desktop-preflight apt-base)
# shellcheck disable=SC2034
MODULE_PROFILES=(desktop)

# shellcheck source=modules/desktop-preflight/checks.sh
source "${OMES_ROOT}/modules/desktop-preflight/checks.sh"

# ---------------------------------------------------------------------------
# Managed paths (overridable for tests - see tests/unit/hyprland-session.bats
# and tests/integration/desktop.bats: OMES_SESSION_DIR/OMES_BIN_DIR let bats
# exercise this module as simulated root, via OMES_TEST=1/OMES_FAKE_ROOT=1,
# without ever writing under the real /usr)
# ---------------------------------------------------------------------------

_hs_session_dir() {
  printf '%s\n' "${OMES_SESSION_DIR:-/usr/share/wayland-sessions}"
}

_hs_bin_dir() {
  printf '%s\n' "${OMES_BIN_DIR:-/usr/local/bin}"
}

_hs_session_file() {
  printf '%s/omes-hyprland.desktop\n' "$(_hs_session_dir)"
}

_hs_wrapper_file() {
  printf '%s/omes-hyprland-session\n' "$(_hs_bin_dir)"
}

# ---------------------------------------------------------------------------
# Package selection (apply-time; module_check has already confirmed every
# DP_HARD_PACKAGES/DP_MESA_PACKAGES/portal-framework entry is available -
# a hard-package failure there means module_check itself already failed and
# run_apply is never reached, per the check-all-then-apply contract in
# lib/omes/module.sh)
# ---------------------------------------------------------------------------

# _hs_available_packages
# Prints (one per line) the real package names to install: every hard
# package, the portal framework, whichever portal backend(s) are
# available, whichever soft toolset packages are available, and whichever
# launcher/polkit-agent candidate is available (first match wins, mirroring
# dp_check_or_candidates's preference order).
_hs_available_packages() {
  local logical real

  for logical in "${DP_HARD_PACKAGES[@]}" "${DP_MESA_PACKAGES[@]}"; do
    printf '%s\n' "$(pkg_map "$logical")"
  done
  printf '%s\n' "$(pkg_map "$DP_PORTAL_FRAMEWORK")"

  real="$(pkg_map "$DP_PORTAL_HYPRLAND")"
  _dp_pkg_available "$real" && printf '%s\n' "$real"
  real="$(pkg_map "$DP_PORTAL_GTK")"
  _dp_pkg_available "$real" && printf '%s\n' "$real"

  for logical in "${DP_SOFT_PACKAGES[@]}"; do
    real="$(pkg_map "$logical")"
    _dp_pkg_available "$real" && printf '%s\n' "$real"
  done

  for logical in "${DP_LAUNCHER_CANDIDATES[@]}"; do
    real="$(pkg_map "$logical")"
    if _dp_pkg_available "$real"; then
      printf '%s\n' "$real"
      break
    fi
  done

  for logical in "${DP_POLKIT_AGENTS[@]}"; do
    real="$(pkg_map "$logical")"
    if _dp_pkg_available "$real"; then
      printf '%s\n' "$real"
      break
    fi
  done
}

# ---------------------------------------------------------------------------
# Session file / wrapper writers (additive only; dry-run honored)
# ---------------------------------------------------------------------------

_hs_write_wrapper() {
  local wrapper
  wrapper="$(_hs_wrapper_file)"

  if omes_dry_run; then
    log_info "[dry-run] would write wrapper: ${wrapper}"
    return 0
  fi

  omes_manage_path "$wrapper"
  mkdir -p "$(dirname "$wrapper")"
  cat > "$wrapper" <<'EOF'
#!/usr/bin/env bash
# Managed by OMES (modules/hyprland-session) - do not edit by hand.
# Sets the environment Hyprland and xdg-desktop-portal expect, then execs
# Hyprland. This session entry is ADDITIVE: Cinnamon remains the default
# session and is never modified or removed by OMES.
set -e
export XDG_CURRENT_DESKTOP=Hyprland
export XDG_SESSION_DESKTOP=Hyprland
export XDG_SESSION_TYPE=wayland
exec Hyprland "$@"
EOF
  chmod 755 "$wrapper"
  log_info "hyprland-session: wrote wrapper: ${wrapper}"
}

_hs_write_session_file() {
  local session wrapper
  session="$(_hs_session_file)"
  wrapper="$(_hs_wrapper_file)"

  if omes_dry_run; then
    log_info "[dry-run] would write session entry: ${session} (Exec=${wrapper})"
    return 0
  fi

  omes_manage_path "$session"
  mkdir -p "$(dirname "$session")"
  {
    printf '[Desktop Entry]\n'
    printf 'Name=OMES Hyprland\n'
    printf 'Comment=Hyprland/Wayland session installed by OMES (Omarchy-inspired) - additive; Cinnamon remains the default and is always available\n'
    printf 'Exec=%s\n' "$wrapper"
    printf 'Type=Application\n'
    printf 'DesktopNames=Hyprland\n'
  } > "$session"
  chmod 644 "$session"
  log_info "hyprland-session: wrote session entry: ${session}"
}

# ---------------------------------------------------------------------------
# module_doctor-style best-effort crash report (folded into module_verify -
# lib/omes/module.sh's module_load contract does not call an optional
# module_doctor function, so this logic must live where it is actually
# invoked; also exposed as module_doctor() for forward-compatibility if a
# future issue wires one up)
# ---------------------------------------------------------------------------

_hs_doctor_best_effort() {
  local dir="${HOME:-}/.local/share/hyprland"
  if [[ -d "$dir" ]]; then
    log_info "hyprland-session: doctor: found ${dir} (previous Hyprland session data present)"
  else
    log_info "hyprland-session: doctor: no ${dir} found yet (Hyprland has not run for this user, or logs elsewhere)"
  fi

  if command -v journalctl >/dev/null 2>&1; then
    local crash_lines
    crash_lines="$(journalctl --user -b -p err -n 20 2>/dev/null | grep -i hyprland || true)"
    if [[ -n "$crash_lines" ]]; then
      log_warn "hyprland-session: doctor: recent user-journal error-level entries mention Hyprland:"
      log_warn "$crash_lines"
    else
      log_info "hyprland-session: doctor: no recent Hyprland error-level journal entries found (best-effort)"
    fi
  else
    log_info "hyprland-session: doctor: journalctl not available; skipping journal check (best-effort)"
  fi
  return 0
}

module_doctor() {
  _hs_doctor_best_effort
}

# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------

module_check() {
  dp_run_all_checks
}

module_apply() {
  # Re-assert the safety invariant immediately before any mutation, every
  # time - never assumed from module_check having passed a moment earlier.
  DP_HAS_FAIL=0
  dp_check_cinnamon
  if [[ "$DP_HAS_FAIL" -eq 1 ]]; then
    log_error "hyprland-session: Cinnamon fallback session is missing; refusing to apply"
    return 1
  fi

  pkg_apt_update || return $?

  local -a pkgs=()
  local p
  while IFS= read -r p; do
    [[ -n "$p" ]] && pkgs+=("$p")
  done < <(_hs_available_packages)

  pkg_install "${pkgs[@]}" || return $?

  _hs_write_wrapper || return 1
  _hs_write_session_file || return 1

  return 0
}

module_verify() {
  if omes_dry_run; then
    log_info "[dry-run] would verify: session file, wrapper, Hyprland binary resolution, Cinnamon still present"
    return 0
  fi

  local ok=1
  local session wrapper
  session="$(_hs_session_file)"
  wrapper="$(_hs_wrapper_file)"

  if [[ -r "$session" ]]; then
    log_info "hyprland-session: session file present: ${session}"
  else
    log_error "hyprland-session: session file missing: ${session}"
    ok=0
  fi

  if [[ -x "$wrapper" ]]; then
    log_info "hyprland-session: wrapper is executable: ${wrapper}"
  else
    log_error "hyprland-session: wrapper missing or not executable: ${wrapper}"
    ok=0
  fi

  if command -v Hyprland >/dev/null 2>&1; then
    log_info "hyprland-session: Hyprland binary resolves: $(command -v Hyprland)"
  else
    log_error "hyprland-session: Hyprland binary does not resolve on PATH"
    ok=0
  fi

  if command -v xdg-desktop-portal >/dev/null 2>&1; then
    log_info "hyprland-session: xdg-desktop-portal binary resolves: $(command -v xdg-desktop-portal)"
  else
    log_warn "hyprland-session: xdg-desktop-portal binary does not resolve on PATH (non-fatal here; portal backends run as their own systemd user units)"
  fi

  # Safety invariant, re-asserted again post-apply.
  DP_HAS_FAIL=0
  dp_check_cinnamon
  if [[ "$DP_HAS_FAIL" -eq 1 ]]; then
    ok=0
  fi

  _hs_doctor_best_effort

  [[ "$ok" -eq 1 ]]
}

module_rollback() {
  local session wrapper
  session="$(_hs_session_file)"
  wrapper="$(_hs_wrapper_file)"

  log_warn "hyprland-session: rollback removes only the OMES session entry and wrapper; Cinnamon, LightDM config, and the default session are never touched"

  if omes_dry_run; then
    log_info "[dry-run] would remove ${session} and ${wrapper}"
    return 0
  fi

  if [[ -f "$session" ]]; then
    rm -f "$session"
    log_info "hyprland-session: removed ${session}"
  fi
  if [[ -f "$wrapper" ]]; then
    rm -f "$wrapper"
    log_info "hyprland-session: removed ${wrapper}"
  fi

  local installed
  installed="$(state_get "module.hyprland-session.installed_packages" 2>/dev/null || true)"
  if [[ -n "$installed" ]]; then
    local -a pkgs=()
    local old_ifs="$IFS"
    IFS=':'
    read -r -a pkgs <<< "$installed"
    IFS="$old_ifs"
    log_warn "hyprland-session: OMES installed these packages and will not remove them automatically: ${pkgs[*]}"
    log_warn "hyprland-session: remove manually with: apt-get remove ${pkgs[*]}"
    log_warn "hyprland-session: or use 'omes uninstall --purge-packages' to remove them the same way"
  else
    log_info "hyprland-session: no packages were recorded as installed by OMES; nothing to report"
  fi

  # Assert the invariant one more time: rollback must never have touched it.
  DP_HAS_FAIL=0
  dp_check_cinnamon

  return 0
}
