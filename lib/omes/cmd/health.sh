# shellcheck shell=bash
# omes-help: layered health checks for Ollama (issue #71)
#
# lib/omes/cmd/health.sh - `omes health <target>` dispatcher.
#
# Sourced by bin/omes's extension-command mechanism (lib/omes/cmd/README.md)
# after every lib/omes/*.sh library and with global flags already parsed.
# Read-only: this command never mutates host state and honors --dry-run
# trivially (there is nothing to dry-run for a health probe).

# _health_py_script <name>
# Prints the path to a lib/omes/py/health/<name> entry point.
_health_py_script() {
  printf '%s/lib/omes/py/health/%s\n' "$OMES_ROOT" "$1"
}

# _health_usage
_health_usage() {
  cat <<'EOF'
Usage: omes health <target> [options]

Targets:
  ollama    Layered service/model/capability health checks for the
            optional Ollama local AI runtime (issue #71).
            Options: --json  --profile <name>  --model <id>

Environment (see docs/configuration.md and docs/ollama.md):
  OLLAMA_HOST                  endpoint (default 127.0.0.1:11434)
  OMES_OLLAMA_MODEL            model id to check (or --model)
  OMES_OLLAMA_PROFILE          capability profile: text|structured|tools|embeddings|vision|full
  OMES_OLLAMA_PROFILE_FILE     path to a JSON profile file (overrides OMES_OLLAMA_PROFILE)
  OMES_OLLAMA_ALLOW_REMOTE=1   explicitly accept a non-loopback endpoint
  OMES_OLLAMA_EXPECT_PLACEMENT expected runtime placement: gpu|cpu|any (default any)
  OMES_HEALTH_TIMEOUT          per-probe timeout in seconds (default 10)
  OMES_OLLAMA_LOAD_TIMEOUT     bounded model-load timeout in seconds (default 30)

Exit codes: 0 ready, 7 not ready, 4 service missing.
EOF
}

# _health_print_ollama_human
# Reads the Ollama health JSON result on stdin and prints a short
# human-readable summary. Never echoes prompts/outputs beyond what the
# checker itself already length-capped.
_health_print_ollama_human() {
  python3 - <<'PY'
import json
import sys

try:
    d = json.load(sys.stdin)
except ValueError:
    print("[omes] health ollama: the checker did not return valid JSON")
    sys.exit(0)

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
PY
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
    printf '%s' "$output" | _health_print_ollama_human
  fi

  exit "$rc"
}

cmd_health() {
  local target="${1:-}"
  case "$target" in
    ollama)
      shift
      _health_run_ollama "$@"
      ;;
    "" | -h | --help)
      _health_usage
      ;;
    *)
      log_error "health: unknown target '${target}' (expected: ollama)"
      exit "$OMES_EX_USAGE"
      ;;
  esac
}
