#!/usr/bin/env bash
# shellcheck shell=bash
# modules/desktop-preflight/module.sh - read-only desktop readiness checks
# for the opt-in Hyprland/Wayland session (issue #8).
#
# The MODULE_* metadata variables below are read by lib/omes/module.sh
# after sourcing this file; ShellCheck cannot see that cross-file usage
# when analyzing this file on its own.
#
# Scope: MODULE_SCOPE=user. This module NEVER mutates the host - it only
# inspects (GPU/driver, package availability, RAM/disk, display manager,
# portal, and the Cinnamon fallback session). module_apply is a no-op
# (records that a check ran); the actual package installation and session
# registration happen in the separate root-scope `hyprland-session` module.

# shellcheck disable=SC2034
MODULE_NAME="desktop-preflight"
# shellcheck disable=SC2034
MODULE_DESCRIPTION="Read-only desktop readiness checks (GPU/driver, packages, RAM/disk, Cinnamon fallback) for the opt-in Hyprland session"
# shellcheck disable=SC2034
MODULE_SCOPE="user"
# shellcheck disable=SC2034
MODULE_REQUIRES=()
# shellcheck disable=SC2034
MODULE_PROFILES=(desktop)

# shellcheck source=modules/desktop-preflight/checks.sh
source "${OMES_ROOT}/modules/desktop-preflight/checks.sh"

module_check() {
  dp_run_all_checks
}

module_apply() {
  log_info "desktop-preflight: read-only checks only; nothing is installed or written by this module"
  if ! omes_dry_run; then
    state_set "module.desktop-preflight.last_run" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  else
    log_info "[dry-run] would record module.desktop-preflight.last_run"
  fi
  return 0
}

module_verify() {
  if omes_dry_run; then
    log_info "[dry-run] would re-run desktop-preflight checks"
    return 0
  fi
  dp_run_all_checks
}

module_rollback() {
  log_info "desktop-preflight: nothing was ever changed on the host by this module; rollback is a no-op"
  state_unset "module.desktop-preflight.last_run" 2>/dev/null || true
  return 0
}
