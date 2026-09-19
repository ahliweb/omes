#!/usr/bin/env bash
# shellcheck shell=bash
# lib/omes/pkg.sh - apt package helpers, package-name mapping, repo validation.
#
# Meant to be sourced after lib/omes/core.sh, lib/omes/log.sh,
# lib/omes/json.sh, lib/omes/detect.sh, lib/omes/state.sh and
# lib/omes/module.sh (uses state_get/state_set, detect_network, log_*,
# omes_run, omes_dry_run, omes_manage_path, json_obj/json_kv, and the
# OMES_EX_* exit-code constants).
#
# ---------------------------------------------------------------------------
# Return-code convention
# ---------------------------------------------------------------------------
# Functions in this file that can fail for more than one distinct reason
# return one of the shared OMES_EX_* exit-code constants (never a bare 1)
# so a caller (module_check / module_apply) can map the failure to the
# correct process exit code without re-deriving "why" itself:
#
#   0                       success
#   $OMES_EX_NETWORK    (8) network required but unavailable
#   $OMES_EX_PREFLIGHT  (4) package/repo validated but not available
#   $OMES_EX_MODULE_APPLY (6) the underlying apt-get command itself failed
#
# `run_apply` (lib/omes/module.sh) recognizes $OMES_EX_NETWORK returned from
# module_apply and maps the process exit code to 8 instead of the default 6;
# `run_checks` maps any module_check failure to exit 4 regardless of reason
# (matching docs/architecture.md Section 11: a network-required-but-absent
# module_check failure surfaces as a preflight failure, exit 4).

