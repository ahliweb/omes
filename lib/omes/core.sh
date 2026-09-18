#!/usr/bin/env bash
# shellcheck shell=bash
# lib/omes/core.sh - strict mode, constants, exit codes, common helpers.
#
# This file is meant to be sourced, never executed directly.

if [[ -n "${OMES_CORE_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317  # unreachable only when sourced; the `exit`
  # branch is the (correct) fallback for accidental direct execution.
  return 0 2>/dev/null || exit 0
fi
OMES_CORE_SH_LOADED=1

set -Eeuo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# OMES_ROOT should normally be set by bin/omes (resolved via `readlink -f`).
# Fall back to a path relative to this file so the library also works when
# sourced directly, e.g. from unit tests.
if [[ -z "${OMES_ROOT:-}" ]]; then
  OMES_CORE_SELF="${BASH_SOURCE[0]}"
  OMES_ROOT="$(cd "$(dirname "${OMES_CORE_SELF}")/../.." && pwd)"
  unset OMES_CORE_SELF
fi
export OMES_ROOT

OMES_LIB_DIR="${OMES_ROOT}/lib/omes"
export OMES_LIB_DIR

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

if [[ -r "${OMES_ROOT}/VERSION" ]]; then
  OMES_VERSION="$(tr -d '[:space:]' < "${OMES_ROOT}/VERSION")"
else
  OMES_VERSION="unknown"
fi
export OMES_VERSION

# ---------------------------------------------------------------------------
# Exit codes (stable; documented in docs/cli.md)
# ---------------------------------------------------------------------------

# Read by every other sourced file (bin/omes, modules/*, tests); ShellCheck
# cannot see that cross-file usage when analyzing this file on its own.
# shellcheck disable=SC2034
readonly OMES_EX_OK=0             # success
# shellcheck disable=SC2034
readonly OMES_EX_ERROR=1          # general/unexpected error
# shellcheck disable=SC2034
readonly OMES_EX_USAGE=2          # usage error
# shellcheck disable=SC2034
readonly OMES_EX_UNSUPPORTED=3    # unsupported platform (OS/arch)
# shellcheck disable=SC2034
readonly OMES_EX_PREFLIGHT=4      # preflight failed
# shellcheck disable=SC2034
readonly OMES_EX_PRIVILEGE=5      # privilege error (needs root / must not be root)
# shellcheck disable=SC2034
readonly OMES_EX_MODULE_APPLY=6   # module apply failed
# shellcheck disable=SC2034
readonly OMES_EX_VERIFY=7         # verification failed
# shellcheck disable=SC2034
readonly OMES_EX_NETWORK=8        # network required but unavailable
# shellcheck disable=SC2034
readonly OMES_EX_BACKUP=9         # backup/restore failed
# shellcheck disable=SC2034
readonly OMES_EX_ROLLBACK=10      # rollback failed

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

# omes_redact <line>
# Masks the value following TOKEN=, KEY=, SECRET= or PASSWORD= (case
# insensitive) in a single line of text, so secrets never reach logs.
omes_redact() {
  local line="$1"
  printf '%s' "$line" | sed -E \
    -e 's/([A-Za-z_]*(TOKEN|KEY|SECRET|PASSWORD)[A-Za-z_]*=)[^[:space:]]+/\1[REDACTED]/gI'
}

# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

# omes_die <exit-code> <message...>
# Prints an error (redacted) and exits with the given code.
omes_die() {
  local code="$1"
  shift
  local msg="$*"
  if command -v log_error >/dev/null 2>&1; then
    log_error "$msg"
  else
    printf '[omes] ERROR: %s\n' "$(omes_redact "$msg")" >&2
  fi
  exit "$code"
}

# omes_require_cmd <command> [exit-code]
# Fails fast with a clear message when a required external command is
# missing from PATH.
omes_require_cmd() {
  local cmd="$1"
  local code="${2:-$OMES_EX_ERROR}"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    omes_die "$code" "required command not found: $cmd"
  fi
}

# omes_is_root
# True (0) when running as UID 0. Honors the OMES_FAKE_ROOT=1 test hook,
# which is only ever consulted when OMES_TEST=1 is also set, so it can
# never affect a real (non-test) invocation.
omes_is_root() {
  if [[ "${OMES_TEST:-0}" == "1" ]] && [[ "${OMES_FAKE_ROOT:-0}" == "1" ]]; then
    return 0
  fi
  [[ "$(id -u)" -eq 0 ]]
}

# omes_state_dir
# Prints the resolved state directory for the current scope (root vs user),
# honoring OMES_STATE_DIR as an override. Does not create the directory;
# see state_init() in lib/omes/state.sh for that.
omes_state_dir() {
  if [[ -n "${OMES_STATE_DIR:-}" ]]; then
    printf '%s\n' "$OMES_STATE_DIR"
    return 0
  fi
  if omes_is_root; then
    printf '%s\n' "/var/lib/omes"
  else
    printf '%s\n' "${XDG_STATE_HOME:-$HOME/.local/state}/omes"
  fi
}

# omes_dry_run
# True (0) when --dry-run was passed or OMES_DRY_RUN=1 is set.
omes_dry_run() {
  [[ "${OMES_DRY_RUN:-0}" == "1" ]]
}

# omes_noninteractive
# True (0) when --yes/--non-interactive was passed or OMES_NONINTERACTIVE=1.
omes_noninteractive() {
  [[ "${OMES_NONINTERACTIVE:-0}" == "1" ]] || [[ "${OMES_ASSUME_YES:-0}" == "1" ]]
}

# omes_run <command> [args...]
# Logs the command (redacted) and executes it unless in dry-run mode, in
# which case it only prints what would run and returns success.
omes_run() {
  local -a cmd=("$@")
  local display
  display="$(printf '%q ' "${cmd[@]}")"
  display="${display% }"
  display="$(omes_redact "$display")"

  if omes_dry_run; then
    if command -v log_info >/dev/null 2>&1; then
      log_info "[dry-run] would run: ${display}"
    else
      printf '[omes] [dry-run] would run: %s\n' "$display"
    fi
    return 0
  fi

  if command -v log_info >/dev/null 2>&1; then
    log_info "running: ${display}"
  fi
  "${cmd[@]}"
}

# omes_confirm <prompt>
# Auto-confirms when --yes/OMES_NONINTERACTIVE=1 is set; otherwise prompts
# interactively and returns 0 only on an explicit y/yes answer.
omes_confirm() {
  local prompt="${1:-Proceed?}"

  if omes_noninteractive; then
    return 0
  fi

  if [[ ! -t 0 ]]; then
    # No terminal to prompt on and no explicit confirmation: refuse.
    return 1
  fi

  local reply=""
  read -r -p "${prompt} [y/N] " reply || true
  case "$reply" in
    y | Y | yes | YES | Yes)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

# ---------------------------------------------------------------------------
# ERR trap
# ---------------------------------------------------------------------------

# omes_err_trap <exit-code> <line-no> <command>
# Reports the failing command and its source line to stderr / log file
# without leaking secrets. Installed via `trap ... ERR` by entry points that
# want this behavior (not installed automatically on source, so libraries
# remain safe to source from bats tests).
omes_err_trap() {
  local code="$1"
  local line="$2"
  local command="$3"
  local safe_command
  safe_command="$(omes_redact "$command")"
  local msg="unexpected error (exit ${code}) at line ${line}: ${safe_command}"
  if command -v log_error >/dev/null 2>&1; then
    log_error "$msg"
  else
    printf '[omes] ERROR: %s\n' "$msg" >&2
  fi
}

# omes_install_err_trap
# Installs the standard ERR trap for CLI entry points.
omes_install_err_trap() {
  trap 'omes_err_trap "$?" "$LINENO" "$BASH_COMMAND"' ERR
}
