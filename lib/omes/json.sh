#!/usr/bin/env bash
# shellcheck shell=bash
# lib/omes/json.sh - minimal JSON building helpers (no jq dependency).
#
# This file is meant to be sourced. It has no dependency on core.sh so it
# can be unit-tested in isolation.

if [[ -n "${OMES_JSON_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317  # unreachable only when sourced; the `exit`
  # branch is the (correct) fallback for accidental direct execution.
  return 0 2>/dev/null || exit 0
fi
OMES_JSON_SH_LOADED=1

# json_escape <string>
# Prints <string> with JSON special characters escaped. Does NOT add the
# surrounding double quotes.
json_escape() {
  local s="${1:-}"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\t'/\\t}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\n'/\\n}"
  printf '%s' "$s"
}

# json_kv <key> <value> [--raw]
# Prints a "key":"value" pair with both key and value escaped and quoted.
# With --raw, the value is emitted verbatim (for numbers, booleans, null,
# or already-built JSON such as nested objects/arrays).
json_kv() {
  local key="$1"
  local value="${2:-}"
  local mode="${3:-}"
  local ekey
  ekey="$(json_escape "$key")"
  if [[ "$mode" == "--raw" ]]; then
    printf '"%s":%s' "$ekey" "$value"
  else
    local evalue
    evalue="$(json_escape "$value")"
    printf '"%s":"%s"' "$ekey" "$evalue"
  fi
}

# json_obj <pair> [pair...]
# Joins already-built "key":value pairs (e.g. from json_kv) into a JSON
# object. Prints {} when called with no arguments.
json_obj() {
  local joined=""
  local first=1
  local part
  for part in "$@"; do
    if [[ "$first" -eq 1 ]]; then
      joined="$part"
      first=0
    else
      joined="${joined},${part}"
    fi
  done
  printf '{%s}' "$joined"
}

# json_array <element> [element...]
# Joins already-built JSON values (e.g. from json_obj) into a JSON array.
# Prints [] when called with no arguments.
json_array() {
  local joined=""
  local first=1
  local part
  for part in "$@"; do
    if [[ "$first" -eq 1 ]]; then
      joined="$part"
      first=0
    else
      joined="${joined},${part}"
    fi
  done
  printf '[%s]' "$joined"
}
