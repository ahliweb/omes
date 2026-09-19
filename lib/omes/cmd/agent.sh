#!/usr/bin/env bash
# omes-help: manage the native OMES + Hermes + systemd agent deployment lifecycle
# shellcheck shell=bash
# lib/omes/cmd/agent.sh - thin wrapper around lib/omes/py/agent/cli.py
# (issue #87). All lifecycle logic (manifest validation, plan rendering,
# state machine, health aggregation) lives in Python (ADR-0012); this
# file only locates python3, forwards arguments/global flags, and
# implements `logs` (a plain journalctl passthrough with no logic of its
# own, so it does not need a Python round-trip).
#
# `omes agent ...` is an OPTIONAL extension command (see
# lib/omes/cmd/README.md, docs/cli.md section 4.12). It is never
# referenced by any installer profile. See docs/agent-deployment.md.

if [[ -n "${OMES_CMD_AGENT_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317
  return 0 2>/dev/null || exit 0
fi
OMES_CMD_AGENT_SH_LOADED=1

_agent_py() {
  local py_root="${OMES_ROOT}/lib/omes/py"
  PYTHONPATH="${py_root}${PYTHONPATH:+:${PYTHONPATH}}" \
    python3 -m agent.cli "$@"
}

# _agent_manifest_service_mode <name>
# Prints spec.serviceMode from the agent's manifest without a full
# Python round-trip, so `logs` can pick the right `journalctl` scope even
# though it never calls agent.cli itself. Falls back to "user" if the
# manifest cannot be read (agent.cli's own subcommands are the
# authoritative validators; this is best-effort routing only).
_agent_manifest_service_mode() {
  local name="$1"
  local config_dir
  if [[ -n "${OMES_CONFIG_DIR:-}" ]]; then
    config_dir="$OMES_CONFIG_DIR"
  elif omes_is_root; then
    config_dir="/etc/omes"
  else
    config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/omes"
  fi
  local manifest="${config_dir}/agents/${name}.json"
  [[ -r "$manifest" ]] || { printf 'user\n'; return 0; }
  python3 -c '
import json, sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        data = json.load(fh)
    print(data.get("spec", {}).get("serviceMode", "user"))
except Exception:
    print("user")
' "$manifest" 2>/dev/null || printf 'user\n'
}

cmd_agent() {
  if ! command -v python3 >/dev/null 2>&1; then
    printf '[omes] ERROR: python3 is required for "omes agent" but was not found\n' >&2
    return 1
  fi

  if [[ "$#" -eq 0 ]]; then
    printf '[omes] usage: omes agent <list|check|plan|apply|status|health|restart|logs|rollback|remove> [name] [args...]\n' >&2
    printf '[omes]   see docs/agent-deployment.md\n' >&2
    return 2
  fi

  local subcommand="$1"
  shift

  local -a extra_args=()
  if [[ "${OMES_JSON:-0}" == "1" ]]; then
    extra_args+=(--json)
  fi

  case "$subcommand" in
    logs)
      local name="${1:-}"
      if [[ -z "$name" ]]; then
        printf '[omes] usage: omes agent logs <name>\n' >&2
        return 2
      fi
      shift
      if ! command -v journalctl >/dev/null 2>&1; then
        printf '[omes] ERROR: journalctl not found\n' >&2
        return 1
      fi
      local unit="omes-agent-${name}.service"
      local mode
      mode="$(_agent_manifest_service_mode "$name")"
      if [[ "$mode" == "user" ]]; then
        journalctl --user -u "$unit" "$@"
      else
        journalctl -u "$unit" "$@"
      fi
      return $?
      ;;
    apply)
      if omes_dry_run; then
        extra_args+=(--dry-run)
      fi
      if omes_noninteractive; then
        extra_args+=(--yes)
      fi
      _agent_py apply "${extra_args[@]}" "$@"
      return $?
      ;;
    rollback | remove)
      if omes_noninteractive; then
        extra_args+=(--yes)
      fi
      _agent_py "$subcommand" "${extra_args[@]}" "$@"
      return $?
      ;;
    list | check | plan | status | health | restart)
      _agent_py "$subcommand" "${extra_args[@]}" "$@"
      return $?
      ;;
    *)
      printf '[omes] unknown "omes agent" subcommand: %s\n' "$subcommand" >&2
      printf '[omes] usage: omes agent <list|check|plan|apply|status|health|restart|logs|rollback|remove> [name] [args...]\n' >&2
      return 2
      ;;
  esac
}
