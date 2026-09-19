#!/usr/bin/env bash
# shellcheck shell=bash
# modules/hermes-backup/module.sh - opt-in Hermes data-class backup
# doctor integration (issue #82).
#
# This module does NOT install anything and does NOT run automatically
# in any profile (MODULE_PROFILES is intentionally empty, matching
# modules/hermes-gateway-system's opt-in-only pattern) - the actual
# backup/restore functionality is the `omes agent-backup` extension
# command (lib/omes/cmd/agent-backup.sh -> lib/omes/py/hermesbackup/).
# This module exists solely so `omes doctor` can report "last backup per
# class and age" as an additive module_doctor hook, without touching
# modules/hermes/module.sh (out of this issue's file scope) or requiring
# an operator to have run `omes install --module hermes-backup` first -
# module_check/apply/verify are all safe, idempotent no-ops so the module
# can be enabled (`--module hermes-backup`) purely to get its doctor
# signal without mutating anything.

# shellcheck disable=SC2034
MODULE_NAME="hermes-backup"
# shellcheck disable=SC2034
MODULE_DESCRIPTION="Opt-in Hermes data-class backup/restore doctor integration (see 'omes agent-backup')"
# shellcheck disable=SC2034
MODULE_SCOPE="user"
# shellcheck disable=SC2034
MODULE_REQUIRES=()
# Deliberately empty: not part of any default profile - see the file
# header comment above.
# shellcheck disable=SC2034
MODULE_PROFILES=()

# _hbk_hermes_home
_hbk_hermes_home() {
  printf '%s\n' "${OMES_HERMES_HOME:-${HOME}/.hermes}"
}

module_check() {
  if ! command -v python3 >/dev/null 2>&1; then
    log_error "hermes-backup: python3 not found (required by 'omes agent-backup')"
    return 1
  fi
  local script="${OMES_ROOT}/lib/omes/py/hermesbackup/cli.py"
  if [[ ! -r "$script" ]]; then
    log_error "hermes-backup: checker not found at ${script}"
    return 1
  fi
  return 0
}

module_apply() {
  if omes_dry_run; then
    log_info "[dry-run] hermes-backup: nothing to apply (doctor-only module; see 'omes agent-backup --help')"
    return 0
  fi
  log_info "hermes-backup: nothing to apply - use 'omes agent-backup create' to make a backup (see docs/hermes-backup.md)"
  return 0
}

module_verify() {
  return 0
}

module_rollback() {
  log_info "hermes-backup: nothing to roll back - this module never writes managed files"
  return 0
}

# module_doctor
# Reports the most recent backup session per class and its age, via
# lib/omes/py/hermesbackup. Advisory only - never fails module_verify;
# reports a WARN-worthy line (non-zero) only when no backups exist at all
# or the checker itself errors, which `omes doctor` surfaces as a WARN
# per bin/omes's module_doctor contract.
module_doctor() {
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'agent-backup: python3 not found; cannot report backup status\n'
    return 1
  fi

  local py_root="${OMES_ROOT}/lib/omes/py"
  local out rc=0
  out="$(PYTHONPATH="${py_root}${PYTHONPATH:+:${PYTHONPATH}}" python3 -m hermesbackup.cli list --json 2>/dev/null)" || rc=$?
  if [[ "$rc" -ne 0 ]] || [[ -z "$out" ]]; then
    printf 'agent-backup: could not list backups (exit %s)\n' "$rc"
    return 1
  fi

  local summary
  summary="$(printf '%s' "$out" | python3 -c '
import json, sys, datetime
try:
    d = json.load(sys.stdin)
except ValueError:
    print("could not parse backup list")
    sys.exit(0)
backups = d.get("backups", [])
if not backups:
    print("no backups yet - see docs/hermes-backup.md (omes agent-backup create)")
    sys.exit(0)
by_class = {}
for b in backups:
    ts = b.get("timestamp", "")
    for c in b.get("classes", []):
        if c not in by_class or ts > by_class[c]:
            by_class[c] = ts
now = datetime.datetime.now(datetime.timezone.utc)
parts = []
for c in sorted(by_class):
    ts = by_class[c]
    try:
        dt = datetime.datetime.strptime(ts.split("-")[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
        age_h = (now - dt).total_seconds() / 3600.0
        parts.append(f"{c}={ts} ({age_h:.1f}h ago)")
    except ValueError:
        parts.append(f"{c}={ts}")
print("; ".join(parts))
' 2>/dev/null || printf 'could not summarize backup list')"

  printf 'agent-backup: %s\n' "$summary"
  return 0
}
