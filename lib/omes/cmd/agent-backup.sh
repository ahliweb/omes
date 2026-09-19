#!/usr/bin/env bash
# omes-help: opt-in backup/restore for Hermes data classes (config, skills, memory, sessions, runtime-state, secrets)
# shellcheck shell=bash
# lib/omes/cmd/agent-backup.sh - thin wrapper around
# lib/omes/py/hermesbackup/cli.py.
#
# `omes agent-backup ...` is an OPTIONAL extension command (see
# lib/omes/cmd/README.md, docs/cli.md section 4.12). Issue #82. All logic
# lives in Python (ADR-0012); this file only locates python3, forwards
# arguments, and maps the global --json/--yes flags through when the
# caller put them before the subcommand.
#
# NOTE (issue #87): this command is expected to be folded under
# `omes agent backup ...` by the later agent-runtime abstraction work;
# the implementation stays in lib/omes/py/hermesbackup so it can be
# re-wired under a different lib/omes/cmd/*.sh entry point without
# touching the Python package itself.

if [[ -n "${OMES_CMD_AGENT_BACKUP_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317
  return 0 2>/dev/null || exit 0
fi
OMES_CMD_AGENT_BACKUP_SH_LOADED=1

cmd_agent-backup() {
  if ! command -v python3 >/dev/null 2>&1; then
    printf '[omes] ERROR: python3 is required for "omes agent-backup" but was not found\n' >&2
    return 1
  fi

  local py_root="${OMES_ROOT}/lib/omes/py"
  local -a extra_args=()

  if [[ "${OMES_JSON:-0}" == "1" ]]; then
    extra_args+=(--json)
  fi
  if omes_noninteractive; then
    extra_args+=(--yes)
  fi

  if [[ "$#" -eq 0 ]]; then
    printf '[omes] usage: omes agent-backup <create|list|verify|restore> [args...]\n' >&2
    printf '[omes]   see docs/hermes-backup.md\n' >&2
    return 2
  fi

  local subcommand="$1"
  shift

  PYTHONPATH="${py_root}${PYTHONPATH:+:${PYTHONPATH}}" \
    python3 -m hermesbackup.cli "$subcommand" "${extra_args[@]}" "$@"
}
