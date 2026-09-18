#!/usr/bin/env bash
# shellcheck shell=bash
# modules/hermes-gateway/telegram-allowlist.sh - manage the Hermes Telegram
# chat-id allowlist safely. Invoked by modules/hermes-gateway/module.sh's
# module_verify (as a doctor-style check, when a Telegram token is
# configured) and fully runnable standalone by an operator.
#
# Usage:
#   telegram-allowlist.sh add <chat-id>
#   telegram-allowlist.sh remove <chat-id>
#   telegram-allowlist.sh --check
#   telegram-allowlist.sh diagnose <chat-id> [--member <user-id>]
#
# Design rules (see docs/telegram-security.md):
#   - Only numeric chat ids are accepted for add/remove; this also means a
#     bot token (never numeric) can never be written through this script.
#   - TELEGRAM_ALLOWED_CHATS and TELEGRAM_GROUP_ALLOWED_CHATS are always
#     written together, atomically (temp file + mv, same directory as
#     .env so the rename is same-filesystem), preserving .env's mode
#     (0600) and, best-effort, its owner.
#   - Never restarts the gateway; always prints the exact restart command
#     and warns the change is inert until that restart (allowlists are
#     read once, at gateway start).
#   - Never prints TELEGRAM_BOT_TOKEN, anywhere, for any command.
#   - `diagnose` only ever calls the safe, read-only getChat /
#     getChatMember / getChatMemberCount endpoints - it deliberately never
#     uses the long-polling conflict-prone read endpoint that would steal
#     updates from a running gateway (see docs/telegram-security.md for
#     the full explanation; that endpoint's name is intentionally not
#     spelled out in this script so an automated guard over modules/**/*.sh
#     stays meaningful). The bot token is read from .env in-process and is
#     never placed in argv or in a URL that reaches stdout/logs - the
#     actual request URL lives only in a 0600 curl config file
#     (`curl -K`), deleted immediately after use.

set -Eeuo pipefail
IFS=$'\n\t'

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${OMES_ROOT:-}" ]]; then
  OMES_ROOT="$(cd "${SELF_DIR}/../.." && pwd)"
fi

if [[ -r "${OMES_ROOT}/lib/omes/core.sh" ]] && [[ -r "${OMES_ROOT}/lib/omes/log.sh" ]]; then
  # shellcheck source=/dev/null
  source "${OMES_ROOT}/lib/omes/core.sh"
  # shellcheck source=/dev/null
  source "${OMES_ROOT}/lib/omes/log.sh"
else
  # Defensive fallback so this script still works if ever copied/run
  # outside a full OMES checkout; in practice it always ships with the
  # rest of the repo.
  log_info() { printf '[telegram-allowlist] %s\n' "$*"; }
  log_warn() { printf '[telegram-allowlist] WARN %s\n' "$*" >&2; }
  log_error() { printf '[telegram-allowlist] ERROR %s\n' "$*" >&2; }
fi

# ---------------------------------------------------------------------------
# .env location and low-level read/write
# ---------------------------------------------------------------------------

_ta_hermes_home() {
  printf '%s\n' "${OMES_HERMES_HOME:-${HOME}/.hermes}"
}

_ta_env_file() {
  printf '%s/.env\n' "$(_ta_hermes_home)"
}

# _ta_is_numeric_chat_id <value>
# Telegram chat ids are integers (negative for groups/supergroups/channels).
# Rejects wildcards, empty values, and anything token-shaped.
_ta_is_numeric_chat_id() {
  [[ "$1" =~ ^-?[0-9]+$ ]]
}

# _ta_env_get <key> <file>
# Prints the raw value of KEY=... (last occurrence wins), returns 1 if the
# key is absent or the file is unreadable.
_ta_env_get() {
  local key="$1" file="$2"
  [[ -r "$file" ]] || return 1
  local line value found=1
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      "${key}="*)
        value="${line#"${key}="}"
        found=0
        ;;
    esac
  done <"$file"
  [[ "$found" -eq 0 ]] || return 1
  printf '%s\n' "$value"
}

