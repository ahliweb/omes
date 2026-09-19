#!/usr/bin/env bash
# shellcheck shell=bash
# lib/omes/backup.sh - per-module backup sessions with sha256 manifests.
#
# Meant to be sourced after lib/omes/core.sh. Restore itself is implemented
# in a later issue (#10); `bin/omes restore` is a stub for now.

if [[ -n "${OMES_BACKUP_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317  # unreachable only when sourced; the `exit`
  # branch is the (correct) fallback for accidental direct execution.
  return 0 2>/dev/null || exit 0
fi
OMES_BACKUP_SH_LOADED=1

# backup_begin <module> <reason>
# Creates <state-dir>/backups/<UTC timestamp>/ (mode 0700) with an empty
# MANIFEST and a META file describing the session, and records it as the
# active backup session (OMES_CURRENT_BACKUP_DIR). Prints the backup dir.
backup_begin() {
  local module="$1"
  local reason="$2"

  # Defensive cleanup: OMES_CURRENT_BACKUP_DIR can still be set here even
  # though its session was already closed, when backup_finish was invoked
  # via `$(...)` (ts="$(backup_finish)") - the `unset` inside that call
  # only ran in the command-substitution subshell and never reached this
  # shell. backup_finish always writes a ".finished" marker into the
  # session directory before it unsets the variable, and that marker is a
  # file - it survives the subshell. When the stale value points at such a
  # finished session, drop it now so nothing below (or in a caller that
  # checks OMES_CURRENT_BACKUP_DIR before calling us) mistakes a finished
  # session for one that is still open. See docs/rollback.md and #129.
  if [[ -n "${OMES_CURRENT_BACKUP_DIR:-}" && -e "${OMES_CURRENT_BACKUP_DIR}/.finished" ]]; then
    unset OMES_CURRENT_BACKUP_DIR
  fi

  local backups_dir
  backups_dir="$(omes_state_dir)/backups"
  mkdir -p "$backups_dir"
  chmod 700 "$backups_dir"

  local ts
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  local backup_dir="${backups_dir}/${ts}"
  local suffix=1
  while [[ -e "$backup_dir" ]]; do
    backup_dir="${backups_dir}/${ts}-${suffix}"
    suffix=$((suffix + 1))
  done

  mkdir -p "$backup_dir"
  chmod 700 "$backup_dir"

  : > "${backup_dir}/MANIFEST"
  chmod 600 "${backup_dir}/MANIFEST"

  {
    printf 'omes_version=%s\n' "${OMES_VERSION:-unknown}"
    printf 'module=%s\n' "$module"
    printf 'reason=%s\n' "$reason"
    printf 'started_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "${backup_dir}/META"
  chmod 600 "${backup_dir}/META"

  OMES_CURRENT_BACKUP_DIR="$backup_dir"
  export OMES_CURRENT_BACKUP_DIR
  printf '%s\n' "$backup_dir"
}

# backup_path <path>
# Copies <path> (file or directory, preserving its relative layout under
# the backup dir) into the active backup session and appends one
# "sha256  original-path" line per regular file to MANIFEST. A no-op when
# <path> does not exist (nothing to protect yet) or when no session is
# active.
backup_path() {
  local src="$1"

  if [[ -z "${OMES_CURRENT_BACKUP_DIR:-}" ]]; then
    if command -v log_error >/dev/null 2>&1; then
      log_error "backup_path called without an active backup session (call backup_begin first)"
    fi
    return 1
  fi

  if [[ ! -e "$src" ]]; then
    return 0
  fi

  local dirpart base abs rel dest
  dirpart="$(cd "$(dirname "$src")" && pwd)"
  base="$(basename "$src")"
  abs="${dirpart}/${base}"
  rel="${abs#/}"
  dest="${OMES_CURRENT_BACKUP_DIR}/${rel}"

  mkdir -p "$(dirname "$dest")"
  cp -a "$abs" "$dest"

  if [[ -d "$abs" ]]; then
    local f relf sum
    while IFS= read -r -d '' f; do
      relf="${f#/}"
      sum="$(sha256sum "$f" | awk '{print $1}')"
      printf '%s  %s\n' "$sum" "$relf" >> "${OMES_CURRENT_BACKUP_DIR}/MANIFEST"
    done < <(find "$abs" -type f -print0)
  else
    if [[ "$base" == ".env" ]]; then
      chmod 600 "$dest"
    fi
    local sum
    sum="$(sha256sum "$abs" | awk '{print $1}')"
    printf '%s  %s\n' "$sum" "$rel" >> "${OMES_CURRENT_BACKUP_DIR}/MANIFEST"
  fi
}

# backup_finish
# Closes out the active backup session (records finished_at in META) and
# prints its directory. No-op if no session is active.
#
# Contract (docs/rollback.md "Session identity"): the session id is
# ALWAYS printed on stdout (unchanged), is ALSO exported as
# OMES_LAST_BACKUP_ID (the session's basename, i.e. what backup_list
# prints) for callers that do not want to capture stdout, and a
# ".finished" marker file is written into the session directory before
# OMES_CURRENT_BACKUP_DIR is unset. The marker exists because neither the
# unset nor the OMES_LAST_BACKUP_ID export can be relied on to reach the
# calling shell when this function is invoked via `$(...)` - a
# command-substitution subshell's variable changes never propagate to its
# parent no matter what this function does. Only a file written to disk
# survives that boundary, which is what backup_begin and restore_backup
# use to detect a stale OMES_CURRENT_BACKUP_DIR left over from exactly
# that pattern. Callers should still prefer calling backup_finish as a
# plain statement (every callsite in this repository does) and reading
# OMES_CURRENT_BACKUP_DIR/OMES_LAST_BACKUP_ID afterward.
backup_finish() {
  if [[ -z "${OMES_CURRENT_BACKUP_DIR:-}" ]]; then
    return 0
  fi
  local dir="$OMES_CURRENT_BACKUP_DIR"
  printf 'finished_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "${dir}/META"

  : > "${dir}/.finished"
  chmod 600 "${dir}/.finished"

  OMES_LAST_BACKUP_ID="$(basename "$dir")"
  export OMES_LAST_BACKUP_ID

  unset OMES_CURRENT_BACKUP_DIR
  printf '%s\n' "$dir"
}

# backup_list
# Prints the UTC-timestamp directory names of every backup session, oldest
# first (lexicographic order matches chronological order for this
# timestamp format).
backup_list() {
  local dir
  dir="$(omes_state_dir)/backups"
  [[ -d "$dir" ]] || return 0

  local entry
  for entry in "$dir"/*/; do
    [[ -d "$entry" ]] || continue
    basename "$entry"
  done | sort
}

# backup_prune [keep]
# Deletes the oldest backup sessions beyond the last <keep>. Defaults to
# the OMES_BACKUP_KEEP environment variable when no argument is given,
# falling back to 10 when that is unset too (docs/architecture.md Section
# 7.5). Wired in by lib/omes/module.sh's run_apply (after every successful
# module_apply's backup) and by `omes backup` (bin/omes).
backup_prune() {
  local keep="${1:-${OMES_BACKUP_KEEP:-10}}"
  local dir
  dir="$(omes_state_dir)/backups"
  [[ -d "$dir" ]] || return 0

  local -a backups=()
  local name
  while IFS= read -r name; do
    backups+=("$name")
  done < <(backup_list)

  local total="${#backups[@]}"
  if (( total <= keep )); then
    return 0
  fi

  local remove_count=$((total - keep))
  local i target
  for ((i = 0; i < remove_count; i++)); do
    target="${dir}/${backups[$i]}"
    if [[ -d "$target" ]]; then
      rm -rf "$target"
    fi
  done
}
