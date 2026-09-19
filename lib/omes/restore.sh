#!/usr/bin/env bash
# shellcheck shell=bash
# lib/omes/restore.sh - restore-from-backup (docs/architecture.md Section
# 7.4) and the generic managed-path rollback used by `omes uninstall`.
#
# Meant to be sourced after lib/omes/core.sh, log.sh, state.sh, backup.sh.
# Every function here is offline-safe: none of it makes a network call
# (docs/architecture.md Section 11 requires `omes restore`/`omes uninstall`
# to work fully offline).

if [[ -n "${OMES_RESTORE_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317  # unreachable only when sourced; the `exit`
  # branch is the (correct) fallback for accidental direct execution.
  return 0 2>/dev/null || exit 0
fi
OMES_RESTORE_SH_LOADED=1

# ---------------------------------------------------------------------------
# Backup session resolution and manifest validation
# ---------------------------------------------------------------------------

# backup_target_dir [timestamp]
# Prints the backup directory to restore from: the exact <timestamp>
# directory if given, or (when omitted) the lexicographically-latest
# session (backup_list's chronological order). Returns 1 with no output
# when the requested timestamp does not exist, or no backups exist at all.
backup_target_dir() {
  local ts="${1:-}"
  local base
  base="$(omes_state_dir)/backups"

  if [[ -n "$ts" ]]; then
    if [[ -d "${base}/${ts}" ]]; then
      printf '%s\n' "${base}/${ts}"
      return 0
    fi
    return 1
  fi

  local latest
  latest="$(backup_list | tail -n1)"
  [[ -n "$latest" ]] || return 1
  printf '%s\n' "${base}/${latest}"
}

# backup_manifest_validate <backup-dir>
# Structural validation only (not hash verification): the MANIFEST must
# exist and be readable, and every non-blank line must match the
# "<64-hex-sha256>  <path-relative-to-/>" format backup_path (lib/omes/
# backup.sh) writes - note the path field has NO leading slash (it is the
# original absolute path with the leading "/" stripped, so the backup
# directory tree can mirror it; restore re-adds the "/"). A malformed
# MANIFEST is treated as corrupt - callers must refuse to restore from it
# (exit 9) rather than attempt a partial/best-guess parse.
backup_manifest_validate() {
  local dir="$1"
  local manifest="${dir}/MANIFEST"

  if [[ ! -r "$manifest" ]]; then
    log_error "restore: MANIFEST not found or unreadable: ${manifest}"
    return 1
  fi

  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    if ! [[ "$line" =~ ^[0-9a-f]{64}\ \ [^[:space:]] ]]; then
      log_error "restore: MANIFEST is corrupt (malformed line): ${manifest}"
      return 1
    fi
  done < "$manifest"

  return 0
}

# ---------------------------------------------------------------------------
# omes restore
# ---------------------------------------------------------------------------

# restore_backup [timestamp]
# Implements docs/architecture.md Section 7.4 against the resolved backup
# session (the given timestamp, or the latest). For each MANIFEST entry:
# verifies the backed-up copy's sha256, takes a fresh pre-restore-backup of
# any file it is about to overwrite, then restores it (cp -a, preserving
# mode/ownership). Honors dry-run (prints planned restores, mutates
# nothing). On any failure it stops immediately - files already restored
# by this invocation are left restored, not rolled further back - and
# returns non-zero; the caller (bin/omes cmd_restore) always maps a
# non-zero return to exit 9, naming the failing path via the log_error
# line already emitted. Works fully offline (no network call anywhere in
# this function).
restore_backup() {
  local requested_ts="$1"
  local dir

  if ! dir="$(backup_target_dir "$requested_ts")"; then
    if [[ -n "$requested_ts" ]]; then
      log_error "restore: no backup found for timestamp: ${requested_ts}"
    else
      log_error "restore: no backups available to restore"
    fi
    return 1
  fi

  # Guard against restoring from a session this shell still believes is
  # "current" (#129). This is exactly the state left behind when a caller
  # captured backup_finish via `$(...)` (ts="$(backup_finish)") - its
  # `unset OMES_CURRENT_BACKUP_DIR` only ran in that command-substitution
  # subshell, so the parent shell's OMES_CURRENT_BACKUP_DIR still points
  # at $dir. Two cases:
  #   - The session was genuinely finished (backup_finish's ".finished"
  #     marker is present): the stale variable is harmless but must not
  #     be treated as an in-progress session below, so drop it and
  #     proceed with the restore.
  #   - The session is still genuinely open (no marker - backup_finish
  #     was never called, e.g. mid-apply): restoring from a backup that
  #     is not closed out yet is refused outright, since its MANIFEST may
  #     still be gaining entries.
  if [[ -n "${OMES_CURRENT_BACKUP_DIR:-}" ]]; then
    local current_real dir_real
    current_real="$(realpath -m "$OMES_CURRENT_BACKUP_DIR" 2>/dev/null || printf '%s' "$OMES_CURRENT_BACKUP_DIR")"
    dir_real="$(realpath -m "$dir" 2>/dev/null || printf '%s' "$dir")"

    if [[ -e "${OMES_CURRENT_BACKUP_DIR}/.finished" ]]; then
      unset OMES_CURRENT_BACKUP_DIR
    elif [[ "$current_real" == "$dir_real" ]]; then
      log_error "restore: refusing to restore from the still-open current backup session: ${dir} (call backup_finish first)"
      return 1
    fi
  fi

  if ! backup_manifest_validate "$dir"; then
    return 1
  fi

  local manifest="${dir}/MANIFEST"
  local restored=0
  local -a manifest_lines=()
  # Read the whole MANIFEST up front rather than iterating a live file
  # handle: this function's own pre-restore-backup step below always opens
  # a brand-new session now (never OMES_CURRENT_BACKUP_DIR), so it can no
  # longer append into the very manifest being restored from - but
  # snapshotting first removes any dependency on that invariant holding
  # for every current and future caller (#129).
  mapfile -t manifest_lines < "$manifest"
  local line sha rel path src actual

  for line in "${manifest_lines[@]}"; do
    [[ -z "$line" ]] && continue
    sha="${line%%  *}"
    rel="${line#*  }"
    path="/${rel}"
    src="${dir}/${rel}"

    if [[ ! -f "$src" ]]; then
      log_error "restore: backed-up copy missing, aborting: ${src}"
      return 1
    fi

    actual="$(sha256sum "$src" | awk '{print $1}')"
    if [[ "$actual" != "$sha" ]]; then
      log_error "restore: checksum mismatch, aborting: ${path} (expected ${sha}, got ${actual})"
      return 1
    fi

    if omes_dry_run; then
      log_info "[dry-run] would restore: ${path}"
      restored=$((restored + 1))
      continue
    fi

    if [[ -e "$path" ]]; then
      # Never destroy the pre-restore state without a recovery path of its
      # own. Always open a fresh, short-lived session for this file - do
      # NOT reuse OMES_CURRENT_BACKUP_DIR here even if one is set: that is
      # precisely the bug in #129, where a stale/leftover "current"
      # session pointed at the backup this function is restoring FROM,
      # so backup_path would have appended pre-restore-backup entries
      # into the very MANIFEST this function is reading, growing the
      # loop it can never finish.
      backup_begin "restore" "pre-restore-backup" >/dev/null
      backup_path "$path"
      backup_finish >/dev/null
    fi

    mkdir -p "$(dirname "$path")"
    if ! cp -a "$src" "$path"; then
      log_error "restore: failed to write restored file, aborting: ${path}"
      return 1
    fi

    restored=$((restored + 1))
    log_info "restore: restored ${path}"
  done

  log_info "restore: restored ${restored} file(s) from $(basename "$dir")"
  return 0
}

# ---------------------------------------------------------------------------
# Generic managed-path rollback (used by `omes uninstall`)
# ---------------------------------------------------------------------------

# _restore_find_oldest_backup_for_path <module> <path>
# Prints the oldest (chronologically-first) backup directory whose META
# records module=<module> and whose MANIFEST captured a pre-existing copy
# of <path> - i.e. the backup taken just before OMES first touched a file
# that already existed. Returns 1 with no output when no such backup
# exists (meaning <path> was created fresh by OMES, never pre-existing).
_restore_find_oldest_backup_for_path() {
  local mod="$1" path="$2"
  local rel="${path#/}"
  local base
  base="$(omes_state_dir)/backups"
  [[ -d "$base" ]] || return 1

  local name entry meta manifest line p
  while IFS= read -r name; do
    entry="${base}/${name}"
    meta="${entry}/META"
    manifest="${entry}/MANIFEST"
    if [[ ! -r "$meta" ]] || [[ ! -r "$manifest" ]]; then
      continue
    fi
    grep -qxF "module=${mod}" "$meta" || continue

    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ -z "$line" ]] && continue
      p="${line#*  }"
      if [[ "$p" == "$rel" ]]; then
        printf '%s\n' "$entry"
        return 0
      fi
    done < "$manifest"
  done < <(backup_list)

  return 1
}