# _ta_env_write_two <key1> <val1> <key2> <val2> <file>
# Atomically rewrites <file>, replacing (or appending, if absent) exactly
# these two keys and leaving every other line - including any
# TELEGRAM_BOT_TOKEN line - byte-for-byte untouched. Writes to a temp file
# in the SAME directory first (guaranteeing a same-filesystem rename), so
# a failure creating/writing that temp file (e.g. a read-only directory)
# leaves the original file completely intact - never a partial write.
_ta_env_write_two() {
  local key1="$1" val1="$2" key2="$3" val2="$4" file="$5"
  local dir
  dir="$(dirname "$file")"
  mkdir -p "$dir"

  local tmp
  if ! tmp="$(mktemp "${dir}/.env.XXXXXX" 2>/dev/null)"; then
    log_error "telegram-allowlist: failed to create a temp file under ${dir} (is it writable?); ${file} was NOT modified"
    return 1
  fi

  local wrote1=0 wrote2=0
  if [[ -r "$file" ]]; then
    local line
    while IFS= read -r line || [[ -n "$line" ]]; do
      case "$line" in
        "${key1}="*)
          printf '%s=%s\n' "$key1" "$val1" >>"$tmp"
          wrote1=1
          ;;
        "${key2}="*)
          printf '%s=%s\n' "$key2" "$val2" >>"$tmp"
          wrote2=1
          ;;
        *)
          printf '%s\n' "$line" >>"$tmp"
          ;;
      esac
    done <"$file"
  fi
  [[ "$wrote1" -eq 1 ]] || printf '%s=%s\n' "$key1" "$val1" >>"$tmp"
  [[ "$wrote2" -eq 1 ]] || printf '%s=%s\n' "$key2" "$val2" >>"$tmp"

  chmod 600 "$tmp"
  if [[ -e "$file" ]]; then
    # Best-effort owner/group preservation; normally a no-op (an operator
    # can only chown to accounts they already own/are permitted to use),
    # so a failure here is silently ignored rather than fatal.
    chown --reference="$file" "$tmp" 2>/dev/null || true
  fi

  mv -f "$tmp" "$file"
}

# ---------------------------------------------------------------------------
# CSV (comma-separated chat-id list) helpers
# ---------------------------------------------------------------------------

# _ta_csv_to_lines <csv>
# Prints one trimmed, non-empty element per line.
_ta_csv_to_lines() {
  local csv="$1" part
  local IFS=','
  for part in $csv; do
    part="${part#"${part%%[![:space:]]*}"}"
    part="${part%"${part##*[![:space:]]}"}"
    [[ -n "$part" ]] && printf '%s\n' "$part"
  done
}

# _ta_lines_to_csv
# Reads lines on stdin, prints a de-duplicated (first occurrence kept,
# order preserved), comma-joined string.
_ta_lines_to_csv() {
  local line joined="" seen=":"
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    case "$seen" in
      *":${line}:"*) continue ;;
    esac
    seen="${seen}${line}:"
    joined="${joined:+${joined},}${line}"
  done
  printf '%s' "$joined"
}

# _ta_csv_remove <csv> <id>
_ta_csv_remove() {
  local csv="$1" id="$2"
  _ta_csv_to_lines "$csv" | grep -vxF -- "$id" | _ta_lines_to_csv || true
}

# ---------------------------------------------------------------------------
# Restart reminder
# ---------------------------------------------------------------------------

_ta_print_restart_warning() {
  log_warn "telegram-allowlist: this change is INERT until the gateway restarts - allowlists are read once, at gateway start (see docs/telegram-security.md)"
  log_warn "telegram-allowlist: restart with: systemctl --user restart hermes-gateway   (user-mode gateway)"
  log_warn "telegram-allowlist: or, for a system-mode gateway: sudo systemctl restart hermes-gateway"
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_add() {
  local chat_id="$1"
  if ! _ta_is_numeric_chat_id "$chat_id"; then
    log_error "telegram-allowlist: '${chat_id}' is not a valid numeric Telegram chat id (wildcards and non-numeric values are rejected)"
    return 1
  fi

  local file
  file="$(_ta_env_file)"

  local allowed_chats group_chats
  allowed_chats="$(_ta_env_get TELEGRAM_ALLOWED_CHATS "$file" || true)"
  group_chats="$(_ta_env_get TELEGRAM_GROUP_ALLOWED_CHATS "$file" || true)"

  local new_allowed new_group
  new_allowed="$({
    _ta_csv_to_lines "$allowed_chats"
    printf '%s\n' "$chat_id"
  } | _ta_lines_to_csv)"
  new_group="$({
    _ta_csv_to_lines "$group_chats"
    printf '%s\n' "$chat_id"
  } | _ta_lines_to_csv)"

  if [[ "$new_allowed" == "$allowed_chats" ]] && [[ "$new_group" == "$group_chats" ]]; then
    log_info "telegram-allowlist: '${chat_id}' is already present in both TELEGRAM_ALLOWED_CHATS and TELEGRAM_GROUP_ALLOWED_CHATS; nothing to do"
    return 0
  fi

  if ! _ta_env_write_two TELEGRAM_ALLOWED_CHATS "$new_allowed" TELEGRAM_GROUP_ALLOWED_CHATS "$new_group" "$file"; then
    return 1
  fi
  log_info "telegram-allowlist: added '${chat_id}' to both TELEGRAM_ALLOWED_CHATS and TELEGRAM_GROUP_ALLOWED_CHATS"
  _ta_print_restart_warning
}

