#!/usr/bin/env bash
# omes-help: submit and run idempotent, audited control jobs (issue #90)
# shellcheck shell=bash
# lib/omes/cmd/job.sh - thin wrapper around lib/omes/py/jobs/cli.py.
#
# `omes job ...` is an extension command (see lib/omes/cmd/README.md,
# docs/cli.md section 4.12). All logic lives in Python (ADR-0012); this
# file only locates python3, forwards arguments, and maps the global
# --json flag when the caller put it before the subcommand. See
# docs/jobs.md for the design.

if [[ -n "${OMES_CMD_JOB_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317
  return 0 2>/dev/null || exit 0
fi
OMES_CMD_JOB_SH_LOADED=1

cmd_job() {
  if ! command -v python3 >/dev/null 2>&1; then
    printf '[omes] ERROR: python3 is required for "omes job" but was not found\n' >&2
    return 1
  fi

  local py_root="${OMES_ROOT}/lib/omes/py"
  local -a extra_args=()

  if [[ "${OMES_JSON:-0}" == "1" ]]; then
    extra_args+=(--json)
  fi

  if [[ "$#" -eq 0 ]]; then
    printf '[omes] usage: omes job <submit|approve|run|status|list|cancel|expire> [args...]\n' >&2
    return 2
  fi

  local subcommand="$1"
  shift

  OMES_ROOT="${OMES_ROOT}" \
    PYTHONPATH="${py_root}${PYTHONPATH:+:${PYTHONPATH}}" \
    python3 -m jobs.cli "$subcommand" "${extra_args[@]}" "$@"
}
