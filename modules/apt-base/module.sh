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
  done < <(pkg_missing "${APT_BASE_PACKAGES[@]}")

  if [[ "${#missing[@]}" -eq 0 ]]; then
    log_info "apt-base: all packages already installed (offline ok)"
    return 0
  fi

  # Validate every missing package exists in the configured repositories
  # BEFORE any mutation, so an unknown/renamed package is reported here
  # (module_check -> preflight failure, exit 4) rather than failing
  # mid-module_apply (exit 6). pkg_exists_in_repos also distinguishes an
  # unavailable network (exit 8 via run_apply/run_checks mapping) from a
  # genuinely unknown package (exit 4) - see lib/omes/pkg.sh.
  local rc bad=0
  for pkg in "${missing[@]}"; do
    pkg_exists_in_repos "$pkg"
    rc=$?
    if [[ "$rc" -ne 0 ]]; then
      bad=1
    fi
  done
  if [[ "$bad" -eq 1 ]]; then
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
  done < <(pkg_missing "${APT_BASE_PACKAGES[@]}")

  if [[ "${#missing[@]}" -eq 0 ]]; then
    log_info "apt-base: nothing to install, all packages already present"
    return 0
  fi

  pkg_apt_update || return $?
  pkg_install "${missing[@]}" || return $?

  return 0
}

module_verify() {
  local -a bad=()
  local pkg
  while IFS= read -r pkg; do
    [[ -n "$pkg" ]] && bad+=("$pkg")
  done < <(pkg_missing "${APT_BASE_PACKAGES[@]}")

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

  local -a pkgs=()
  local old_ifs="$IFS"
  IFS=':'
  read -r -a pkgs <<< "$installed"
  IFS="$old_ifs"

  log_warn "apt-base: OMES installed these packages and will not remove them automatically: ${pkgs[*]}"
  log_warn "apt-base: remove manually with: apt-get remove ${pkgs[*]}"
  log_warn "apt-base: or use 'omes uninstall --purge-packages' to remove them the same way"
  return 0
}