cmd_remove() {
  local chat_id="$1"
  if ! _ta_is_numeric_chat_id "$chat_id"; then
    log_error "telegram-allowlist: '${chat_id}' is not a valid numeric Telegram chat id"
    return 1
  fi

  local file
  file="$(_ta_env_file)"

  local allowed_chats group_chats
  allowed_chats="$(_ta_env_get TELEGRAM_ALLOWED_CHATS "$file" || true)"
  group_chats="$(_ta_env_get TELEGRAM_GROUP_ALLOWED_CHATS "$file" || true)"

  local new_allowed new_group
  new_allowed="$(_ta_csv_remove "$allowed_chats" "$chat_id")"
  new_group="$(_ta_csv_remove "$group_chats" "$chat_id")"

  if [[ "$new_allowed" == "$allowed_chats" ]] && [[ "$new_group" == "$group_chats" ]]; then
    log_info "telegram-allowlist: '${chat_id}' was not present in either list; nothing to do"
    return 0
  fi

  if ! _ta_env_write_two TELEGRAM_ALLOWED_CHATS "$new_allowed" TELEGRAM_GROUP_ALLOWED_CHATS "$new_group" "$file"; then
    return 1
  fi
  log_info "telegram-allowlist: removed '${chat_id}' from both TELEGRAM_ALLOWED_CHATS and TELEGRAM_GROUP_ALLOWED_CHATS"
  _ta_print_restart_warning
}

cmd_check() {
  local file
  file="$(_ta_env_file)"

  local token_state="<not set>"
  if _ta_env_get TELEGRAM_BOT_TOKEN "$file" >/dev/null 2>&1; then
    token_state="***REDACTED***"
  fi
  printf '[telegram-allowlist] TELEGRAM_BOT_TOKEN=%s\n' "$token_state"

  local allowed_users allowed_chats group_chats
  allowed_users="$(_ta_env_get TELEGRAM_ALLOWED_USERS "$file" || true)"
  allowed_chats="$(_ta_env_get TELEGRAM_ALLOWED_CHATS "$file" || true)"
  group_chats="$(_ta_env_get TELEGRAM_GROUP_ALLOWED_CHATS "$file" || true)"

  printf '[telegram-allowlist] TELEGRAM_ALLOWED_USERS=%s\n' "${allowed_users:-<empty>}"
  printf '[telegram-allowlist] TELEGRAM_ALLOWED_CHATS=%s\n' "${allowed_chats:-<empty>}"
  printf '[telegram-allowlist] TELEGRAM_GROUP_ALLOWED_CHATS=%s\n' "${group_chats:-<empty>}"

  local -a only_allowed=()
  local -a only_group=()
  local id
  while IFS= read -r id; do
    [[ -z "$id" ]] && continue
    if ! _ta_csv_to_lines "$group_chats" | grep -qxF -- "$id"; then
      only_allowed+=("$id")
    fi
  done < <(_ta_csv_to_lines "$allowed_chats")
  while IFS= read -r id; do
    [[ -z "$id" ]] && continue
    if ! _ta_csv_to_lines "$allowed_chats" | grep -qxF -- "$id"; then
      only_group+=("$id")
    fi
  done < <(_ta_csv_to_lines "$group_chats")

  local rc=0
  if [[ "${#only_allowed[@]}" -gt 0 ]]; then
    log_warn "telegram-allowlist: half-enabled (in TELEGRAM_ALLOWED_CHATS only, missing from TELEGRAM_GROUP_ALLOWED_CHATS): ${only_allowed[*]}"
    rc=1
  fi
  if [[ "${#only_group[@]}" -gt 0 ]]; then
    log_warn "telegram-allowlist: half-enabled (in TELEGRAM_GROUP_ALLOWED_CHATS only, missing from TELEGRAM_ALLOWED_CHATS): ${only_group[*]}"
    rc=1
  fi
  if [[ "$rc" -eq 0 ]]; then
    log_info "telegram-allowlist: no half-enabled groups found"
  fi
  return "$rc"
}

