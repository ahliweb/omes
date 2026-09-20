#!/usr/bin/env bash
# shellcheck shell=bash
# lib/omes/state.sh - state directory + key=value state file read/write.
#
# Meant to be sourced after lib/omes/core.sh (uses omes_state_dir()).

if [[ -n "${OMES_STATE_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317  # unreachable only when sourced; the `exit`
  # branch is the (correct) fallback for accidental direct execution.
  return 0 2>/dev/null || exit 0
fi
OMES_STATE_SH_LOADED=1

# state_init
# Creates the state directory (and backups/logs subdirectories) with mode
# 0700, and an empty state file with mode 0600 if one does not exist yet.
# Prints the resolved state directory.
state_init() {
  local dir
  dir="$(omes_state_dir)"

  mkdir -p "$dir"
  chmod 700 "$dir"

  mkdir -p "${dir}/backups"
  chmod 700 "${dir}/backups"

  mkdir -p "${dir}/logs"
  chmod 700 "${dir}/logs"

  local state_file="${dir}/state"
  if [[ ! -e "$state_file" ]]; then
    : >"$state_file"
  fi
  chmod 600 "$state_file"

  OMES_STATE_DIR="$dir"
  export OMES_STATE_DIR
  printf '%s\n' "$dir"
}

# state_file_path
# Prints the path to the state file (does not guarantee it exists).
state_file_path() {
  printf '%s/state\n' "$(omes_state_dir)"
}

# state_get <key>
# Prints the value for <key> and returns 0, or returns 1 when the key is
# absent or the state file does not exist.
state_get() {
  local key="$1"
  local file
  file="$(state_file_path)"
  [[ -r "$file" ]] || return 1

  local prefix="${key}="
  local line
  while IFS= read -r line; do
    if [[ "$line" == "${prefix}"* ]]; then
      printf '%s\n' "${line#"$prefix"}"
      return 0
    fi
  done <"$file"
  return 1
}

# state_set <key> <value>
# Atomically writes <key>=<value> into the state file (replacing any
# previous value for the same key), creating the state dir/file as needed.
# The state file is kept at mode 0600.
state_set() {
  local key="$1"
  local value="$2"
  local dir file tmp

  dir="$(omes_state_dir)"
  mkdir -p "$dir"
  chmod 700 "$dir" 2>/dev/null || true

  file="${dir}/state"
  tmp="$(mktemp "${dir}/.state.XXXXXX")"

  {
    if [[ -r "$file" ]]; then
      local line
      while IFS= read -r line; do
        [[ "$line" == "${key}="* ]] && continue
        printf '%s\n' "$line"
      done <"$file"
    fi
    printf '%s=%s\n' "$key" "$value"
  } >"$tmp"

  chmod 600 "$tmp"
  mv -f "$tmp" "$file"
}

# state_unset <key>
# Removes <key> from the state file, if present. No-op if the state file
# does not exist.
state_unset() {
  local key="$1"
  local dir file tmp

  dir="$(omes_state_dir)"
  file="${dir}/state"
  [[ -r "$file" ]] || return 0

  tmp="$(mktemp "${dir}/.state.XXXXXX")"
  local line
  while IFS= read -r line; do
    [[ "$line" == "${key}="* ]] && continue
    printf '%s\n' "$line"
  done <"$file" >"$tmp"

  chmod 600 "$tmp"
  mv -f "$tmp" "$file"
}

# state_list_modules
# Prints the distinct module names that have any module.<name>.* key in
# the state file, one per line, in first-seen order.
state_list_modules() {
  local file
  file="$(state_file_path)"
  [[ -r "$file" ]] || return 0

  local -A seen=()
  local line key mod
  while IFS= read -r line; do
    key="${line%%=*}"
    case "$key" in
      module.*.*)
        mod="${key#module.}"
        mod="${mod%.*}"
        if [[ -z "${seen[$mod]:-}" ]]; then
          seen[$mod]=1
          printf '%s\n' "$mod"
        fi
        ;;
    esac
  done <"$file"
}
