#!/usr/bin/env bash
# shellcheck shell=bash
# modules/hermes/module.sh - Hermes Agent CLI, installed per-user.
#
# The MODULE_* metadata variables below are read by lib/omes/module.sh
# after sourcing this file; ShellCheck cannot see that cross-file usage
# when analyzing this file on its own.
#
# Scope: MODULE_SCOPE=user. Hermes installs per-user (~/.hermes,
# ~/.local/bin/hermes) and must never be installed as root - see
# docs/hermes-integration.md and docs/security.md §1.

# shellcheck disable=SC2034
MODULE_NAME="hermes"
# shellcheck disable=SC2034
MODULE_DESCRIPTION="Hermes Agent CLI (per-user install via the upstream installer)"
# shellcheck disable=SC2034
MODULE_SCOPE="user"
# shellcheck disable=SC2034
MODULE_REQUIRES=()
# shellcheck disable=SC2034
MODULE_PROFILES=(server desktop hermes)

# Default upstream installer location (verified 2026-09-18). Overridable via
# OMES_HERMES_INSTALLER_URL purely for testability; there is no documented
# operator reason to change it.
HERMES_DEFAULT_INSTALLER_URL="https://hermes-agent.nousresearch.com/install.sh"

HERMES_PATH_MARKER_BEGIN="# BEGIN OMES hermes PATH"
HERMES_PATH_MARKER_END="# END OMES hermes PATH"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# _hermes_home
# Prints the resolved HERMES_HOME: OMES_HERMES_HOME override, else
# ~/.hermes (upstream default). Profiles that want an isolated Hermes
# instance set OMES_HERMES_HOME before running `omes install`.
_hermes_home() {
  printf '%s\n' "${OMES_HERMES_HOME:-${HOME}/.hermes}"
}

# _hermes_installer_url
# Prints the installer URL (OMES_HERMES_INSTALLER_URL override, else the
# verified upstream default).
_hermes_installer_url() {
  printf '%s\n' "${OMES_HERMES_INSTALLER_URL:-$HERMES_DEFAULT_INSTALLER_URL}"
}

# _hermes_path_snippet_file
# Prints the path to the OMES-managed PATH snippet that adds
# ~/.local/bin (where the Hermes installer places its binary) to PATH for
# future shells.
_hermes_path_snippet_file() {
  printf '%s/omes/hermes-path.sh\n' "${XDG_DATA_HOME:-${HOME}/.local/share}"
}

# _hermes_ensure_runtime_path
# Makes ~/.local/bin visible on PATH for the REST OF THIS PROCESS (not just
# future shells), so a hermes binary installed earlier in this same
# module_apply/module_verify call is immediately runnable without requiring
# a new shell. Idempotent.
_hermes_ensure_runtime_path() {
  case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) ;;
    *)
      PATH="${HOME}/.local/bin:${PATH}"
      export PATH
      ;;
  esac
}

# _hermes_installed_version
# Prints `hermes --version` output when the hermes CLI is present and
# runnable; prints nothing (and returns non-zero) otherwise. Never treats a
# present-but-broken binary as "installed".
_hermes_installed_version() {
  _hermes_ensure_runtime_path
  command -v hermes >/dev/null 2>&1 || return 1
  hermes --version 2>/dev/null
}