# _ta_api_call <method> <query-string-without-token>
# Calls https://api.telegram.org/bot<TOKEN>/<method>?<query> without ever
# placing the token in argv or in any logged/printed URL: the full URL
# (the only place the token appears) lives solely in a 0600 curl config
# file (`curl -K`), created immediately before the call and removed
# immediately after. Prints the raw JSON response (chat/member metadata,
# never the token) on success.
_ta_api_call() {
  local method="$1" query="$2"
  local file
  file="$(_ta_env_file)"

  local token
  if ! token="$(_ta_env_get TELEGRAM_BOT_TOKEN "$file")" || [[ -z "$token" ]]; then
    log_error "telegram-allowlist: TELEGRAM_BOT_TOKEN is not set in ${file}"
    return 1
  fi

  if ! command -v curl >/dev/null 2>&1; then
    log_error "telegram-allowlist: curl not found"
    return 1
  fi

  local cfg
  if ! cfg="$(mktemp 2>/dev/null)"; then
    log_error "telegram-allowlist: failed to create a temp file for the API request"
    return 1
  fi
  chmod 600 "$cfg"
  {
    printf 'url = "https://api.telegram.org/bot%s/%s?%s"\n' "$token" "$method" "$query"
    printf 'silent\n'
    printf 'show-error\n'
  } >"$cfg"

  local out rc
  out="$(curl -K "$cfg" 2>&1)"
  rc=$?
  rm -f "$cfg"

  if [[ "$rc" -ne 0 ]]; then
    log_error "telegram-allowlist: request to ${method} failed (curl exit ${rc})"
    return 1
  fi
  printf '%s\n' "$out"
}

cmd_diagnose() {
  local chat_id="${1:-}"
  if [[ -z "$chat_id" ]] || ! _ta_is_numeric_chat_id "$chat_id"; then
    log_error "telegram-allowlist: diagnose requires a valid numeric chat id"
    return 1
  fi
  shift || true

  local user_id=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --member)
        user_id="${2:-}"
        shift 2
        ;;
      *)
        shift
        ;;
    esac
  done

  log_info "telegram-allowlist: getChat chat_id=${chat_id}"
  _ta_api_call "getChat" "chat_id=${chat_id}" || return 1

  log_info "telegram-allowlist: getChatMemberCount chat_id=${chat_id}"
  _ta_api_call "getChatMemberCount" "chat_id=${chat_id}" || return 1

  if [[ -n "$user_id" ]]; then
    log_info "telegram-allowlist: getChatMember chat_id=${chat_id} user_id=${user_id}"
    _ta_api_call "getChatMember" "chat_id=${chat_id}&user_id=${user_id}" || return 1
  fi
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_ta_usage() {
  cat <<'EOF'
Usage: telegram-allowlist.sh <command> [args]

Commands:
  add <chat-id>               Add <chat-id> to both TELEGRAM_ALLOWED_CHATS
                               and TELEGRAM_GROUP_ALLOWED_CHATS (atomic).
  remove <chat-id>             Remove <chat-id> from both lists (atomic).
  --check                      Report the current allowlists (token
                                redacted) and flag any half-enabled group.
  diagnose <chat-id> [--member <user-id>]
                               Safe read-only Telegram lookups: getChat,
                               getChatMemberCount, and getChatMember when
                               --member is given. See docs/telegram-security.md
                               for why the long-polling read endpoint is
                               never used here.

Every add/remove prints the exact restart command; the gateway is never
restarted automatically.
EOF
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    add)
      [[ $# -ge 2 ]] || {
        log_error "usage: telegram-allowlist.sh add <chat-id>"
        exit 2
      }
      cmd_add "$2"
      ;;
    remove)
      [[ $# -ge 2 ]] || {
        log_error "usage: telegram-allowlist.sh remove <chat-id>"
        exit 2
      }
      cmd_remove "$2"
      ;;
    --check | check)
      cmd_check
      ;;
    diagnose)
      [[ $# -ge 2 ]] || {
        log_error "usage: telegram-allowlist.sh diagnose <chat-id> [--member <user-id>]"
        exit 2
      }
      shift
      cmd_diagnose "$@"
      ;;
    -h | --help | "")
      _ta_usage
      [[ "$cmd" == "" ]] && exit 2
      exit 0
      ;;
    *)
      log_error "telegram-allowlist: unknown command: ${cmd}"
      _ta_usage
      exit 2
      ;;
  esac
}

# Only run main when executed directly, not when sourced (e.g. by bats
# tests that want to call the cmd_*/_ta_* functions in isolation).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
