# shellcheck shell=bash
# omes-help: layered health checks for Ollama and the Hermes agent/gateway (issues #71, #79)
#
# lib/omes/cmd/health.sh - `omes health [agent|gateway|ollama]` dispatcher.
#
# Sourced by bin/omes's extension-command mechanism (lib/omes/cmd/README.md)
# after every lib/omes/*.sh library and with global flags already parsed.
# Read-only: this command never mutates host state and honors --dry-run
# trivially (there is nothing to dry-run for a health probe).

# shellcheck source=../runtime.sh
source "${OMES_ROOT}/lib/omes/runtime.sh"

# _health_py_script <name>
# Prints the path to a lib/omes/py/health/<name> entry point.
_health_py_script() {
  printf '%s/lib/omes/py/health/%s\n' "$OMES_ROOT" "$1"
}

# _health_usage
_health_usage() {
  cat <<'EOF'
Usage: omes health [agent|gateway|ollama|versions] [options]

Targets:
  agent     (default) Layered host/runtime/gateway/provider/channel
            readiness model for the Hermes deployment (issue #79).
  gateway   Same model, scoped to the gateway/provider/channel layers
            (skips host/runtime).
            Options for agent|gateway: --json  --mode <user|system>
  ollama    Layered service/model/capability health checks for the
            optional Ollama local AI runtime (issue #71).
            Options: --json  --profile <name>  --model <id>
  versions  Runtime version/compatibility evidence: OMES, OS/arch/kernel,
            Hermes, gateway mode, python3, node, browser, ffmpeg, docker
            (client only), Ollama, and non-secret Hermes config keys
            (issue #83; see docs/compatibility-evidence.md). Options: --json

Environment (see docs/configuration.md, docs/ollama.md, docs/hermes-integration.md):
  OMES_HEALTH_TIMEOUT           per-probe timeout in seconds (default 10)
  OMES_GATEWAY_MODE             agent|gateway target's systemd scope: user (default) or system
  OMES_HERMES_GATEWAY_HEALTH_URL       optional loopback-only gateway API health endpoint
  OMES_HERMES_GATEWAY_HEALTH_TOKEN_FILE  file path (never a value) holding its bearer token
  OMES_HEALTH_DISK_MIN_MB / OMES_HEALTH_MEM_MIN_MB  host-layer thresholds (default 512 / 256)
  OLLAMA_HOST                   endpoint (default 127.0.0.1:11434)
  OMES_OLLAMA_MODEL             model id to check (or --model)
  OMES_OLLAMA_PROFILE           capability profile: text|structured|tools|embeddings|vision|full
  OMES_OLLAMA_PROFILE_FILE      path to a JSON profile file (overrides OMES_OLLAMA_PROFILE)
  OMES_OLLAMA_ALLOW_REMOTE=1    explicitly accept a non-loopback endpoint
  OMES_OLLAMA_EXPECT_PLACEMENT  expected runtime placement: gpu|cpu|any (default any)
  OMES_OLLAMA_LOAD_TIMEOUT      bounded model-load timeout in seconds (default 30)

Exit codes: agent/gateway: 0 ready, 7 not ready. ollama: 0 ready, 7 not ready, 4 service missing.
EOF
}

# _health_host_facts_json
# Prints a small JSON object with the host facts hermes.py's host layer
# needs (systemd presence, free disk, total memory) - computed from
# lib/omes/detect.sh, which this command already has sourced (it is
# loaded by bin/omes before any extension command runs), so the Python
# layer does not re-implement host detection (ADR-0012: Python for
# workflow-engine logic, bash/existing libs for host facts).
_health_host_facts_json() {
  local systemd_present="false"
  command -v systemctl >/dev/null 2>&1 && systemd_present="true"
  local disk_mb mem_mb
  disk_mb="$(detect_disk_free_mb "${HOME:-/}" 2>/dev/null || printf '0')"
  mem_mb="$(detect_mem_mb 2>/dev/null || printf '0')"
  json_obj \
    "$(json_kv systemd_present "$systemd_present" --raw)" \
    "$(json_kv disk_free_mb "$disk_mb" --raw)" \
    "$(json_kv mem_mb "$mem_mb" --raw)"
}

# _health_print_hermes_human <json>
# Prints a short human-readable summary of <json> (the agent/gateway
# health checker's output), including each layer's "proves"/remediation
# text so `omes health agent` is self-explanatory without --json. Takes
# the JSON as an argument (via an env var, not argv) rather than stdin -
# see _health_print_ollama_human's comment for why a heredoc script
# cannot also read the value from its own stdin.
_health_print_hermes_human() {
  OMES_HEALTH_JSON="$1" python3 -c '
import json
import os

try:
    d = json.loads(os.environ["OMES_HEALTH_JSON"])
except ValueError:
    print("[omes] health: the checker did not return valid JSON")
    raise SystemExit(0)

print("[omes] health: ready=%s connected=%s" % (d.get("ready"), d.get("connected")))
for name, layer in d.get("layers", {}).items():
    print("[omes]   %-10s %s" % (name + ":", layer.get("status", "unknown")))
    if layer.get("detail"):
        print("[omes]     detail: %s" % layer["detail"])
    if layer.get("status") == "fail" and layer.get("remediation"):
        print("[omes]     remediation: %s" % layer["remediation"])
'
}

# _health_run_hermes <target> [--json] [--mode <user|system>]
_health_run_hermes() {
  local target="$1"
  shift
  local want_json=0
  [[ "${OMES_JSON:-0}" == "1" ]] && want_json=1
  local mode="${OMES_GATEWAY_MODE:-user}"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json)
        want_json=1
        shift
        ;;
      --mode)
        [[ $# -ge 2 ]] || omes_die "$OMES_EX_USAGE" "health ${target}: --mode requires a value"
        mode="$2"
        shift 2
        ;;
      -h | --help)
        _health_usage
        exit "$OMES_EX_OK"
        ;;
      *)
        omes_die "$OMES_EX_USAGE" "health ${target}: unknown argument: $1"
        ;;
    esac
  done

  case "$mode" in
    user | system) ;;
    *) omes_die "$OMES_EX_USAGE" "health ${target}: --mode must be 'user' or 'system' (got '${mode}')" ;;
  esac

  if ! command -v python3 >/dev/null 2>&1; then
    log_error "health ${target}: python3 not found (required for lib/omes/py/health/hermes.py)"
    exit "$OMES_EX_PREFLIGHT"
  fi

  local script
  script="$(_health_py_script hermes.py)"
  if [[ ! -r "$script" ]]; then
    log_error "health ${target}: ${script} not found"
    exit "$OMES_EX_PREFLIGHT"
  fi

  local hermes_home
  hermes_home="$(runtime_home hermes)"

  local output rc=0
  output="$(_health_host_facts_json | python3 "$script" --target "$target" --mode "$mode" --hermes-home "$hermes_home" 2>/dev/null)" || rc=$?

  if [[ -z "$output" ]]; then
    log_error "health ${target}: the health checker produced no output (exit ${rc})"
    exit "$OMES_EX_PREFLIGHT"
  fi

  if [[ "$want_json" -eq 1 ]]; then
    printf '%s\n' "$output"
  else
    _health_print_hermes_human "$output"
  fi

  exit "$rc"
}

