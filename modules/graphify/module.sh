#!/usr/bin/env bash
# shellcheck shell=bash
# modules/graphify/module.sh - Graphify CLI, installed per-user in an
# isolated Python tool environment (uv tool preferred, pipx fallback).
#
# The MODULE_* metadata variables below are read by lib/omes/module.sh
# after sourcing this file; ShellCheck cannot see that cross-file usage
# when analyzing this file on its own.
#
# Scope: MODULE_SCOPE=user. Graphify (upstream: Graphify-Labs/graphify,
# PyPI package `graphifyy`, CLI executable `graphify`) is installed
# entirely within the invoking user's isolated tool environment - never
# into system Python (PEP 668) and never as root. See
# docs/graphify.md §2 and docs/adr/0014-graphify-integration-boundary.md.
#
# This module owns install/check/update/uninstall lifecycle of the
# `graphify` CLI ONLY. It never touches graphify-out/ directories, vault
# content, or provider/API-key configuration - those remain entirely the
# operator's and, in a later run, graphify's own responsibility.

# shellcheck disable=SC2034
MODULE_NAME="graphify"
# shellcheck disable=SC2034
MODULE_DESCRIPTION="Graphify CLI (graphifyy, per-user tool-env install)"
# shellcheck disable=SC2034
MODULE_SCOPE="user"
# shellcheck disable=SC2034
MODULE_REQUIRES=()
# Deliberately not wired into any profile's default module list -
# graphify is optional (docs/graphify.md §1.4). Reachable via
# `omes install --module graphify`.
# shellcheck disable=SC2034
MODULE_PROFILES=()

GRAPHIFY_PACKAGE="graphifyy"
GRAPHIFY_UV_INSTALLER_URL="https://astral.sh/uv/install.sh"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# _graphify_ensure_runtime_path
# Makes ~/.local/bin visible on PATH for the REST OF THIS PROCESS (uv tool
# and pipx both install shims there by default), so a tool installed
# earlier in this same module_apply/module_verify call is immediately
# runnable without requiring a new shell. Idempotent. Mirrors
# modules/hermes/module.sh's _hermes_ensure_runtime_path.
_graphify_ensure_runtime_path() {
  case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) ;;
    *)
      PATH="${HOME}/.local/bin:${PATH}"
      export PATH
      ;;
  esac
}

# _graphify_installer_available
# Prints "uv" or "pipx" (uv preferred) when found on PATH; prints nothing
# and returns 1 when neither is found. Does not install anything.
_graphify_installer_available() {
  _graphify_ensure_runtime_path
  if command -v uv >/dev/null 2>&1; then
    printf 'uv\n'
    return 0
  fi
  if command -v pipx >/dev/null 2>&1; then
    printf 'pipx\n'
    return 0
  fi
  return 1
}

# _graphify_python_version_ok
# True (0) when `python3` is present and reports >= 3.10. Parses only the
# major.minor from `python3 --version` output (e.g. "Python 3.11.4").
_graphify_python_version_ok() {
  command -v python3 >/dev/null 2>&1 || return 1

  local out major minor
  out="$(python3 --version 2>&1)" || return 1
  # Expected shape: "Python 3.11.4"
  out="${out#Python }"
  major="${out%%.*}"
  minor="${out#*.}"
  minor="${minor%%.*}"

  [[ "$major" =~ ^[0-9]+$ ]] || return 1
  [[ "$minor" =~ ^[0-9]+$ ]] || return 1

  if [[ "$major" -gt 3 ]]; then
    return 0
  fi
  if [[ "$major" -eq 3 ]] && [[ "$minor" -ge 10 ]]; then
    return 0
  fi
  return 1
}

# _graphify_installed_version
# Prints `graphify --version` output when the graphify CLI is present and
# runnable; prints nothing (and returns non-zero) otherwise. Never treats
# a present-but-broken binary as "installed".
_graphify_installed_version() {
  _graphify_ensure_runtime_path
  command -v graphify >/dev/null 2>&1 || return 1
  graphify --version 2>/dev/null
}

# _graphify_bootstrap_uv
# Downloads the official uv installer to a temp file (never `curl | bash`),
# optionally verifies OMES_UV_INSTALLER_SHA256, then runs it. Mirrors
# modules/hermes/module.sh's _hermes_download_and_install. Only ever
# called when the operator has explicitly opted in via
# OMES_GRAPHIFY_INSTALLER=uv-bootstrap (docs/graphify.md §2).
_graphify_bootstrap_uv() {
  if omes_dry_run; then
    log_info "[dry-run] would download the uv installer from ${GRAPHIFY_UV_INSTALLER_URL} and run it"
    return 0
  fi

  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/omes-uv-install.XXXXXX")"

  log_info "graphify: downloading uv installer from ${GRAPHIFY_UV_INSTALLER_URL} (OMES_GRAPHIFY_INSTALLER=uv-bootstrap)"
  if ! curl -fsSL "$GRAPHIFY_UV_INSTALLER_URL" -o "$tmp"; then
    log_error "graphify: failed to download the uv installer from ${GRAPHIFY_UV_INSTALLER_URL}"
    rm -f "$tmp"
    return 1
  fi

  if [[ -n "${OMES_UV_INSTALLER_SHA256:-}" ]]; then
    local actual
    actual="$(sha256sum "$tmp" | awk '{print $1}')"
    if [[ "$actual" != "${OMES_UV_INSTALLER_SHA256}" ]]; then
      log_error "graphify: uv installer sha256 mismatch (expected ${OMES_UV_INSTALLER_SHA256}, got ${actual}); aborting, nothing executed"
      rm -f "$tmp"
      return 1
    fi
    log_info "graphify: uv installer sha256 verified against OMES_UV_INSTALLER_SHA256"
  else
    log_warn "graphify: OMES_UV_INSTALLER_SHA256 is not set; running the uv installer without verifying its integrity"
  fi

  log_info "graphify: running uv installer"
  local rc=0
  omes_run bash "$tmp" || rc=$?
  rm -f "$tmp"

  if [[ "$rc" -ne 0 ]]; then
    log_error "graphify: uv installer exited with status ${rc}"
    return 1
  fi

  _graphify_ensure_runtime_path
  return 0
}

# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------

module_check() {
  if omes_is_root; then
    log_error "graphify: must not run as root - graphify installs per-user, into an isolated tool environment; re-run as the target user without sudo"
    return 1
  fi

  if ! _graphify_python_version_ok; then
    log_error "graphify: python3 >= 3.10 is required and was not found (or is older than 3.10)"
    return 1
  fi

  local installer
  if installer="$(_graphify_installer_available)"; then
    log_info "graphify: using ${installer} for install/update/uninstall"
  else
    if [[ "${OMES_GRAPHIFY_INSTALLER:-}" == "uv-bootstrap" ]]; then
      log_info "graphify: neither uv nor pipx found; OMES_GRAPHIFY_INSTALLER=uv-bootstrap is set, will bootstrap uv"
      if ! detect_network; then
        log_error "graphify: network required to bootstrap uv (OMES_GRAPHIFY_INSTALLER=uv-bootstrap)"
        return 1
      fi
    else
      log_error "graphify: neither uv nor pipx found on PATH - install pipx via 'sudo apt install pipx' (available in Ubuntu noble), or install uv by setting OMES_GRAPHIFY_INSTALLER=uv-bootstrap"
      return 1
    fi
  fi

  local installed_version
  if installed_version="$(_graphify_installed_version)"; then
    log_info "graphify: already installed (${installed_version})"
    return 0
  fi

  log_info "graphify: not yet installed"
  if ! detect_network; then
    log_error "graphify: network required to install graphifyy"
    return 1
  fi
  return 0
}

module_apply() {
  local installer
  if ! installer="$(_graphify_installer_available)"; then
    if [[ "${OMES_GRAPHIFY_INSTALLER:-}" == "uv-bootstrap" ]]; then
      if ! _graphify_bootstrap_uv; then
        return 1
      fi
      if omes_dry_run; then
        installer="uv"
      elif ! installer="$(_graphify_installer_available)"; then
        log_error "graphify: uv bootstrap reported success but uv is still not on PATH"
        return 1
      fi
    else
      log_error "graphify: neither uv nor pipx found on PATH and OMES_GRAPHIFY_INSTALLER=uv-bootstrap is not set"
      return 1
    fi
  fi

  local current_version=""
  current_version="$(_graphify_installed_version || true)"

  local need_install=1
  if [[ -n "$current_version" ]]; then
    if [[ -z "${OMES_GRAPHIFY_VERSION:-}" ]] || [[ "$current_version" == *"${OMES_GRAPHIFY_VERSION}"* ]]; then
      need_install=0
    fi
  fi

  if [[ "$need_install" -eq 1 ]]; then
    local pkgspec="$GRAPHIFY_PACKAGE"
    if [[ -n "${OMES_GRAPHIFY_VERSION:-}" ]]; then
      pkgspec="${GRAPHIFY_PACKAGE}==${OMES_GRAPHIFY_VERSION}"
    fi

    local -a cmd=()
    if [[ "$installer" == "uv" ]]; then
      cmd=(uv tool install "$pkgspec")
    else
      cmd=(pipx install "$pkgspec")
    fi

    if ! omes_run "${cmd[@]}"; then
      log_error "graphify: install failed via ${installer} (${pkgspec})"
      return 1
    fi
  else
    log_info "graphify: already installed (${current_version}); skipping install"
  fi

  if ! omes_dry_run; then
    local resolved
    resolved="$(_graphify_installed_version || true)"
    if [[ -n "$resolved" ]]; then
      state_set "module.graphify.version_installed" "$resolved"
    fi
  fi

  return 0
}

module_verify() {
  if omes_dry_run; then
    log_info "[dry-run] would verify: graphify --help exits 0"
    return 0
  fi

  _graphify_ensure_runtime_path

  if ! command -v graphify >/dev/null 2>&1; then
    log_error "graphify: 'graphify' is not on PATH after apply"
    return 1
  fi

  if ! graphify --help >/dev/null 2>&1; then
    log_error "graphify: 'graphify --help' failed"
    return 1
  fi

  log_info "graphify: 'graphify --help' succeeded"
  return 0
}

module_rollback() {
  local installer
  if ! installer="$(_graphify_installer_available)"; then
    log_warn "graphify: rollback found neither uv nor pipx on PATH; nothing to uninstall"
    state_unset "module.graphify.version_installed"
    return 0
  fi

  log_warn "graphify: rollback removes only the ${GRAPHIFY_PACKAGE} tool-env install; it never touches graphify-out/ directories or any other graphify-produced data"

  if omes_dry_run; then
    log_info "[dry-run] would run: ${installer} uninstall ${GRAPHIFY_PACKAGE}"
    return 0
  fi

  local -a cmd=()
  if [[ "$installer" == "uv" ]]; then
    cmd=(uv tool uninstall "$GRAPHIFY_PACKAGE")
  else
    cmd=(pipx uninstall "$GRAPHIFY_PACKAGE")
  fi

  if ! omes_run "${cmd[@]}"; then
    log_error "graphify: uninstall via ${installer} failed"
    return 1
  fi

  log_warn "graphify: removed ${GRAPHIFY_PACKAGE} via ${installer} tool uninstall"
  state_unset "module.graphify.version_installed"
  return 0
}
