#!/usr/bin/env bash
# shellcheck shell=bash
# modules/hermes-gateway/hardening.sh - shared systemd hardening/resource
# limit helper, sourced by both modules/hermes-gateway/module.sh (user
# mode) and modules/hermes-gateway-system/module.sh (system mode).
#
# Issue #81: opt-in "conservative"/"strict" systemd hardening profiles for
# the Hermes gateway unit, applied as a SEPARATE managed drop-in
# (20-omes-hardening.conf) from the existing PATH drop-in
# (omes-path.conf) in the same *.service.d directory. Off by default
# (OMES_HERMES_HARDENING=off). Never touches the operator-owned unit file
# itself - only a drop-in registered via omes_manage_path, so backup and
# rollback cover it like any other managed path.
#
# Every function here takes an explicit "mode" ("user" or "system") as
# its first argument instead of reading MODULE_SCOPE, because this file
# is shared by two modules with different systemctl invocations
# (`systemctl --user ...` vs `systemctl ...`) and different drop-in
# directories (`$HOME/.config/systemd/user/...` vs `/etc/systemd/system/...`).
#
# See docs/hermes-hardening.md for the full directive-by-directive
# rationale and compatibility notes this file implements.

if [[ -n "${OMES_HERMES_HARDENING_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317
  return 0 2>/dev/null || exit 0
fi
OMES_HERMES_HARDENING_SH_LOADED=1

HARDENING_UNIT="hermes-gateway"
HARDENING_DROPIN_NAME="20-omes-hardening.conf"

# ---------------------------------------------------------------------------
# systemctl invocation helper (mode-aware)
# ---------------------------------------------------------------------------

# _hardening_systemctl <mode> <args...>
_hardening_systemctl() {
  local mode="$1"
  shift
  if [[ "$mode" == "user" ]]; then
    systemctl --user "$@"
  else
    systemctl "$@"
  fi
}

# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------

# hardening_profile
# Prints the resolved profile (off|conservative|strict). Falls back to
# "off" (with a warning) for any unrecognized value - hardening is always
# an explicit opt-in, so an invalid value must never silently escalate.
hardening_profile() {
  local raw="${OMES_HERMES_HARDENING:-off}"
  case "$raw" in
    off | conservative | strict)
      printf '%s\n' "$raw"
      ;;
    *)
      log_warn "hermes-gateway hardening: unrecognized OMES_HERMES_HARDENING='${raw}'; treating as 'off' (valid: off, conservative, strict)"
      printf 'off\n'
      ;;
  esac
}