if [[ -n "${OMES_PKG_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317  # unreachable only when sourced; the `exit`
  # branch is the (correct) fallback for accidental direct execution.
  return 0 2>/dev/null || exit 0
fi
OMES_PKG_SH_LOADED=1

# pkg_provenance_payload/pkg_record_provenance (below) need json_obj/
# json_kv (lib/omes/json.sh) and provenance_record_component
# (lib/omes/cmd/audit-provenance.sh, itself only needing omes_dry_run/
# log_*/omes_state_dir, already sourced by every caller of this file per
# its own header above). Both are idempotent to source, so they are
# pulled in here defensively - the same pattern lib/omes/versions.sh uses
# for its own dependencies - rather than requiring every apt-installing
# module to remember two extra `source` lines in the right order.
# shellcheck source=./json.sh
source "${OMES_ROOT}/lib/omes/json.sh"
# shellcheck source=./cmd/audit-provenance.sh
source "${OMES_ROOT}/lib/omes/cmd/audit-provenance.sh"

# ---------------------------------------------------------------------------
# Install-state queries (read-only, safe from module_check)
# ---------------------------------------------------------------------------

# pkg_is_installed <pkg>
# True when dpkg reports the real (already-mapped) package name as fully
# installed.
pkg_is_installed() {
  local pkg="$1"
  local status
  status="$(dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null || true)"
  [[ "$status" == "install ok installed" ]]
}

# pkg_missing <pkg...>
# Prints the subset of the given (real) package names that are not yet
# installed, one per line, preserving input order.
pkg_missing() {
  local pkg
  for pkg in "$@"; do
    pkg_is_installed "$pkg" || printf '%s\n' "$pkg"
  done
}

# Network checks below call detect_network() (lib/omes/detect.sh) directly,
# rather than re-deriving their own online/offline decision, so there is a
# single source of truth for "is the network usable" across the codebase.
# detect_network already honors OMES_ASSUME_ONLINE=1 / OMES_ASSUME_OFFLINE=1
# (checked in that order) so callers/tests wanting a deterministic offline
# result must also clear OMES_ASSUME_ONLINE (e.g.
# `OMES_ASSUME_ONLINE=0 OMES_ASSUME_OFFLINE=1`), matching detect_network's
# own contract and tests.

# ---------------------------------------------------------------------------
# apt-get update (rate-limited)
# ---------------------------------------------------------------------------

# pkg_apt_update
# Runs `apt-get update` at most once per OMES_PKG_APT_UPDATE_MAX_AGE seconds
# (default 3600 = 1h), tracked via the pkg.apt_update.last_run state key
# (shared across modules: it is one host-wide apt cache, not a per-module
# resource). Honors dry-run (prints the planned command, mutates nothing,
# including the state key). Returns $OMES_EX_NETWORK when network is
# required and unavailable, $OMES_EX_MODULE_APPLY when apt-get itself
# fails, 0 on success or on a fresh-enough skip.
pkg_apt_update() {
  local max_age="${OMES_PKG_APT_UPDATE_MAX_AGE:-3600}"
  local key="pkg.apt_update.last_run"
  local last now age

  last="$(state_get "$key" 2>/dev/null || true)"
  now="$(date -u +%s)"

  if [[ -n "$last" ]]; then
    age=$((now - last))
    if [[ "$age" -lt "$max_age" ]]; then
      log_info "pkg: apt-get update skipped (last run ${age}s ago, max-age ${max_age}s)"
      return 0
    fi
  fi

  if omes_dry_run; then
    omes_run apt-get update
    return 0
  fi

  if ! detect_network; then
    log_error "pkg: apt-get update requires network access, but network is unavailable"
    return "$OMES_EX_NETWORK"
  fi

  if ! omes_run apt-get update; then
    log_error "pkg: apt-get update failed"
    return "$OMES_EX_MODULE_APPLY"
  fi

  state_set "$key" "$now"
  return 0
}

# ---------------------------------------------------------------------------
# Repository/candidate introspection (read-only, safe from module_check)
# ---------------------------------------------------------------------------

# pkg_candidate_version <pkg>
# Prints the apt-cache "Candidate" version for <pkg> (empty or "(none)" when
# apt has no candidate for it, e.g. unknown package or empty cache).
pkg_candidate_version() {
  local pkg="$1"
  local out candidate
  out="$(apt-cache policy "$pkg" 2>/dev/null || true)"
  candidate="$(printf '%s\n' "$out" | awk -F': ' '/^[[:space:]]*Candidate:/ {print $2; exit}')"
  printf '%s\n' "${candidate:-}"
}

# ---------------------------------------------------------------------------
# Package-manager provenance (issue #84)
# ---------------------------------------------------------------------------

# pkg_installed_version <pkg>
# Prints the installed version of <pkg> per dpkg (`dpkg-query -W -f
# '${Version}'`), or empty when not installed/unknown. Read-only.
pkg_installed_version() {
  local pkg="$1"
  dpkg-query -W -f='${Version}' "$pkg" 2>/dev/null || true
}

# pkg_apt_origin <pkg>
# Prints the apt repository origin URL for <pkg>'s currently configured
# candidate (the first "<priority> <url> ..." line under `apt-cache
# policy`'s "Version table:"), or empty when it cannot be determined.
# Read-only - used to populate a package-manager provenance record's
# installer_source_url/package_manager.origin (issue #84); this is NOT a
# guarantee the candidate shown is what is actually installed.
pkg_apt_origin() {
  local pkg="$1"
  local out
  out="$(apt-cache policy "$pkg" 2>/dev/null || true)"
  printf '%s\n' "$out" | awk '/^[[:space:]]+[0-9]+[[:space:]]+https?:\/\// {print $2; exit}'
}

# pkg_provenance_payload <pkg>
# Builds the JSON payload provenance_record_component (see
# lib/omes/cmd/audit-provenance.sh) expects for an apt-managed package:
# resolved_version (dpkg), installer_source_url/package_manager.origin
# (apt-cache policy), and checksum.status "package_manager_verified"
# (apt/dpkg verifies package signatures itself; OMES did not
# independently pin/re-verify a checksum here - see docs/provenance.md).
# Requires lib/omes/json.sh to already be sourced (json_obj/json_kv).
# Prints nothing (and returns 1) when <pkg> is not currently installed,
# so a caller never records a provenance entry for a package that was
# not actually installed.
pkg_provenance_payload() {
  local pkg="$1"
  local version origin
  version="$(pkg_installed_version "$pkg")"
  if [[ -z "$version" ]]; then
    return 1
  fi
  origin="$(pkg_apt_origin "$pkg")"

  local pkgmgr_obj checksum_obj
  pkgmgr_obj="$(json_obj \
    "$(json_kv name apt)" \
    "$(json_kv package "$pkg")" \
    "$(json_kv version "$version")" \
    "$(json_kv origin "$origin")")"
  checksum_obj="$(json_obj \
    "$(json_kv algorithm "")" \
    "$(json_kv expected "")" \
    "$(json_kv actual "")" \
    "$(json_kv status package_manager_verified)")"

  json_obj \
    "$(json_kv installer_source_url "$origin")" \
    "$(json_kv resolved_version "$version")" \
    "$(json_kv install_time "$(date -u +%Y-%m-%dT%H:%M:%SZ)")" \
    "$(json_kv checksum "$checksum_obj" --raw)" \
    "$(json_kv package_manager "$pkgmgr_obj" --raw)"
}

# pkg_record_provenance <pkg...>
# Calls provenance_record_component (lib/omes/cmd/audit-provenance.sh -
# the caller must have sourced that file) for each installed <pkg>,
# using pkg_provenance_payload. Best-effort and silent about packages
# that turn out not to be installed (pkg_provenance_payload's own
# contract); never fails the caller's module_apply, matching
# provenance_record_component's own never-fail contract. Honors
# OMES_DRY_RUN via provenance_record_component itself.
pkg_record_provenance() {
  local pkg payload
  for pkg in "$@"; do
    payload="$(pkg_provenance_payload "$pkg")" || continue
    provenance_record_component "$pkg" "${OMES_PROFILE:-}" "$payload"
  done
}

# pkg_exists_in_repos <pkg>
# Validates that <pkg> (a real, already-mapped package name) is available
# in the currently configured repositories, BEFORE any mutation - this is
# what module_check calls for every package it plans to install, so a
# missing/renamed package is reported as a preflight failure (exit 4)
# instead of failing mid-`module_apply` (exit 6). Returns $OMES_EX_NETWORK
# when the availability cannot be validated because network is unavailable
# (rather than misreporting a possibly-stale/empty cache as "package
# unknown"), $OMES_EX_PREFLIGHT when apt-cache confirms no candidate, 0 when
# a candidate exists.
pkg_exists_in_repos() {
  local pkg="$1"

  if ! detect_network; then
    log_error "pkg: cannot validate '${pkg}' availability: network is unavailable"
    return "$OMES_EX_NETWORK"
  fi

  if ! command -v apt-cache >/dev/null 2>&1; then
    log_error "pkg: apt-cache not found (requires a Debian/Ubuntu-based system)"
    return "$OMES_EX_MODULE_APPLY"
  fi

  local candidate
  candidate="$(pkg_candidate_version "$pkg")"
  if [[ -z "$candidate" ]] || [[ "$candidate" == "(none)" ]]; then
    log_error "pkg: '${pkg}' is not available in the configured repositories"
    return "$OMES_EX_PREFLIGHT"
  fi

  return 0
}

# ---------------------------------------------------------------------------
# Install (mutating; module_apply only)
# ---------------------------------------------------------------------------

# _pkg_record_installed <module-name> <pkg...>
# Merges <pkg...> into module.<module-name>.installed_packages (colon-
# separated per docs/architecture.md Section 6.3), de-duplicated, preserving
# first-seen order. Never removes a previously recorded package.
_pkg_record_installed() {
  local mod="$1"
  shift
  local -a newly=("$@")
  local key="module.${mod}.installed_packages"
  local existing
  existing="$(state_get "$key" 2>/dev/null || true)"

  local -A seen=()
  local -a merged=()
  local p

  if [[ -n "$existing" ]]; then
    local -a existing_arr=()
    local old_ifs="$IFS"
    IFS=':'
    read -r -a existing_arr <<< "$existing"
    IFS="$old_ifs"
    for p in "${existing_arr[@]}"; do
      [[ -z "$p" ]] && continue
      if [[ -z "${seen[$p]:-}" ]]; then
        seen[$p]=1
        merged+=("$p")
      fi
    done
  fi

  for p in "${newly[@]}"; do
    if [[ -z "${seen[$p]:-}" ]]; then
      seen[$p]=1
      merged+=("$p")
    fi
  done

  local joined=""
  if [[ "${#merged[@]}" -gt 0 ]]; then
    local IFS=':'
    joined="${merged[*]}"
  fi
  state_set "$key" "$joined"
}

# pkg_install <pkg...>
# Idempotent: recomputes the actually-missing subset of <pkg...> itself, so
# it is always safe to pass a module's whole package list. Installs via
# `DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends`.
# Honors dry-run (prints the planned command via omes_run, mutates nothing).
# Records only the packages newly installed BY THIS CALL into
# module.<MODULE_NAME>.installed_packages (merged with any prior record for
# that module) - MODULE_NAME must be set by the caller (module_load already
# does this for every module_apply invocation). Returns $OMES_EX_NETWORK
# when network is required and unavailable, $OMES_EX_MODULE_APPLY when
# apt-get itself fails, 0 on success (including a no-op when nothing is
# missing).
pkg_install() {
  local -a requested=("$@")
  local -a missing=()
  local pkg

  while IFS= read -r pkg; do
    [[ -n "$pkg" ]] && missing+=("$pkg")
  done < <(pkg_missing "${requested[@]}")

  if [[ "${#missing[@]}" -eq 0 ]]; then
    log_info "pkg: nothing to install, already present: ${requested[*]}"
    return 0
  fi

  if omes_dry_run; then
    DEBIAN_FRONTEND=noninteractive omes_run apt-get install -y --no-install-recommends "${missing[@]}"
    return 0
  fi

  if ! detect_network; then
    log_error "pkg: install requires network access for: ${missing[*]}"
    return "$OMES_EX_NETWORK"
  fi

  if ! DEBIAN_FRONTEND=noninteractive omes_run apt-get install -y --no-install-recommends "${missing[@]}"; then
    log_error "pkg: apt-get install failed for: ${missing[*]}"
    return "$OMES_EX_MODULE_APPLY"
  fi

  if [[ -n "${MODULE_NAME:-}" ]]; then
    _pkg_record_installed "$MODULE_NAME" "${missing[@]}"
  else
    log_warn "pkg: MODULE_NAME is unset; skipping installed_packages bookkeeping for: ${missing[*]}"
  fi

  return 0
}

# ---------------------------------------------------------------------------
# Package-name mapping (logical name -> real package name per OS/release)
# ---------------------------------------------------------------------------

# Default logical -> real package name table, valid across every supported
# OS unless overridden below. Documented divergences:
#   bat  -> bat      (binary installed as `batcat`, see pkg_map_binary)
#   fd   -> fd-find   (binary installed as `fdfind`, see pkg_map_binary)
#   eza  -> eza       (package name matches upstream, but is NOT guaranteed
#                       to exist in every supported release's repos; callers
#                       MUST verify with pkg_exists_in_repos and report the
#                       package as unavailable rather than add a PPA - see
#                       docs/packages.md "Repository policy")
declare -gA PKG_MAP_DEFAULT=(
  [bat]=bat
  [fd]=fd-find
  [eza]=eza
)

# Per-release overrides, keyed "${OMES_OS_ID}:${OMES_OS_VERSION_ID}:<logical>".
# This is the documented mechanism for a package-name divergence tied to a
# *specific* OS+version rather than the whole OS family (e.g. a package
# renamed/split starting in one release). Empty by default; extend it here
# (and document the addition in docs/packages.md) when a real divergence is
# found - do not special-case it inline in a module.
declare -gA PKG_MAP_RELEASE_OVERRIDES=()

# pkg_map <logical-name>
# Prints the real package name for <logical-name> on the current OS
# (checks PKG_MAP_RELEASE_OVERRIDES first, then PKG_MAP_DEFAULT, then falls
# back to the logical name unchanged when no mapping is needed).
pkg_map() {
  local logical="$1"
  local override_key="${OMES_OS_ID:-}:${OMES_OS_VERSION_ID:-}:${logical}"

  if [[ -n "${PKG_MAP_RELEASE_OVERRIDES[$override_key]:-}" ]]; then
    printf '%s\n' "${PKG_MAP_RELEASE_OVERRIDES[$override_key]}"
    return 0
  fi

  if [[ -n "${PKG_MAP_DEFAULT[$logical]:-}" ]]; then
    printf '%s\n' "${PKG_MAP_DEFAULT[$logical]}"
    return 0
  fi

  printf '%s\n' "$logical"
}

# pkg_map_binary <logical-name>
# Prints the executable name a module should look for after installing
# pkg_map's result (differs from the package name for bat/fd).
pkg_map_binary() {
  local logical="$1"
  case "$logical" in
    bat) printf 'batcat\n' ;;
    fd) printf 'fdfind\n' ;;
    *) pkg_map "$logical" ;;
  esac
}