# _hermes_download_and_install <home>
# Downloads the Hermes installer to a temp file (never `curl | bash`),
# optionally verifies OMES_HERMES_INSTALLER_SHA256, then runs it with an
# explicit HERMES_HOME. Honors dry-run (no download, no execution).
_hermes_download_and_install() {
  local home="$1"
  local url
  url="$(_hermes_installer_url)"

  if omes_dry_run; then
    log_info "[dry-run] would download the Hermes installer from ${url} and run it with HERMES_HOME=${home}"
    return 0
  fi

  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/omes-hermes-install.XXXXXX")"

  log_info "hermes: downloading installer from ${url}"
  if ! curl -fsSL "$url" -o "$tmp"; then
    log_error "hermes: failed to download the Hermes installer from ${url}"
    rm -f "$tmp"
    return 1
  fi

  if [[ -n "${OMES_HERMES_INSTALLER_SHA256:-}" ]]; then
    local actual
    actual="$(sha256sum "$tmp" | awk '{print $1}')"
    if [[ "$actual" != "${OMES_HERMES_INSTALLER_SHA256}" ]]; then
      log_error "hermes: installer sha256 mismatch (expected ${OMES_HERMES_INSTALLER_SHA256}, got ${actual}); aborting, nothing executed"
      rm -f "$tmp"
      return 1
    fi
    log_info "hermes: installer sha256 verified against OMES_HERMES_INSTALLER_SHA256"
  else
    log_warn "hermes: OMES_HERMES_INSTALLER_SHA256 is not set; running the Hermes installer without verifying its integrity (see docs/security.md §6)"
  fi

  log_info "hermes: running installer with HERMES_HOME=${home}"
  local rc=0
  HERMES_HOME="$home" omes_run bash "$tmp" || rc=$?
  rm -f "$tmp"

  if [[ "$rc" -ne 0 ]]; then
    log_error "hermes: installer exited with status ${rc}"
    return 1
  fi

  return 0
}

# _hermes_ensure_path_snippet
# Creates/refreshes the OMES-managed PATH snippet and sources it from
# ~/.bashrc and ~/.profile if not already wired in (idempotent: a rc file
# already containing the marker block is left untouched). Both rc files are
# registered via omes_manage_path before being modified, so they are backed
# up and restorable.
_hermes_ensure_path_snippet() {
  local snippet
  snippet="$(_hermes_path_snippet_file)"

  if omes_dry_run; then
    log_info "[dry-run] would ensure PATH snippet at ${snippet} and source it from ~/.bashrc and ~/.profile"
    return 0
  fi

  omes_manage_path "$snippet"
  mkdir -p "$(dirname "$snippet")"
  cat > "$snippet" <<'SNIPPET'
# Managed by OMES (modules/hermes) - do not edit by hand.
# Ensures ~/.local/bin (where the Hermes installer places its binary) is on PATH.
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
SNIPPET
  chmod 644 "$snippet"

  local rc
  for rc in "${HOME}/.bashrc" "${HOME}/.profile"; do
    if [[ -f "$rc" ]] && grep -qF "$HERMES_PATH_MARKER_BEGIN" "$rc" 2>/dev/null; then
      log_info "hermes: PATH snippet already wired into ${rc}"
      continue
    fi
    omes_manage_path "$rc"
    {
      printf '\n%s\n' "$HERMES_PATH_MARKER_BEGIN"
      printf '. "%s"\n' "$snippet"
      printf '%s\n' "$HERMES_PATH_MARKER_END"
    } >> "$rc"
    log_info "hermes: wired PATH snippet into ${rc}"
  done
}

# _hermes_remove_marker_block <rc-file>
# Best-effort removal of the OMES-managed marker block from <rc-file>
# (used by module_rollback). No-op if the file or the marker is absent.
_hermes_remove_marker_block() {
  local rc="$1"
  [[ -f "$rc" ]] || return 0
  grep -qF "$HERMES_PATH_MARKER_BEGIN" "$rc" 2>/dev/null || return 0

  local tmp
  tmp="$(mktemp "${rc}.omes-rollback.XXXXXX")"
  awk -v b="$HERMES_PATH_MARKER_BEGIN" -v e="$HERMES_PATH_MARKER_END" '
    $0==b {skip=1; next}
    $0==e {skip=0; next}
    skip {next}
    {print}
  ' "$rc" > "$tmp"
  mv -f "$tmp" "$rc"
}

