#!/usr/bin/env bash
# shellcheck shell=bash
# lib/omes/versions.sh - runtime version/compatibility evidence collection
# (issue #83).
#
# This file gathers the host facts bash already has (OMES version, git
# ref, OS/arch/kernel via lib/omes/detect.sh, Hermes home, gateway mode
# via lib/omes/state.sh) and hands them to
# lib/omes/py/provenance/versions.py, which probes every optional
# runtime (Hermes, node, browser, ffmpeg, docker client, Ollama, python3,
# and a documented allowlist of non-secret Hermes config keys) and
# returns a single JSON evidence report.
#
# This file NEVER reads $HERMES_HOME/.env and never mutates host state;
# it is read-only, used by `omes health versions` (lib/omes/cmd/health.sh)
# and modules/hermes/module.sh's module_doctor.
#
# Meant to be sourced after lib/omes/core.sh, lib/omes/log.sh,
# lib/omes/json.sh, lib/omes/detect.sh and lib/omes/state.sh.

if [[ -n "${OMES_VERSIONS_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317  # unreachable only when sourced; the `exit`
  # branch is the (correct) fallback for accidental direct execution.
  return 0 2>/dev/null || exit 0
fi
OMES_VERSIONS_SH_LOADED=1

# This file is documented as "meant to be sourced after
# lib/omes/{core,log,json,detect,state}.sh", which `bin/omes` always
# does before dispatching to any module/command. A caller that only
# sources a narrower subset (e.g. a unit test isolating
# modules/hermes/module.sh with just core/log/state/backup/module - see
# tests/unit/ollama-health.bats) would otherwise hit "command not
# found" for json_obj/detect_os the first time a versions_* function
# actually runs. Each of these libraries already guards against being
# sourced twice, so pulling them in here defensively is a safe no-op
# when the caller already loaded them.
for _omes_versions_dep in json detect state; do
  # shellcheck disable=SC1090
  source "${OMES_ROOT}/lib/omes/${_omes_versions_dep}.sh"
done
unset _omes_versions_dep

# _versions_py_script
# Prints the path to lib/omes/py/provenance/versions.py.
_versions_py_script() {
  printf '%s/lib/omes/py/provenance/versions.py\n' "$OMES_ROOT"
}

# _versions_git_ref
# Prints the short git ref of the OMES checkout at $OMES_ROOT, or nothing
# if this is not a git checkout (or git is unavailable).
_versions_git_ref() {
  if ! command -v git >/dev/null 2>&1; then
    return 1
  fi
  git -C "$OMES_ROOT" rev-parse --short HEAD 2>/dev/null
}

# _versions_gateway_mode
# Prints the applied Hermes gateway mode ("user" or "system") from state,
# preferring the user-scope module (the default path); prints nothing if
# neither module has recorded a mode.
_versions_gateway_mode() {
  state_get "module.hermes-gateway.mode" 2>/dev/null && return 0
  state_get "module.hermes-gateway-system.mode" 2>/dev/null && return 0
  return 1
}

# versions_host_facts_json
# Prints the JSON object versions.py expects on stdin: OMES
# version/git_ref, OS/arch/kernel facts (from lib/omes/detect.sh, already
# sourced by every caller), the resolved Hermes home, and the applied
# gateway mode (from lib/omes/state.sh), if any.
versions_host_facts_json() {
  detect_os || true
  detect_arch || true

  local kernel
  kernel="$(uname -r 2>/dev/null || true)"

  local git_ref
  git_ref="$(_versions_git_ref || true)"

  local gateway_mode
  gateway_mode="$(_versions_gateway_mode || true)"

  local hermes_home
  if declare -F runtime_home >/dev/null 2>&1; then
    hermes_home="$(runtime_home hermes 2>/dev/null || true)"
  else
    hermes_home="${OMES_HERMES_HOME:-${HOME:-}/.hermes}"
  fi

  local omes_obj os_obj
  omes_obj="$(json_obj \
    "$(json_kv version "${OMES_VERSION:-}")" \
    "$(json_kv git_ref "${git_ref}")")"
  os_obj="$(json_obj \
    "$(json_kv id "${OMES_OS_ID:-}")" \
    "$(json_kv version_id "${OMES_OS_VERSION_ID:-}")" \
    "$(json_kv codename "${OMES_OS_CODENAME:-}")" \
    "$(json_kv pretty "${OMES_OS_PRETTY:-}")" \
    "$(json_kv kernel "${kernel}")")"

  json_obj \
    "$(json_kv omes "$omes_obj" --raw)" \
    "$(json_kv os "$os_obj" --raw)" \
    "$(json_kv arch "${OMES_ARCH:-}")" \
    "$(json_kv hermes_home "${hermes_home}")" \
    "$(json_kv gateway_mode "${gateway_mode}")"
}

# versions_collect_json
# Runs versions.py with the host facts on stdin and prints its JSON
# evidence report on stdout. Returns the script's exit code (0 on a
# normal run per versions.py's contract; 1 only on an internal error such
# as invalid input JSON, which should not happen here).
versions_collect_json() {
  local script
  script="$(_versions_py_script)"
  if [[ ! -r "$script" ]]; then
    log_error "versions: ${script} not found"
    return 1
  fi
  versions_host_facts_json | python3 "$script"
}
