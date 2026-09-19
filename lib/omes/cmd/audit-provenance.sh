#!/usr/bin/env bash
# shellcheck shell=bash
# omes-help: supply-chain provenance audit (see 'omes audit provenance --help'; normally reached via 'omes audit provenance')
#
# lib/omes/cmd/audit-provenance.sh - `omes audit provenance` subcommand
# (issue #84), sourced automatically by lib/omes/cmd/audit.sh's
# _audit_source_siblings.
#
# Also the home of `provenance_record_component`, the documented bash
# helper OTHER MODULES may call to register a supply-chain provenance
# record (see docs/provenance.md) - modules/hermes/module.sh sources
# this file directly (not through the audit dispatcher) to reuse it.
#
# Read-only from the audit side: `omes audit provenance` never executes
# a discovered file, never reads a credential, and never mutates host
# state. `provenance_record_component` only ever WRITES to
# <state-dir>/provenance/<component>.json (mode 0600) - it never
# touches anything outside that one file.
#
# bin/omes's extension-command mechanism (lib/omes/cmd/README.md) lists
# every lib/omes/cmd/*.sh file as a top-level command and requires a
# `# omes-help:` header (its absence makes `omes help` itself die - see
# bin/omes's `_omes_print_extension_commands`), so this file defines
# `cmd_audit-provenance` as a thin alias for the same subcommand
# `omes audit provenance` already reaches through lib/omes/cmd/audit.sh -
# both spellings do exactly the same thing.