# _health_print_ollama_human <json>
# Prints a short human-readable summary of <json> (the Ollama health
# checker's output). Never echoes prompts/outputs beyond what the
# checker itself already length-capped. Takes the JSON as an argument
# (via an env var, not argv, and not a heredoc) rather than reading it
# from stdin: a `python3 - <<'PY'` heredoc would consume the function's
# OWN stdin as the script source, leaving nothing for `sys.stdin` to
# read - passing the value explicitly avoids that trap entirely.
_health_print_ollama_human() {
  OMES_HEALTH_JSON="$1" python3 -c '
import json
import os

try:
    d = json.loads(os.environ["OMES_HEALTH_JSON"])
except ValueError:
    print("[omes] health ollama: the checker did not return valid JSON")
    raise SystemExit(0)

print(
    "[omes] health ollama: provider=%s profile=%s ready=%s"
    % (d.get("provider"), d.get("profile"), d.get("ready"))
)
svc = d.get("service", {})
print("[omes]   service:  %s" % svc.get("status", "unknown"))
model = d.get("model", {})
print("[omes]   model:    id=%s status=%s" % (model.get("id", ""), model.get("status", "unknown")))
caps = d.get("capabilities", {})
for name in ("chat", "structured_output", "tool_calling", "embeddings", "vision"):
    if name in caps:
        print("[omes]   capability:%-20s %s" % (name, caps[name]))
for chk in d.get("checks", []):
    if chk.get("status") not in ("pass", "not_applicable"):
        print("[omes]   WARN %s: %s" % (chk.get("name"), chk.get("detail")))
        if chk.get("remediation"):
            print("[omes]     remediation: %s" % chk["remediation"])
'
}