# hardening_memory_max
hardening_memory_max() {
  printf '%s\n' "${OMES_HERMES_MEMORY_MAX:-2G}"
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# hardening_dropin_dir <mode> [home-or-etc-base]
hardening_dropin_dir() {
  local mode="$1"
  local base="${2:-}"
  if [[ "$mode" == "user" ]]; then
    printf '%s/.config/systemd/user/%s.service.d\n' "${base:-$HOME}" "$HARDENING_UNIT"
  else
    printf '%s/%s.service.d\n' "${base:-${OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR:-/etc/systemd/system}}" "$HARDENING_UNIT"
  fi
}

# hardening_dropin_file <mode> [home-or-etc-base]
hardening_dropin_file() {
  local mode="$1"
  local base="${2:-}"
  printf '%s/%s\n' "$(hardening_dropin_dir "$mode" "$base")" "$HARDENING_DROPIN_NAME"
}

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# hardening_rw_paths <hermes-home>
# Prints the space-separated ReadWritePaths= value for strict mode: the
# resolved HERMES_HOME plus any operator-supplied extra paths
# (OMES_HERMES_RW_PATHS, colon-separated - e.g. a shared workspace or
# Docker CLI config dir a skill needs to write to).
hardening_rw_paths() {
  local home="$1"
  local extra="${OMES_HERMES_RW_PATHS:-}"
  local out="$home"
  if [[ -n "$extra" ]]; then
    local IFS=':'
    local p
    for p in $extra; do
      [[ -z "$p" ]] && continue
      out="${out} ${p}"
    done
  fi
  printf '%s\n' "$out"
}

# hardening_render <profile> <hermes-home>
# Prints the [Service] drop-in content for the given profile. "off"
# prints nothing (empty string) - callers must not write a file for off.
#
# Directives are deliberately conservative about compatibility
# (docs/hermes-hardening.md has the full rationale):
#   - RestrictNamespaces is never set: Chromium's sandbox needs user
#     namespaces.
#   - ProtectSystem=full (never =strict) and PrivateDevices is never set:
#     PrivateDevices would hide /dev/snd and /dev/dri (audio/video tools).
#   - Docker CLI socket access depends entirely on operator-granted group
#     membership/mount; hardening never touches that.
hardening_render() {
  local profile="$1"
  local home="$2"

  [[ "$profile" == "off" ]] && return 0

  local memory_max cpu_quota
  memory_max="$(hardening_memory_max)"
  cpu_quota="${OMES_HERMES_CPU_QUOTA:-}"

  {
    printf '[Service]\n'
    printf 'Restart=on-failure\n'
    printf 'RestartSec=5\n'
    printf 'StartLimitIntervalSec=60\n'
    printf 'StartLimitBurst=5\n'
    printf 'TasksMax=512\n'
    printf 'MemoryMax=%s\n' "$memory_max"
    printf 'MemoryHigh=%s\n' "$memory_max"
    if [[ -n "$cpu_quota" ]]; then
      printf 'CPUQuota=%s\n' "$cpu_quota"
    fi
    printf 'NoNewPrivileges=yes\n'
    printf 'UMask=0077\n'
    printf 'PrivateTmp=yes\n'
    printf 'ProtectSystem=full\n'

    if [[ "$profile" == "strict" ]]; then
      printf 'ProtectHome=read-only\n'
      printf 'ReadWritePaths=%s\n' "$(hardening_rw_paths "$home")"
      printf 'ProtectKernelTunables=yes\n'
      printf 'ProtectKernelModules=yes\n'
      printf 'ProtectLogs=yes\n'
      printf 'RestrictSUIDSGID=yes\n'
      printf 'LockPersonality=yes\n'
      printf 'SystemCallArchitectures=native\n'
      printf 'CapabilityBoundingSet=\n'
    fi
  }
}

# ---------------------------------------------------------------------------
# module_check helper: predicts incompatibilities, never fails the check
# ---------------------------------------------------------------------------

# hardening_systemd_major
# Prints the systemd major version (e.g. "255"), or empty on failure.
hardening_systemd_major() {
  command -v systemctl >/dev/null 2>&1 || return 0
  systemctl --version 2>/dev/null | head -1 | grep -oE '[0-9]+' | head -1
}

# hardening_browser_present
# True (0) if a Chromium-family browser or a Playwright browser cache is
# detected (used to warn about strict-mode sandbox incompatibility).
hardening_browser_present() {
  local b
  for b in chromium chromium-browser google-chrome; do
    command -v "$b" >/dev/null 2>&1 && return 0
  done
  [[ -d "${HOME:-}/.cache/ms-playwright" ]] && return 0
  return 1
}

# hardening_docker_socket_group
# Prints the docker group's members (getent), empty if there is no
# docker group at all.
hardening_docker_socket_group() {
  getent group docker 2>/dev/null | awk -F: '{print $4}'
}

# hardening_check <mode> <hermes-home>
# Advisory-only compatibility prediction, logged via log_info/log_warn.
# Never returns non-zero: an incompatibility is a warning an operator
# reads before opting into a stronger profile, not a preflight failure -
# the profile itself defaults to "off" and strict is never chosen for
# them.
hardening_check() {
  local mode="$1"
  local home="$2"
  local profile
  profile="$(hardening_profile)"

  if [[ "$profile" == "off" ]]; then
    log_info "hermes-gateway hardening: OMES_HERMES_HARDENING=off (default) - no drop-in will be managed"
    return 0
  fi

  log_info "hermes-gateway hardening: profile '${profile}' requested (mode=${mode})"

  local major
  major="$(hardening_systemd_major)"
  if [[ -n "$major" ]] && [[ "$major" -lt 240 ]]; then
    log_warn "hermes-gateway hardening: systemd ${major} detected; ProtectHome=/ProtectSystem= directives on a --user unit are best supported on systemd >= 240 - verify with 'systemd-analyze verify' after applying"
  fi

  if [[ "$profile" == "strict" ]]; then
    if hardening_browser_present; then
      log_warn "hermes-gateway hardening: a Chromium-family browser or Playwright cache was detected - strict mode's ProtectHome=read-only and CapabilityBoundingSet= may break browser-automation sandboxing; see docs/hermes-hardening.md before applying"
    fi
    log_warn "hermes-gateway hardening: strict mode may also break some MCP subprocess tools that expect a writable \$HOME outside \$HERMES_HOME; use OMES_HERMES_RW_PATHS to add exceptions"
  fi

  local docker_members
  docker_members="$(hardening_docker_socket_group)"
  if [[ -n "$docker_members" ]]; then
    log_info "hermes-gateway hardening: docker group members: ${docker_members} (hardening never grants or revokes this membership)"
  fi

  if command -v systemd-analyze >/dev/null 2>&1; then
    local tmp
    tmp="$(mktemp)"
    hardening_render "$profile" "$home" >"$tmp"
    systemd-analyze verify "$tmp" >/dev/null 2>&1 \
      || log_info "hermes-gateway hardening: 'systemd-analyze verify' could not fully validate a standalone drop-in fragment (expected outside a full unit context); this is informational only"
    rm -f "$tmp"
  fi

  return 0
}

# ---------------------------------------------------------------------------
# apply / verify / doctor / rollback
# ---------------------------------------------------------------------------

# hardening_apply <mode> <hermes-home> [dropin-base]
# Writes (or removes, for profile "off") the managed hardening drop-in,
# reloads the daemon, and - only with confirmation - restarts the unit,
# verifying it comes back active within a bounded timeout. On a failed
# restart, automatically rolls the drop-in back and restarts again so the
# gateway is not left down because of a hardening change (the one place
# in this module where auto-rollback is justified: it undoes only the
# change this very call just made, within the same operation, before
# control returns to the operator).
hardening_apply() {
  local mode="$1"
  local home="$2"
  local base="${3:-}"

  local profile
  profile="$(hardening_profile)"

  local dropin_file dropin_dir
  dropin_dir="$(hardening_dropin_dir "$mode" "$base")"
  dropin_file="$(hardening_dropin_file "$mode" "$base")"

  if omes_dry_run; then
    if [[ "$profile" == "off" ]]; then
      log_info "[dry-run] hardening: profile off - would ensure ${dropin_file} is absent"
    else
      log_info "[dry-run] hardening: would write ${dropin_file} for profile '${profile}' and reload+restart ${HARDENING_UNIT} (mode=${mode})"
    fi
    return 0
  fi

  if [[ "$profile" == "off" ]]; then
    if [[ -f "$dropin_file" ]]; then
      rm -f "$dropin_file"
      rmdir "$dropin_dir" 2>/dev/null || true
      _hardening_systemctl "$mode" daemon-reload
      log_info "hermes-gateway hardening: removed ${dropin_file} (profile is off)"
    fi
    state_unset "module.hermes-gateway.hardening_profile" 2>/dev/null || true
    return 0
  fi

  mkdir -p "$dropin_dir"
  omes_manage_path "$dropin_file"
  hardening_render "$profile" "$home" >"$dropin_file"
  chmod 644 "$dropin_file"
  log_info "hermes-gateway hardening: wrote ${dropin_file} (profile=${profile})"

  _hardening_systemctl "$mode" daemon-reload

  if ! omes_confirm "Restart ${HARDENING_UNIT} (mode=${mode}) now to apply the '${profile}' hardening profile?"; then
    log_warn "hermes-gateway hardening: drop-in written but the unit was not restarted (declined); it will only take effect on the next restart"
    return 0
  fi

  _hardening_systemctl "$mode" restart "$HARDENING_UNIT" || true

  local timeout="${OMES_HERMES_HARDENING_TIMEOUT:-15}"
  local waited=0
  while ((waited < timeout)); do
    if _hardening_systemctl "$mode" is-active "$HARDENING_UNIT" >/dev/null 2>&1; then
      state_set "module.hermes-gateway.hardening_profile" "$profile"
      log_info "hermes-gateway hardening: ${HARDENING_UNIT} active under profile '${profile}'"
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done

  log_error "hermes-gateway hardening: ${HARDENING_UNIT} did not become active within ${timeout}s under profile '${profile}' - automatically rolling back the hardening drop-in"
  rm -f "$dropin_file"
  rmdir "$dropin_dir" 2>/dev/null || true
  _hardening_systemctl "$mode" daemon-reload
  _hardening_systemctl "$mode" restart "$HARDENING_UNIT" >/dev/null 2>&1 || true
  state_unset "module.hermes-gateway.hardening_profile" 2>/dev/null || true
  return 1
}

# hardening_verify <mode>
# Advisory: logs the resolved runtime properties. Never fails on its own
# (the unit's active/enabled state is already the module's authoritative
# verification signal).
hardening_verify() {
  local mode="$1"
  local props
  props="$(_hardening_systemctl "$mode" show "$HARDENING_UNIT" \
    -p MemoryMax -p TasksMax -p NoNewPrivileges -p ProtectHome -p ProtectSystem 2>/dev/null || true)"
  [[ -n "$props" ]] && log_info "hermes-gateway hardening: ${props}"
  return 0
}

# hardening_doctor <mode>
# One-line `omes doctor` summary: active profile + key limits + whether
# the unit is currently running under them.
hardening_doctor() {
  local mode="$1"
  local profile
  profile="$(state_get "module.hermes-gateway.hardening_profile" 2>/dev/null || printf 'off')"
  [[ -z "$profile" ]] && profile="off"

  local active="unknown"
  if _hardening_systemctl "$mode" is-active "$HARDENING_UNIT" >/dev/null 2>&1; then
    active="active"
  else
    active="inactive"
  fi

  local mem tasks
  mem="$(_hardening_systemctl "$mode" show "$HARDENING_UNIT" -p MemoryMax --value 2>/dev/null || printf '?')"
  tasks="$(_hardening_systemctl "$mode" show "$HARDENING_UNIT" -p TasksMax --value 2>/dev/null || printf '?')"

  printf 'hardening: profile=%s unit=%s MemoryMax=%s TasksMax=%s\n' "$profile" "$active" "$mem" "$tasks"
}

# hardening_rollback <mode> [dropin-base]
# Removes only the OMES hardening drop-in (never the PATH drop-in or any
# operator-owned unit file), reloads, and restarts with confirmation.
hardening_rollback() {
  local mode="$1"
  local base="${2:-}"
  local dropin_dir dropin_file
  dropin_dir="$(hardening_dropin_dir "$mode" "$base")"
  dropin_file="$(hardening_dropin_file "$mode" "$base")"

  if omes_dry_run; then
    log_info "[dry-run] hardening: would remove ${dropin_file} and reload+restart ${HARDENING_UNIT} (mode=${mode})"
    return 0
  fi

  if [[ ! -f "$dropin_file" ]]; then
    state_unset "module.hermes-gateway.hardening_profile" 2>/dev/null || true
    return 0
  fi

  rm -f "$dropin_file"
  rmdir "$dropin_dir" 2>/dev/null || true
  _hardening_systemctl "$mode" daemon-reload
  log_info "hermes-gateway hardening: removed ${dropin_file}"

  if omes_confirm "Restart ${HARDENING_UNIT} (mode=${mode}) now that hardening has been rolled back?"; then
    _hardening_systemctl "$mode" restart "$HARDENING_UNIT" >/dev/null 2>&1 || true
  fi

  state_unset "module.hermes-gateway.hardening_profile" 2>/dev/null || true
  return 0
}
