#!/usr/bin/env bash
# shellcheck shell=bash
# modules/desktop-config/module.sh - templates keyboard-first Hyprland/
# Waybar/foot config plus a shell aliases snippet into the user's
# $XDG_CONFIG_HOME (issue #8). Omarchy-inspired, not copied - see
# config/hypr/hyprland.conf's header comment.
#
# The MODULE_* metadata variables below are read by lib/omes/module.sh
# after sourcing this file; ShellCheck cannot see that cross-file usage
# when analyzing this file on its own.
#
# Scope: MODULE_SCOPE=user. Never overwrites a pre-existing file that
# differs from the OMES template unless --yes is given (logged and
# skipped otherwise, recorded in state) - see module_apply/_dc_copy_file.
# MODULE_REQUIRES is intentionally empty: hyprland-session (root scope)
# and this module (user scope) never run in the same `omes install`
# invocation (module_filter_by_scope splits by privilege - see
# modules/hyprland-session/module.sh's header comment for the same
# reasoning), so there is no in-process ordering to declare; the intended
# operator sequence is documented in docs/linux-mint.md instead.

# shellcheck disable=SC2034
MODULE_NAME="desktop-config"
# shellcheck disable=SC2034
MODULE_DESCRIPTION="Templates Hyprland/Waybar/foot config and a shell aliases snippet into \$HOME (never overwrites a differing file without --yes)"
# shellcheck disable=SC2034
MODULE_SCOPE="user"
# shellcheck disable=SC2034
MODULE_REQUIRES=()
# shellcheck disable=SC2034
MODULE_PROFILES=(desktop)

DC_MARKER_BEGIN="# BEGIN OMES desktop shell config"
DC_MARKER_END="# END OMES desktop shell config"

# desktop-config's own skip bookkeeping for this module_apply run.
declare -ga DC_SKIPPED=()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_dc_home_config_dir() {
  printf '%s\n' "${XDG_CONFIG_HOME:-${HOME}/.config}"
}

_dc_shell_snippet_file() {
  printf '%s/omes/shell.sh\n' "$(_dc_home_config_dir)"
}

# _dc_copy_file <src> <dest>
# Installs <src> at <dest>: creates it if absent; leaves it alone (no-op)
# if identical; overwrites (backed up first via omes_manage_path) only
# when --yes/OMES_NONINTERACTIVE=1 is set; otherwise logs and records
# <dest> as skipped.
_dc_copy_file() {
  local src="$1" dest="$2"

  if [[ ! -e "$dest" ]]; then
    omes_manage_path "$dest"
    mkdir -p "$(dirname "$dest")"
    cp "$src" "$dest"
    chmod 644 "$dest"
    log_info "desktop-config: installed ${dest}"
    return 0
  fi

  if cmp -s "$src" "$dest"; then
    log_info "desktop-config: ${dest} already up to date"
    return 0
  fi

  if omes_noninteractive; then
    omes_manage_path "$dest"
    cp "$src" "$dest"
    chmod 644 "$dest"
    log_info "desktop-config: overwrote ${dest} (backed up first, --yes)"
    return 0
  fi

  log_warn "desktop-config: ${dest} exists and differs from the OMES template; skipping (re-run with --yes to overwrite - a backup is made first)"
  DC_SKIPPED+=("$dest")
  return 0
}

