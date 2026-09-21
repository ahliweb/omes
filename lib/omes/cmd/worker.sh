#!/usr/bin/env bash
# omes-help: run or manage the Control Center pull-worker client (ADR-0027, issue #192)
# shellcheck shell=bash
# lib/omes/cmd/worker.sh - thin wrapper around lib/omes/py/jobs/worker.py.

if [[ -n "${OMES_CMD_WORKER_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317
  return 0 2>/dev/null || exit 0
fi
OMES_CMD_WORKER_SH_LOADED=1

cmd_worker() {
  if ! command -v python3 >/dev/null 2>&1; then
    printf '[omes] ERROR: python3 is required for "omes worker" but was not found\n' >&2
    return 1
  fi

  local py_root="${OMES_ROOT}/lib/omes/py"

  if [[ "$#" -eq 0 ]]; then
    printf '[omes] usage: omes worker <enroll|poll|heartbeat|status> [args...]\n' >&2
    return 2
  fi

  local subcommand="$1"
  shift

  OMES_ROOT="${OMES_ROOT}" \
    PYTHONPATH="${py_root}${PYTHONPATH:+:${PYTHONPATH}}" \
    python3 -m jobs.worker "$subcommand" "$@"
}