# _restore_copy_one <backup-dir> <path>
# Verifies the backed-up copy of <path> inside <backup-dir> against that
# session's own MANIFEST sha256, then restores it (cp -a). Returns 1
# (nothing written) on a missing copy or a checksum mismatch.
_restore_copy_one() {
  local backup_dir="$1" path="$2"
  local rel="${path#/}"
  local src="${backup_dir}/${rel}"
  local manifest="${backup_dir}/MANIFEST"

  if [[ ! -f "$src" ]]; then
    log_error "uninstall: backed-up copy missing: ${src}"
    return 1
  fi

  local expected="" line p
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    p="${line#*  }"
    if [[ "$p" == "$rel" ]]; then
      expected="${line%%  *}"
      break
    fi
  done < "$manifest"

  if [[ -z "$expected" ]]; then
    log_error "uninstall: no MANIFEST entry for: ${path} (in ${manifest})"
    return 1
  fi

  local actual
  actual="$(sha256sum "$src" | awk '{print $1}')"
  if [[ "$actual" != "$expected" ]]; then
    log_error "uninstall: checksum mismatch for ${path} (expected ${expected}, got ${actual})"
    return 1
  fi

  mkdir -p "$(dirname "$path")"
  cp -a "$src" "$path"
}

# module_rollback_managed_paths <module>
# Generic best-effort undo of every path in module.<module>.managed_paths
# (never touches anything else): a path that some backup captured as
# pre-existing (OMES modified a file that was already there) is restored
# from the OLDEST such backup, reconstructing what was there before OMES
# ever touched it; a path no backup ever captured (OMES created it fresh)
# is removed outright. This is what makes "uninstall never deletes user
# data" (docs/scope.md Section 5) concrete at the file level. Honors
# dry-run. Returns 0 on full success, 1 naming the first failing path via
# log_error (the caller, `omes uninstall`, maps this to exit 10).
module_rollback_managed_paths() {
  local mod="$1"
  local raw
  raw="$(state_get "module.${mod}.managed_paths" 2>/dev/null || true)"
  [[ -n "$raw" ]] || return 0

  local -a paths=()
  local old_ifs="$IFS"
  IFS=':'
  read -r -a paths <<< "$raw"
  IFS="$old_ifs"

  local p src_dir
  for p in "${paths[@]}"; do
    [[ -z "$p" ]] && continue

    if src_dir="$(_restore_find_oldest_backup_for_path "$mod" "$p")"; then
      if omes_dry_run; then
        log_info "[dry-run] would restore (pre-existing before OMES): ${p}"
        continue
      fi
      if ! _restore_copy_one "$src_dir" "$p"; then
        return 1
      fi
      log_info "uninstall: restored pre-existing file: ${p}"
    else
      if omes_dry_run; then
        log_info "[dry-run] would remove (OMES-created): ${p}"
        continue
      fi
      if [[ -e "$p" ]]; then
        rm -rf "$p"
      fi
      log_info "uninstall: removed OMES-created path: ${p}"
    fi
  done

  return 0
}