# _health_run_ollama [--json] [--profile <name>] [--model <id>]
_health_run_ollama() {
  local -a py_args=()
  local want_json=0
  [[ "${OMES_JSON:-0}" == "1" ]] && want_json=1

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json)
        want_json=1
        shift
        ;;
      --profile)
        [[ $# -ge 2 ]] || omes_die "$OMES_EX_USAGE" "health ollama: --profile requires a value"
        py_args+=(--profile "$2")
        shift 2
        ;;
      --model)
        [[ $# -ge 2 ]] || omes_die "$OMES_EX_USAGE" "health ollama: --model requires a value"
        py_args+=(--model "$2")
        shift 2
        ;;
      -h | --help)
        _health_usage
        exit "$OMES_EX_OK"
        ;;
      *)
        omes_die "$OMES_EX_USAGE" "health ollama: unknown argument: $1"
        ;;
    esac
  done

  if ! command -v python3 >/dev/null 2>&1; then
    log_error "health ollama: python3 not found (required for lib/omes/py/health/ollama.py)"
    exit "$OMES_EX_PREFLIGHT"
  fi

  local script
  script="$(_health_py_script ollama.py)"
  if [[ ! -r "$script" ]]; then
    log_error "health ollama: ${script} not found"
    exit "$OMES_EX_PREFLIGHT"
  fi

  local output rc=0
  output="$(python3 "$script" "${py_args[@]}" 2>/dev/null)" || rc=$?

  if [[ -z "$output" ]]; then
    log_error "health ollama: the Ollama health checker produced no output (exit ${rc})"
    exit "$OMES_EX_PREFLIGHT"
  fi

  if [[ "$want_json" -eq 1 ]]; then
    printf '%s\n' "$output"
  else
    _health_print_ollama_human "$output"
  fi

  exit "$rc"
}

# =============================================================================
# BEGIN omes health versions (issue #83) - see docs/compatibility-evidence.md
# =============================================================================
#
# `omes health versions` reports the same runtime/compatibility evidence
# lib/omes/versions.sh collects for modules/hermes/module.sh's
# module_doctor, as a standalone read-only report. This block is
# deliberately self-contained (its own usage text branch, its own
# runner) so it can be lifted out or extended without touching the
# agent/gateway/ollama targets above.

# shellcheck source=../versions.sh
source "${OMES_ROOT}/lib/omes/versions.sh"

# _health_print_versions_human <json>
# Prints a short human-readable summary of the versions.py evidence
# report, via the shared printer in lib/omes/versions.sh (also used by
# `omes status`'s evidence section so the two summaries never drift).
_health_print_versions_human() {
  versions_print_human_summary "$1" "health versions"
}

# _health_run_versions [--json]
_health_run_versions() {
  local want_json=0
  [[ "${OMES_JSON:-0}" == "1" ]] && want_json=1

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json)
        want_json=1
        shift
        ;;
      -h | --help)
        _health_usage
        exit "$OMES_EX_OK"
        ;;
      *)
        omes_die "$OMES_EX_USAGE" "health versions: unknown argument: $1"
        ;;
    esac
  done

  if ! command -v python3 >/dev/null 2>&1; then
    log_error "health versions: python3 not found (required for lib/omes/py/provenance/versions.py)"
    exit "$OMES_EX_PREFLIGHT"
  fi

  local output rc=0
  output="$(versions_collect_json 2>/dev/null)" || rc=$?

  if [[ -z "$output" ]]; then
    log_error "health versions: the evidence collector produced no output (exit ${rc})"
    exit "$OMES_EX_PREFLIGHT"
  fi

  if [[ "$want_json" -eq 1 ]]; then
    printf '%s\n' "$output"
  else
    _health_print_versions_human "$output"
  fi

  exit "$rc"
}
# =============================================================================
# END omes health versions (issue #83)
# =============================================================================

cmd_health() {
  if [[ "${1:-}" == "-h" ]] || [[ "${1:-}" == "--help" ]]; then
    _health_usage
    return 0
  fi

  if [[ "${1:-}" == "versions" ]]; then
    shift
    _health_run_versions "$@"
    return 0
  fi

  # `omes health [agent|gateway|ollama] [options]` (issues #71, #79): the
  # target may appear anywhere among the arguments (docs/cli.md §4.12
  # lets a global flag like --json sit either before or after it, e.g.
  # `omes health --json ollama` vs. `omes health ollama --json`), so scan
  # for the first known target name rather than assuming position 1;
  # everything else is passed through, in order, to that target's
  # handler. Defaults to "agent" (issue #79's `omes health [agent|gateway]`
  # makes the target optional) when none is named.
  local target="" a
  local -a rest=()
  for a in "$@"; do
    if [[ -z "$target" ]] && { [[ "$a" == "agent" ]] || [[ "$a" == "gateway" ]] || [[ "$a" == "ollama" ]]; }; then
      target="$a"
      continue
    fi
    rest+=("$a")
  done
  [[ -z "$target" ]] && target="agent"

  case "$target" in
    ollama)
      _health_run_ollama "${rest[@]+"${rest[@]}"}"
      ;;
    agent | gateway)
      _health_run_hermes "$target" "${rest[@]+"${rest[@]}"}"
      ;;
  esac
}