# ---------------------------------------------------------------------------
# Repository validation and deb822 .sources management
# ---------------------------------------------------------------------------

# repo_validate <name> <uri> <suite> <signed-by-path>
# Verifies (read-only): all four arguments are non-empty, <uri> uses
# https://, and <signed-by-path> exists with mode 0644. Logs the specific
# reason(s) it fails via log_error. Returns 0 only when every check passes.
repo_validate() {
  local name="${1:-}" uri="${2:-}" suite="${3:-}" signed_by="${4:-}"
  local ok=1

  if [[ -z "$name" ]] || [[ -z "$uri" ]] || [[ -z "$suite" ]] || [[ -z "$signed_by" ]]; then
    log_error "repo_validate: name, uri, suite and signed-by path are all required"
    return 1
  fi

  case "$uri" in
    https://*) ;;
    *)
      log_error "repo_validate: '${name}' repository URI must use https:// (got: ${uri})"
      ok=0
      ;;
  esac

  if [[ ! -e "$signed_by" ]]; then
    log_error "repo_validate: '${name}' signed-by keyring file does not exist: ${signed_by}"
    ok=0
  else
    local mode
    mode="$(stat -c '%a' "$signed_by" 2>/dev/null || true)"
    if [[ "$mode" != "644" ]]; then
      log_error "repo_validate: '${name}' signed-by keyring file '${signed_by}' has mode ${mode:-unknown} (expected 0644)"
      ok=0
    fi
  fi

  [[ "$ok" -eq 1 ]]
}

