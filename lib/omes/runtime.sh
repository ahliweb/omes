#!/usr/bin/env bash
# shellcheck shell=bash
# lib/omes/runtime.sh - runtime-neutral agent-runtime contract layer.
#
# OMES supports exactly one agent runtime today: Hermes Agent. This file
# defines the small, explicit boundary between "OMES needs a runtime fact"
# and "that fact happens to come from Hermes" (issue #85, ADR-0013). It is
# NOT a plugin system and does not install a second runtime; it exists so
# that runtime-specific knowledge (unit names, HERMES_HOME resolution,
# version/health probe entrypoints) has exactly one place to live instead
# of being re-derived ad hoc in every module and command.
#
# Meant to be sourced after lib/omes/core.sh, lib/omes/log.sh and
# lib/omes/json.sh.

if [[ -n "${OMES_RUNTIME_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317  # unreachable only when sourced; the `exit`
  # branch is the (correct) fallback for accidental direct execution.
  return 0 2>/dev/null || exit 0
fi
OMES_RUNTIME_SH_LOADED=1

# runtime_supported <name>
# True (0) only for "hermes" - the sole supported runtime. Every other
# name, including empty, is unsupported. See docs/adr/0013-agent-runtime-boundary.md
# for why OMES does not attempt to guess or auto-detect a runtime.
runtime_supported() {
  local name="${1:-}"
  [[ "$name" == "hermes" ]]
}

# runtime_require <name>
# Dies with exit 4 (OMES_EX_PREFLIGHT) when <name> is not a supported
# runtime; a no-op otherwise. Callers use this to fail fast and
# explicitly rather than falling through to Hermes-specific commands for
# an unrecognized runtime name.
runtime_require() {
  local name="${1:-}"
  if ! runtime_supported "$name"; then
    omes_die "$OMES_EX_PREFLIGHT" "unsupported runtime: '${name}' (only 'hermes' is supported; see docs/agent-runtime-boundary.md)"
  fi
}

# _runtime_json_str <string>
# A JSON string literal (quotes included), for building array elements
# without going through json_kv. Kept local to this file with a
# runtime-prefixed name so it never collides with a future lib/omes/json.sh
# helper of a similar shape.
_runtime_json_str() {
  printf '"%s"' "$(json_escape "${1:-}")"
}

# _runtime_describe_hermes
# Prints the JSON metadata object for the "hermes" runtime. Field names
# and shapes are the contract asserted by tests/fixtures/runtime/hermes.json
# and tests/unit/runtime.bats; changing them is a breaking change to
# every caller (lib/omes/cmd/health.sh, lib/omes/cmd/audit.sh, module
# doctor hooks) and must update the fixture in the same change.
_runtime_describe_hermes() {
  local version_command service_units backup_classes provenance_sources
  version_command="$(json_array "$(_runtime_json_str hermes)" "$(_runtime_json_str --version)")"
  service_units="$(json_obj \
    "$(json_kv user hermes-gateway)" \
    "$(json_kv system hermes-gateway)")"
  backup_classes="$(json_array \
    "$(_runtime_json_str config)" \
    "$(_runtime_json_str state)" \
    "$(_runtime_json_str secrets-ref)")"
  provenance_sources="$(json_array \
    "$(_runtime_json_str "hermes --version")" \
    "$(_runtime_json_str "hermes doctor")" \
    "$(_runtime_json_str "hermes gateway status")")"

  json_obj \
    "$(json_kv name hermes)" \
    "$(json_kv version_command "$version_command" --raw)" \
    "$(json_kv home_env_var HERMES_HOME)" \
    "$(json_kv home_default "\$HOME/.hermes")" \
    "$(json_kv service_units "$service_units" --raw)" \
    "$(json_kv health_probe "omes health agent")" \
    "$(json_kv backup_classes "$backup_classes" --raw)" \
    "$(json_kv provenance_sources "$provenance_sources" --raw)"
}

# runtime_describe <name>
# Prints the runtime's JSON metadata object on stdout. Dies (exit 4) for
# an unsupported runtime name via runtime_require.
runtime_describe() {
  local name="${1:-}"
  runtime_require "$name"
  case "$name" in
    hermes) _runtime_describe_hermes ;;
  esac
}

# runtime_home <name>
# Prints the resolved home directory for <name>'s runtime. For hermes
# this is the same resolution modules/hermes and modules/hermes-gateway*
# already use (OMES_HERMES_HOME override, else ~/.hermes) - centralized
# here so future callers (lib/omes/py/health, lib/omes/cmd/audit.sh) read
# it from one place rather than re-implementing the fallback.
runtime_home() {
  local name="${1:-}"
  runtime_require "$name"
  case "$name" in
    hermes)
      printf '%s\n' "${OMES_HERMES_HOME:-${HOME}/.hermes}"
      ;;
  esac
}

# runtime_service_unit <name> <scope>
# Prints the systemd unit name for <name>'s runtime at <scope> (user or
# system). Both scopes currently resolve to the same literal unit name
# for hermes (modules/hermes-gateway and modules/hermes-gateway-system
# both use "hermes-gateway", just under different systemctl scopes), but
# callers should read it from here rather than hardcoding the string.
runtime_service_unit() {
  local name="${1:-}" scope="${2:-}"
  runtime_require "$name"
  case "$scope" in
    user | system) ;;
    *)
      omes_die "$OMES_EX_USAGE" "runtime_service_unit: scope must be 'user' or 'system' (got '${scope}')"
      ;;
  esac
  case "$name" in
    hermes)
      printf '%s\n' "hermes-gateway"
      ;;
  esac
}