if [[ -n "${OMES_CMD_AUDIT_PROVENANCE_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317  # unreachable only when sourced; the `exit`
  # branch is the (correct) fallback for accidental direct execution.
  return 0 2>/dev/null || exit 0
fi
OMES_CMD_AUDIT_PROVENANCE_SH_LOADED=1

# _provenance_record_py
_provenance_record_py() {
  printf '%s/lib/omes/py/provenance/record.py\n' "$OMES_ROOT"
}

# _provenance_audit_py
_provenance_audit_py() {
  printf '%s/lib/omes/py/provenance/audit.py\n' "$OMES_ROOT"
}

# provenance_record_component <component> <profile> <payload-json>
#
# Documented helper for any module that installs a component: writes a
# provenance record via lib/omes/py/provenance/record.py. <payload-json>
# is the JSON object described in record.py's module docstring
# (installer_source_url, resolved_version, install_time, checksum
# {algorithm,expected,actual,status}, package_manager) - build it with
# lib/omes/json.sh's json_obj/json_kv, never by hand-interpolating a
# secret into a string. Honors OMES_DRY_RUN (skips the write, logs what
# would happen) and never fails the caller's apply step: a recording
# failure is logged as a WARN and swallowed, since a provenance record
# is diagnostic, not a precondition for the install itself.
provenance_record_component() {
  local component="$1"
  local profile="$2"
  local payload="$3"

  if omes_dry_run; then
    log_info "[dry-run] would record provenance for component=${component} profile=${profile}"
    return 0
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    log_warn "provenance: python3 not found; skipping provenance record for ${component}"
    return 0
  fi

  local script
  script="$(_provenance_record_py)"
  if [[ ! -r "$script" ]]; then
    log_warn "provenance: ${script} not found; skipping provenance record for ${component}"
    return 0
  fi

  local state_dir
  state_dir="$(omes_state_dir)"

  local out rc=0
  out="$(printf '%s' "$payload" | python3 "$script" --state-dir "$state_dir" --component "$component" --profile "$profile" 2>/dev/null)" || rc=$?
  if [[ "$rc" -ne 0 ]]; then
    log_warn "provenance: could not record provenance for ${component} (exit ${rc})"
    return 0
  fi
  log_info "provenance: recorded ${component} (${out})"
  return 0
}

# _audit_provenance_usage
_audit_provenance_usage() {
  cat <<'EOF'
Usage: omes audit provenance [--profile <name>] [--json]

Audits <state-dir>/provenance/*.json records written at install time
(issue #84): reports each component's checksum status, flags a
recorded checksum mismatch as FAIL (fail-closed), warns on an
unverified checksum status and on a mutable installer-source reference
(main/latest/HEAD in the URL), warns on missing required metadata, and
lists (never executes) executable files found under managed
$HERMES_HOME/{skills,plugins,mcp}/** paths for manual review.

This audit is NOT a security certification - see docs/provenance.md.

Options:
  --profile <name>   Only include provenance records recorded under this profile.
  --json             Print the full JSON report instead of a human summary.

Exit codes: 0 clean (no findings), 7 findings present.
EOF
}

# _audit_provenance_print_human <json>
_audit_provenance_print_human() {
  OMES_PROV_JSON="$1" python3 -c '
import json
import os

try:
    d = json.loads(os.environ["OMES_PROV_JSON"])
except ValueError:
    print("[omes] audit provenance: the checker did not return valid JSON")
    raise SystemExit(0)

print("[omes] audit provenance: ok=%s" % d.get("ok"))
for c in d.get("components", []):
    pkgmgr = c.get("package_manager") or {}
    pkgmgr_str = (" package_manager=%s:%s" % (pkgmgr.get("name"), pkgmgr.get("package"))) if pkgmgr else ""
    print("[omes]   component=%s version=%s checksum_status=%s%s" % (c.get("component"), c.get("resolved_version"), c.get("checksum_status"), pkgmgr_str))
for f in d.get("findings", []):
    print("[omes]   %-4s %s: %s" % (f.get("severity"), f.get("component"), f.get("detail")))
review = d.get("review", [])
if review:
    print("[omes]   review (never executed): %d executable file(s) under managed skill/plugin/MCP paths" % len(review))
    for r in review:
        print("[omes]     %s mode=%s size=%s sha256=%s" % (r.get("path"), r.get("mode"), r.get("size"), r.get("sha256")))
'
}

# _audit_dispatch_provenance [--profile <name>] [--json]
# Called by lib/omes/cmd/audit.sh's cmd_audit dispatcher for `omes audit
# provenance`.
_audit_dispatch_provenance() {
  local want_json=0
  [[ "${OMES_JSON:-0}" == "1" ]] && want_json=1
  local profile=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json)
        want_json=1
        shift
        ;;
      --profile)
        [[ $# -ge 2 ]] || omes_die "$OMES_EX_USAGE" "audit provenance: --profile requires a value"
        profile="$2"
        shift 2
        ;;
      -h | --help)
        _audit_provenance_usage
        exit "$OMES_EX_OK"
        ;;
      *)
        omes_die "$OMES_EX_USAGE" "audit provenance: unknown argument: $1"
        ;;
    esac
  done

  if ! command -v python3 >/dev/null 2>&1; then
    log_error "audit provenance: python3 not found (required for lib/omes/py/provenance/audit.py)"
    exit "$OMES_EX_PREFLIGHT"
  fi

  local script
  script="$(_provenance_audit_py)"
  if [[ ! -r "$script" ]]; then
    log_error "audit provenance: ${script} not found"
    exit "$OMES_EX_PREFLIGHT"
  fi

  local state_dir hermes_home
  state_dir="$(omes_state_dir)"
  if declare -F runtime_home >/dev/null 2>&1; then
    hermes_home="$(runtime_home hermes 2>/dev/null || true)"
  else
    hermes_home="${OMES_HERMES_HOME:-${HOME:-}/.hermes}"
  fi

  local -a py_args=(--state-dir "$state_dir" --hermes-home "$hermes_home")
  [[ -n "$profile" ]] && py_args+=(--profile "$profile")

  local output rc=0
  output="$(python3 "$script" "${py_args[@]}" 2>/dev/null)" || rc=$?

  if [[ -z "$output" ]]; then
    log_error "audit provenance: the checker produced no output (exit ${rc})"
    exit "$OMES_EX_PREFLIGHT"
  fi

  if [[ "$want_json" -eq 1 ]]; then
    printf '%s\n' "$output"
  else
    _audit_provenance_print_human "$output"
  fi

  exit "$rc"
}

# cmd_audit-provenance
# Alias entry point so this file also works as a standalone top-level
# extension command (`omes audit-provenance ...`), since bin/omes's
# generic extension-command mechanism reaches every lib/omes/cmd/*.sh
# file that way regardless of whether it is also a sibling of another
# dispatcher. Identical behavior to `omes audit provenance ...`.
cmd_audit-provenance() {
  _audit_dispatch_provenance "$@"
}
