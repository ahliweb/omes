#!/usr/bin/env bash
# shellcheck shell=bash
# modules/apt-base/module.sh - baseline CLI tooling via apt.
#
# The MODULE_* metadata variables below are read by lib/omes/module.sh
# after sourcing this file; ShellCheck cannot see that cross-file usage
# when analyzing this file on its own.

# shellcheck disable=SC2034
MODULE_NAME="apt-base"
# shellcheck disable=SC2034
MODULE_DESCRIPTION="Base CLI tooling (curl, git, python3, jq, ufw, ...)"
# shellcheck disable=SC2034
MODULE_SCOPE="root"
# shellcheck disable=SC2034
MODULE_REQUIRES=()
# shellcheck disable=SC2034
MODULE_PROFILES=(server desktop)

APT_BASE_PACKAGES=(curl ca-certificates git python3 jq unzip tar gnupg lsb-release ufw)

# _apt_base_is_installed <package>
# True when dpkg reports the package as fully installed.
_apt_base_is_installed() {
  local pkg="$1"
  local status
  status="$(dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null || true)"
  [[ "$status" == "install ok installed" ]]
}

# _apt_base_missing_packages
# Prints the subset of APT_BASE_PACKAGES that are not yet installed.
_apt_base_missing_packages() {
  local pkg
  for pkg in "${APT_BASE_PACKAGES[@]}"; do
    if ! _apt_base_is_installed "$pkg"; then
      printf '%s\n' "$pkg"
    fi
  done
}

# _apt_base_update_if_stale
# Runs `apt-get update` at most once per hour, tracked via the
# module.apt-base.last_update state key.
_apt_base_update_if_stale() {
  local last now age
  last="$(state_get "module.apt-base.last_update" 2>/dev/null || true)"
  now="$(date -u +%s)"

  if [[ -n "$last" ]]; then
    age=$((now - last))
    if [[ "$age" -lt 3600 ]]; then
      log_info "apt-base: apt-get update skipped (last run ${age}s ago)"
      return 0
    fi
  fi

  omes_run apt-get update

  if ! omes_dry_run; then
    state_set "module.apt-base.last_update" "$now"
  fi
}

module_check() {
  if ! command -v apt-get >/dev/null 2>&1; then
    log_error "apt-base: apt-get not found (requires a Debian/Ubuntu-based system)"
    return 1
  fi
  if ! command -v dpkg-query >/dev/null 2>&1; then
    log_error "apt-base: dpkg-query not found"
    return 1
  fi

  local -a missing=()
  local pkg
  while IFS= read -r pkg; do
    [[ -n "$pkg" ]] && missing+=("$pkg")
  done < <(_apt_base_missing_packages)

  if [[ "${#missing[@]}" -eq 0 ]]; then
    log_info "apt-base: all packages already installed (offline ok)"
    return 0
  fi

  if ! detect_network; then
    log_error "apt-base: network required to install missing packages: ${missing[*]}"
    return 1
  fi

  log_info "apt-base: missing packages: ${missing[*]}"
  return 0
}

module_apply() {
  local -a missing=()
  local pkg
  while IFS= read -r pkg; do
    [[ -n "$pkg" ]] && missing+=("$pkg")
  done < <(_apt_base_missing_packages)

  if [[ "${#missing[@]}" -eq 0 ]]; then
    log_info "apt-base: nothing to install, all packages already present"
    if ! omes_dry_run; then
      state_set "module.apt-base.installed_packages" ""
    fi
    return 0
  fi

  _apt_base_update_if_stale

  DEBIAN_FRONTEND=noninteractive omes_run apt-get install -y --no-install-recommends "${missing[@]}"

  if ! omes_dry_run; then
    local IFS=' '
    state_set "module.apt-base.installed_packages" "${missing[*]}"
  fi

  return 0
}

module_verify() {
  local -a bad=()
  local pkg
  while IFS= read -r pkg; do
    [[ -n "$pkg" ]] && bad+=("$pkg")
  done < <(_apt_base_missing_packages)

  if [[ "${#bad[@]}" -gt 0 ]]; then
    log_error "apt-base: packages missing after apply: ${bad[*]}"
    return 1
  fi

  log_info "apt-base: verified all packages installed"
  return 0
}

module_rollback() {
  local installed
  installed="$(state_get "module.apt-base.installed_packages" 2>/dev/null || true)"

  if [[ -z "$installed" ]]; then
    log_info "apt-base: no packages were installed by OMES; nothing to report"
    return 0
  fi

  log_warn "apt-base: OMES installed these packages and will not remove them automatically: ${installed}"
  log_warn "apt-base: remove manually with apt-get, or use 'omes uninstall --purge-packages' once available (tracked in #10)"
  return 0
}