# _dc_copy_dir <name>
# Copies every top-level file in config/<name>/ (relative to OMES_ROOT)
# into $XDG_CONFIG_HOME/<name>/, via _dc_copy_file.
_dc_copy_dir() {
  local name="$1"
  local src_dir="${OMES_ROOT}/config/${name}"
  local dest_dir
  dest_dir="$(_dc_home_config_dir)/${name}"

  if [[ ! -d "$src_dir" ]]; then
    log_warn "desktop-config: template directory missing: ${src_dir}"
    return 0
  fi

  local f base
  for f in "$src_dir"/*; do
    [[ -f "$f" ]] || continue
    base="$(basename "$f")"
    if omes_dry_run; then
      log_info "[dry-run] would install ${dest_dir}/${base}"
      continue
    fi
    _dc_copy_file "$f" "${dest_dir}/${base}"
  done
}

# _dc_ensure_shell_snippet
# Installs config/shell/aliases.sh at $XDG_CONFIG_HOME/omes/shell.sh
# (subject to the same never-overwrite-without---yes rule) and wires it
# into ~/.bashrc via a marker block, idempotently - mirrors
# modules/hermes/module.sh's _hermes_ensure_path_snippet pattern.
_dc_ensure_shell_snippet() {
  local snippet src
  snippet="$(_dc_shell_snippet_file)"
  src="${OMES_ROOT}/config/shell/aliases.sh"

  if omes_dry_run; then
    log_info "[dry-run] would ensure ${snippet} and source it from ~/.bashrc"
    return 0
  fi

  if [[ ! -r "$src" ]]; then
    log_warn "desktop-config: template missing: ${src}"
    return 0
  fi

  _dc_copy_file "$src" "$snippet"

  local rc="${HOME}/.bashrc"
  if [[ -f "$rc" ]] && grep -qF "$DC_MARKER_BEGIN" "$rc" 2>/dev/null; then
    log_info "desktop-config: shell snippet already wired into ${rc}"
    return 0
  fi

  omes_manage_path "$rc"
  {
    printf '\n%s\n' "$DC_MARKER_BEGIN"
    printf '. "%s"\n' "$snippet"
    printf '%s\n' "$DC_MARKER_END"
  } >>"$rc"
  log_info "desktop-config: wired shell snippet into ${rc}"
}

_dc_remove_marker_block() {
  local rc="$1"
  [[ -f "$rc" ]] || return 0
  grep -qF "$DC_MARKER_BEGIN" "$rc" 2>/dev/null || return 0

  local tmp
  tmp="$(mktemp "${rc}.omes-rollback.XXXXXX")"
  awk -v b="$DC_MARKER_BEGIN" -v e="$DC_MARKER_END" '
    $0==b {skip=1; next}
    $0==e {skip=0; next}
    skip {next}
    {print}
  ' "$rc" >"$tmp"
  mv -f "$tmp" "$rc"
}

# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------

module_check() {
  if [[ ! -w "$HOME" ]]; then
    log_error "desktop-config: \$HOME (${HOME}) is not writable"
    return 1
  fi

  local d
  for d in hypr waybar foot shell; do
    if [[ ! -d "${OMES_ROOT}/config/${d}" ]]; then
      log_error "desktop-config: missing template directory: ${OMES_ROOT}/config/${d}"
      return 1
    fi
  done

  log_info "desktop-config: templates present, \$HOME writable"
  return 0
}

module_apply() {
  DC_SKIPPED=()

  local d
  for d in hypr waybar foot; do
    _dc_copy_dir "$d"
  done
  _dc_ensure_shell_snippet

  if ! omes_dry_run; then
    local joined=""
    if [[ "${#DC_SKIPPED[@]}" -gt 0 ]]; then
      local IFS=':'
      joined="${DC_SKIPPED[*]}"
      log_warn "desktop-config: skipped (pre-existing, differing, no --yes): ${DC_SKIPPED[*]}"
    fi
    state_set "module.desktop-config.skipped_paths" "$joined"
  fi

  return 0
}

module_verify() {
  if omes_dry_run; then
    log_info "[dry-run] would verify installed config files"
    return 0
  fi

  local ok=1
  local d src_dir dest_dir f base
  for d in hypr waybar foot; do
    src_dir="${OMES_ROOT}/config/${d}"
    dest_dir="$(_dc_home_config_dir)/${d}"
    [[ -d "$src_dir" ]] || continue
    for f in "$src_dir"/*; do
      [[ -f "$f" ]] || continue
      base="$(basename "$f")"
      if [[ ! -e "${dest_dir}/${base}" ]]; then
        log_error "desktop-config: expected file missing: ${dest_dir}/${base}"
        ok=0
      fi
    done
  done

  local snippet
  snippet="$(_dc_shell_snippet_file)"
  if [[ ! -e "$snippet" ]]; then
    log_error "desktop-config: expected shell snippet missing: ${snippet}"
    ok=0
  fi
  if [[ -f "${HOME}/.bashrc" ]] && ! grep -qF "$DC_MARKER_BEGIN" "${HOME}/.bashrc" 2>/dev/null; then
    log_error "desktop-config: shell snippet not wired into ~/.bashrc"
    ok=0
  fi

  [[ "$ok" -eq 1 ]]
}

module_rollback() {
  log_warn "desktop-config: rollback removes only the OMES-managed shell snippet and its ~/.bashrc marker block; installed hypr/waybar/foot config files are left in place - restore your previous versions with 'omes restore' (not implemented yet, tracked in #10) using the backups made before each write"

  if omes_dry_run; then
    log_info "[dry-run] would remove $(_dc_shell_snippet_file) and the OMES marker block from ~/.bashrc"
    return 0
  fi

  local snippet
  snippet="$(_dc_shell_snippet_file)"
  if [[ -f "$snippet" ]]; then
    rm -f "$snippet"
    log_info "desktop-config: removed ${snippet}"
  fi

  _dc_remove_marker_block "${HOME}/.bashrc"

  state_unset "module.desktop-config.skipped_paths" 2>/dev/null || true
  return 0
}
