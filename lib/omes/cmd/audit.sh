# shellcheck shell=bash
# omes-help: security audits (exposure; see lib/omes/cmd/audit-*.sh for more)
#
# lib/omes/cmd/audit.sh - `omes audit <subcommand>` dispatcher.
#
# This file only defines cmd_audit's own dispatch scaffolding and the
# `exposure` subcommand (issue #80). A future subcommand (e.g.
# `provenance`, issue #84) is expected to live in a SIBLING file
# lib/omes/cmd/audit-<name>.sh defining `_audit_dispatch_<name>` (see
# _audit_source_siblings below) rather than growing this file - this
# keeps each audit subcommand's implementation independently reviewable
# and lets a later change add one without touching this dispatcher.
#
# Sourced by bin/omes's extension-command mechanism (lib/omes/cmd/README.md)
# after every lib/omes/*.sh library and with global flags already parsed.
# Read-only: no audit subcommand mutates host state, opens a port, alters
# firewall rules, or reads credential values.

# _audit_source_siblings
# Sources every lib/omes/cmd/audit-*.sh file alongside this one, if any
# exist, so a subcommand implemented in its own file (e.g. a future
# lib/omes/cmd/audit-provenance.sh for issue #84) is picked up
# automatically without editing this dispatcher. Each such file is
# expected to define its own `_audit_dispatch_<name>` function; this
# dispatcher calls it by convention (see cmd_audit below) rather than
# hardcoding a list of known subcommands.
_audit_source_siblings() {
  local dir="${OMES_ROOT}/lib/omes/cmd"
  local f
  shopt -s nullglob
  for f in "${dir}"/audit-*.sh; do
    # shellcheck disable=SC1090  # sibling extension files are discovered at runtime
    source "$f"
  done
  shopt -u nullglob
}
_audit_source_siblings

# _audit_usage
_audit_usage() {
  cat <<'EOF'
Usage: omes audit <subcommand> [options]

Subcommands:
  exposure    Detect unsafe listener exposure (Hermes gateway,
              browser-control/CDP, MCP servers, Ollama): loopback vs
              LAN vs wildcard binds, and firewall (ufw) coverage
              (issue #80). Options: --json

Exit codes (exposure): 0 ok, 7 findings, 4 required tool (ss) missing.
EOF
}

# _audit_py_script <name>
_audit_py_script() {
  printf '%s/lib/omes/py/health/%s\n' "$OMES_ROOT" "$1"
}

# _audit_print_exposure_human <json>
# Prints a short human-readable summary of the exposure audit's JSON
# result. Takes the JSON as an argument (env var, not argv/stdin) - see
# lib/omes/cmd/health.sh's identical pattern and the comment there
# explaining why a `python3 - <<'PY'` heredoc cannot also read a piped
# value from its own stdin.
_audit_print_exposure_human() {
  OMES_AUDIT_JSON="$1" python3 -c '
import json
import os

try:
    d = json.loads(os.environ["OMES_AUDIT_JSON"])
except ValueError:
    print("[omes] audit exposure: the checker did not return valid JSON")
    raise SystemExit(0)

print("[omes] audit exposure: ok=%s" % d.get("ok"))
fw = d.get("firewall", {})
print("[omes]   firewall: tool=%s available=%s active=%s" % (fw.get("tool"), fw.get("available"), fw.get("active")))
for f in d.get("findings", []):
    marker = "OK" if f.get("approved") else "FINDING"
    print("[omes]   %-7s %s/%s owner=%s bind=%s" % (marker, f.get("address"), f.get("port"), f.get("owner"), f.get("bind_class")))
    if f.get("detail"):
        print("[omes]     detail: %s" % f["detail"])
    if not f.get("approved") and f.get("remediation"):
        print("[omes]     remediation: %s" % f["remediation"])
if not d.get("findings"):
    print("[omes]   no non-loopback listeners found")
'
}

# _audit_run_exposure [--json]
_audit_run_exposure() {
  local want_json=0
  [[ "${OMES_JSON:-0}" == "1" ]] && want_json=1

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json)
        want_json=1
        shift
        ;;
      -h | --help)
        _audit_usage
        exit "$OMES_EX_OK"
        ;;
      *)
        omes_die "$OMES_EX_USAGE" "audit exposure: unknown argument: $1"
        ;;
    esac
  done

  if ! command -v python3 >/dev/null 2>&1; then
    log_error "audit exposure: python3 not found (required for lib/omes/py/health/exposure.py)"
    exit "$OMES_EX_PREFLIGHT"
  fi

  local script
  script="$(_audit_py_script exposure.py)"
  if [[ ! -r "$script" ]]; then
    log_error "audit exposure: ${script} not found"
    exit "$OMES_EX_PREFLIGHT"
  fi

  local output rc=0
  output="$(python3 "$script" 2>/dev/null)" || rc=$?

  if [[ -z "$output" ]]; then
    log_error "audit exposure: the checker produced no output (exit ${rc})"
    exit "$OMES_EX_PREFLIGHT"
  fi

  if [[ "$want_json" -eq 1 ]]; then
    printf '%s\n' "$output"
  else
    _audit_print_exposure_human "$output"
  fi

  exit "$rc"
}

cmd_audit() {
  local sub="${1:-}"
  case "$sub" in
    exposure)
      shift
      _audit_run_exposure "$@"
      ;;
    "" | -h | --help)
      _audit_usage
      ;;
    *)
      if declare -F "_audit_dispatch_${sub}" >/dev/null 2>&1; then
        shift
        "_audit_dispatch_${sub}" "$@"
      else
        log_error "audit: unknown subcommand '${sub}' (expected: exposure)"
        exit "$OMES_EX_USAGE"
      fi
      ;;
  esac
}