# _hermes_ensure_env_file <home>
# Creates $home/.env (mode 0600) if absent. Never overwrites an existing
# .env (that would destroy operator-managed secrets).
_hermes_ensure_env_file() {
  local home="$1"
  local env_file="${home}/.env"

  if omes_dry_run; then
    log_info "[dry-run] would ensure ${env_file} exists with mode 0600"
    return 0
  fi

  omes_manage_path "$env_file"
  if [[ ! -e "$env_file" ]]; then
    mkdir -p "$home"
    : > "$env_file"
    log_info "hermes: created ${env_file}"
  fi
  chmod 600 "$env_file"
}

# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------

module_check() {
  if omes_is_root; then
    log_error "hermes: must not run as root - Hermes installs per-user; re-run as the target user without sudo (see docs/hermes-integration.md)"
    return 1
  fi

  if ! command -v curl >/dev/null 2>&1; then
    log_error "hermes: curl not found (required to download the Hermes installer)"
    return 1
  fi

  if [[ ! -w "$HOME" ]]; then
    log_error "hermes: \$HOME (${HOME}) is not writable"
    return 1
  fi

  local installed_version
  if installed_version="$(_hermes_installed_version)"; then
    log_info "hermes: already installed (${installed_version})"
    return 0
  fi

  log_info "hermes: not yet installed"
  if ! detect_network; then
    log_error "hermes: network required to download the Hermes installer"
    return 1
  fi
  return 0
}

module_apply() {
  local home
  home="$(_hermes_home)"
  export HERMES_HOME="$home"

  local current_version=""
  current_version="$(_hermes_installed_version || true)"

  local need_install=1
  if [[ -n "$current_version" ]]; then
    if [[ -z "${OMES_HERMES_VERSION:-}" ]] || [[ "$current_version" == *"${OMES_HERMES_VERSION}"* ]]; then
      need_install=0
    fi
  fi

  if [[ "$need_install" -eq 1 ]]; then
    if ! _hermes_download_and_install "$home"; then
      return 1
    fi
  else
    log_info "hermes: already installed (${current_version}); skipping installer download"
  fi

  _hermes_ensure_path_snippet
  _hermes_ensure_env_file "$home"

  if ! omes_dry_run; then
    state_set "module.hermes.home" "$home"
  fi

  return 0
}

module_verify() {
  if omes_dry_run; then
    log_info "[dry-run] would verify: hermes --version and hermes doctor"
    return 0
  fi

  _hermes_ensure_runtime_path

  local version_out
  if ! version_out="$(hermes --version 2>&1)"; then
    log_error "hermes: 'hermes --version' failed: ${version_out}"
    return 1
  fi
  log_info "hermes: version: ${version_out}"

  local doctor_out
  if ! doctor_out="$(hermes doctor 2>&1)"; then
    log_error "hermes: 'hermes doctor' reported a failure:"
    log_error "${doctor_out}"
    return 1
  fi

  if grep -qi 'warn' <<<"$doctor_out"; then
    log_warn "hermes: 'hermes doctor' reported warnings:"
    log_warn "${doctor_out}"
  else
    log_info "hermes: 'hermes doctor' passed with no warnings"
  fi

  return 0
}

module_rollback() {
  local home
  home="$(_hermes_home)"
  local snippet
  snippet="$(_hermes_path_snippet_file)"

  log_warn "hermes: rollback removes only OMES-managed PATH wiring; it never deletes ${home} or any Hermes data"

  if omes_dry_run; then
    log_info "[dry-run] would remove ${snippet} and the OMES marker block from ~/.bashrc and ~/.profile"
    return 0
  fi

  if [[ -f "$snippet" ]]; then
    rm -f "$snippet"
    log_info "hermes: removed ${snippet}"
  fi

  local rc
  for rc in "${HOME}/.bashrc" "${HOME}/.profile"; do
    _hermes_remove_marker_block "$rc"
  done

  state_unset "module.hermes.home"

  log_warn "hermes: to fully remove Hermes yourself, run: rm -rf '${home}' \"\$HOME/.local/bin/hermes\""
  return 0
}
