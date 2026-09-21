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
  [[ -r "$manifest" ]] || {
    printf 'user\n'; return 0
  }
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

# _agent_manifest_backend <name>
# Prints spec.backend ("systemd" or "compose") from the agent's manifest,
# so `logs` can route to journalctl or `docker compose logs` without a
# full agent.cli round trip. Best-effort only, same fallback contract as
# _agent_manifest_service_mode above (defaults to "systemd").
_agent_manifest_backend() {
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
  [[ -r "$manifest" ]] || {
    printf 'systemd\n'; return 0
  }
  python3 -c '
import json, sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        data = json.load(fh)
    print(data.get("spec", {}).get("backend", "systemd"))
except Exception:
    print("systemd")
' "$manifest" 2>/dev/null || printf 'systemd\n'
}

# _agent_logs_compose <name> [--tail N] [--follow] [-- extra docker-compose-logs args]
# `omes agent logs <name>` for `backend: "compose"` (issue #96 follow-up):
# `docker compose -p <project> -f <file> logs --no-color --tail <n>`.
# `--follow` is accepted but never the default (a bounded, operator-
# requested opt-in only, per AGENTS.md "no destructive/unbounded defaults"
# spirit) - a plain `omes agent logs <name>` always returns rather than
# streaming forever.
_agent_logs_compose() {
  local name="$1"
  shift

  if ! command -v docker >/dev/null 2>&1; then
    printf '[omes] ERROR: docker not found\n' >&2
    return 1
  fi

  local tail="200"
  local follow=0
  local -a extra_args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tail)
        [[ $# -ge 2 ]] || {
          printf '[omes] --tail requires an argument\n' >&2; return 2
        }
        tail="$2"
        shift 2
        ;;
      --follow)
        follow=1
        shift
        ;;
      *)
        extra_args+=("$1")
        shift
        ;;
    esac
  done

  local plan_json
  plan_json="$(_agent_py plan "$name" --json 2>/dev/null)" || {
    printf '[omes] ERROR: could not compute the plan for agent "%s" (see: omes agent check %s)\n' "$name" "$name" >&2
    return 1
  }

  local project compose_file
  project="$(printf '%s' "$plan_json" | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin)["project"])
except Exception:
    pass
' 2>/dev/null)"
  compose_file="$(printf '%s' "$plan_json" | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin)["composeFile"])
except Exception:
    pass
' 2>/dev/null)"

  if [[ -z "$project" || -z "$compose_file" ]]; then
    printf '[omes] ERROR: could not resolve the compose project/file for agent "%s"\n' "$name" >&2
    return 1
  fi

  local -a docker_args=(compose -p "$project" -f "$compose_file" logs --no-color --tail "$tail")
  if [[ "$follow" -eq 1 ]]; then
    docker_args+=(--follow)
  fi
  docker "${docker_args[@]}" "${extra_args[@]}"
}

cmd_agent() {
  if ! command -v python3 >/dev/null 2>&1; then
    printf '[omes] ERROR: python3 is required for "omes agent" but was not found\n' >&2
    return 1
  fi

  if [[ "$#" -eq 0 ]]; then
    printf '[omes] usage: omes agent <list|doctor|check|plan|apply|status|health|restart|logs|rollback|remove> [name] [args...]\n' >&2
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
        printf '[omes] usage: omes agent logs <name> [--tail N] [--follow]\n' >&2
        return 2
      fi
      shift
      local backend
      backend="$(_agent_manifest_backend "$name")"
      if [[ "$backend" == "compose" ]]; then
        _agent_logs_compose "$name" "$@"
        return $?
      fi
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
    list | doctor | check | plan | status | health | restart | migrate | orchestration)
      _agent_py "$subcommand" "${extra_args[@]}" "$@"
      return $?
      ;;
    *)
      printf '[omes] unknown "omes agent" subcommand: %s\n' "$subcommand" >&2
      printf '[omes] usage: omes agent <list|doctor|check|plan|apply|status|health|restart|logs|rollback|remove|orchestration> [name] [args...]\n' >&2
      return 2
      ;;
  esac
}
