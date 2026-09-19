#!/usr/bin/env bash
# omes-help: manage the optional content distribution workflow (inbox, jobs, reports)
# shellcheck shell=bash
# lib/omes/cmd/content.sh - thin wrapper around lib/omes/py/content/cli.py.
#
# `omes content ...` is an OPTIONAL extension command (see
# lib/omes/cmd/README.md, docs/cli.md section 4.12). It is never referenced
# by any profile or installer path (docs/content-distribution.md section 9).
# All logic lives in Python (ADR-0012); this file only locates python3,
# forwards arguments, and maps the global --json flag when the caller put
# it before the subcommand.

if [[ -n "${OMES_CMD_CONTENT_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317
  return 0 2>/dev/null || exit 0
fi
OMES_CMD_CONTENT_SH_LOADED=1

cmd_content() {
  if ! command -v python3 >/dev/null 2>&1; then
    printf '[omes] ERROR: python3 is required for "omes content" but was not found\n' >&2
    return 1
  fi

  local py_root="${OMES_ROOT}/lib/omes/py"
  local -a extra_args=()

  # If bin/omes already parsed a global --json before the subcommand, pass
  # it through explicitly so `cli.py` (which owns its own argument parsing
  # per subcommand) still sees it.
  if [[ "${OMES_JSON:-0}" == "1" ]]; then
    extra_args+=(--json)
  fi

  if [[ "$#" -eq 0 ]]; then
    printf '[omes] usage: omes content <scan|rescan|list|resume|reconcile|retry|cancel|approve|reject|report|export|prune> [args...]\n' >&2
    return 2
  fi

  local subcommand="$1"
  shift

  PYTHONPATH="${py_root}${PYTHONPATH:+:${PYTHONPATH}}" \
    python3 -m content.cli "$subcommand" "${extra_args[@]}" "$@"
}
