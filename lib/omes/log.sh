#!/usr/bin/env bash
# shellcheck shell=bash
# lib/omes/log.sh - human + JSON-aware logging, always mirrored to a log file.
#
# This file is meant to be sourced after lib/omes/core.sh.

if [[ -n "${OMES_LOG_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317  # unreachable only when sourced; the `exit`
  # branch is the (correct) fallback for accidental direct execution.
  return 0 2>/dev/null || exit 0
fi
OMES_LOG_SH_LOADED=1

# ---------------------------------------------------------------------------
# Log file resolution
# ---------------------------------------------------------------------------

# log_file_path
# Prints the path to the current run's log file, resolving and caching it
# (via OMES_LOG_FILE) on first use. Honors OMES_LOG_FILE / --log-file.
log_file_path() {
  if [[ -n "${OMES_LOG_FILE:-}" ]]; then
    printf '%s\n' "$OMES_LOG_FILE"
    return 0
  fi

  local dir
  dir="$(omes_state_dir)/logs"
  mkdir -p "$dir" 2>/dev/null || true
  local ts
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  OMES_LOG_FILE="${dir}/omes-${ts}.log"
  export OMES_LOG_FILE
  printf '%s\n' "$OMES_LOG_FILE"
}

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

_log_color_enabled() {
  local fd="$1"
  [[ -z "${NO_COLOR:-}" ]] && [[ -t "$fd" ]]
}

_log_colorize() {
  local color="$1" text="$2" fd="$3"
  if _log_color_enabled "$fd"; then
    printf '\033[%sm%s\033[0m' "$color" "$text"
  else
    printf '%s' "$text"
  fi
}

# ---------------------------------------------------------------------------
# Core writer
# ---------------------------------------------------------------------------

# _log_write <level-name> <color-code> <fd> <message>
# fd is 1 (stdout) or 2 (stderr) for the human-readable stream; the log file
# always receives the line, and JSON mode forces everything to stderr.
_log_write() {
  local level="$1" color="$2" fd="$3" msg="$4"
  local redacted
  redacted="$(omes_redact "$msg")"

  local prefix="[omes] "
  if [[ -n "$level" ]]; then
    prefix="[omes] ${level} "
  fi

  local logfile
  logfile="$(log_file_path)"
  printf '%s %s%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$prefix" "$redacted" \
    >> "$logfile" 2>/dev/null || true

  local out_fd="$fd"
  if [[ "${OMES_JSON:-0}" == "1" ]]; then
    out_fd=2
  fi

  local colored_prefix
  colored_prefix="$(_log_colorize "$color" "$prefix" "$out_fd")"
  if [[ "$out_fd" == "1" ]]; then
    printf '%s%s\n' "$colored_prefix" "$redacted"
  else
    printf '%s%s\n' "$colored_prefix" "$redacted" >&2
  fi
}

# ---------------------------------------------------------------------------
# Public logging functions
# ---------------------------------------------------------------------------

log_info() {
  _log_write "" "0;36" "1" "$*"
}

log_warn() {
  _log_write "WARN" "0;33" "2" "$*"
}

log_error() {
  _log_write "ERROR" "0;31" "2" "$*"
}

log_debug() {
  if [[ "${OMES_VERBOSE:-0}" != "1" ]]; then
    # Debug lines are only ever suppressed from the console; they still go
    # to the log file for later inspection when verbose logging is wanted.
    local redacted logfile
    redacted="$(omes_redact "$*")"
    logfile="$(log_file_path)"
    printf '%s [omes] DEBUG %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$redacted" \
      >> "$logfile" 2>/dev/null || true
    return 0
  fi
  _log_write "DEBUG" "1;30" "1" "$*"
}