# repo_sources_dir
# Prints the directory .sources files are written to. Overridable via
# OMES_APT_SOURCES_DIR (tests only; production always uses the real apt
# sources.list.d). /etc/apt/sources.list itself is never written by OMES.
repo_sources_dir() {
  printf '%s\n' "${OMES_APT_SOURCES_DIR:-/etc/apt/sources.list.d}"
}

# repo_add <name> <uri> <suite> <signed-by-path> [components] [--ubuntu-only]
# Validates via repo_validate (refuses on failure, no mutation), then writes
# a deb822-format <name>.sources file under repo_sources_dir(), backed up
# and registered via omes_manage_path (never modifies
# /etc/apt/sources.list). [components] defaults to "main". Pass
# --ubuntu-only when the repository is Ubuntu-only upstream (e.g. Docker):
# on Linux Mint this emits the documented non-parity WARN (docs/scope.md
# Section 4.6) - the caller is still responsible for passing
# $OMES_OS_CODENAME (which already resolves to $UBUNTU_CODENAME on Mint,
# see lib/omes/detect.sh detect_os) as <suite>, never the Mint codename.
# Honors dry-run.
repo_add() {
  local name="$1" uri="$2" suite="$3" signed_by="$4"
  shift 4
  local components="main"
  local ubuntu_only=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ubuntu-only)
        ubuntu_only=1
        shift
        ;;
      *)
        components="$1"
        shift
        ;;
    esac
  done

  if ! repo_validate "$name" "$uri" "$suite" "$signed_by"; then
    log_error "repo_add: refusing to add repository '${name}': failed validation"
    return 1
  fi

  if [[ "$ubuntu_only" -eq 1 ]] && [[ "${OMES_OS_ID:-}" == "linuxmint" ]]; then
    log_warn "repo_add: '${name}' is an Ubuntu-only repository; Docker/vendors of this kind do not support Linux Mint directly, no support parity is claimed. Using \$UBUNTU_CODENAME (${OMES_OS_CODENAME:-unknown}) for suite '${suite}' (see docs/scope.md Section 4.6)."
  fi

  local dir file
  dir="$(repo_sources_dir)"
  file="${dir}/${name}.sources"

  if omes_dry_run; then
    log_info "[dry-run] would write apt source: ${file} (Types: deb, URIs: ${uri}, Suites: ${suite}, Components: ${components}, Signed-By: ${signed_by})"
    return 0
  fi

  mkdir -p "$dir"
  omes_manage_path "$file"

  local tmp
  tmp="$(mktemp "${dir}/.${name}.sources.XXXXXX")"
  {
    printf 'Types: deb\n'
    printf 'URIs: %s\n' "$uri"
    printf 'Suites: %s\n' "$suite"
    printf 'Components: %s\n' "$components"
    printf 'Signed-By: %s\n' "$signed_by"
  } > "$tmp"
  chmod 644 "$tmp"
  mv -f "$tmp" "$file"
  log_info "repo_add: wrote ${file}"
}

# repo_remove <name>
# Removes <name>.sources from repo_sources_dir() if present (backed up and
# registered via omes_manage_path first); a no-op, not an error, when the
# file does not exist. Honors dry-run.
repo_remove() {
  local name="$1"
  local dir file
  dir="$(repo_sources_dir)"
  file="${dir}/${name}.sources"

  if [[ ! -e "$file" ]]; then
    log_info "repo_remove: no such repository file, nothing to do: ${file}"
    return 0
  fi

  if omes_dry_run; then
    log_info "[dry-run] would remove apt source: ${file}"
    return 0
  fi

  omes_manage_path "$file"
  rm -f "$file"
  log_info "repo_remove: removed ${file}"
}
