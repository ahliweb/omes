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
Usage: omes health [agent|gateway|ollama|versions|ai-privacy] [options]

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
  ai-privacy  Read-only AI privacy posture/egress evidence: policy
            version, effective provider destination class
            (local/private/cloud/unknown), local endpoint network
            classification, cloud-fallback/network-isolation state,
            PASS/FAIL/WARN/BLOCKED with stable reason codes, the #215
            local-only posture source when available (issue #216), and the
            #236 model/runtime artifact provenance/integrity summary
            (declared/verified counts, checksum status, last verification
            time - never re-hashed on this call; see 'omes audit
            provenance --verify-artifacts' and
            docs/ai-data-privacy-and-model-security.md section 11). NEVER
            prints prompt text, response text, or credential values.
            Options: --json  --persist
            --persist is opt-in (issue #234): re-validates the evidence
            object against contracts/ai-egress/v1/privacy-posture-evidence.schema.json
            (refusing to write anything that fails - fail closed) and
            writes it to <state-dir>/ai-privacy-evidence/ (mode 0700/0600).
            Default behavior is unchanged: nothing is persisted without
            this flag.
  ai-privacy prune  Retention/rotation for evidence persisted by
            --persist above (issue #234). Deletes records older than
            --max-age-days and/or beyond --max-count, confined to the
            OMES-owned <state-dir>/ai-privacy-evidence/ directory and only
            ever touching files matching this module's own naming
            pattern - never a symlink, never anything else in that
            directory. Idempotent; supports --dry-run.
            Options: --json  --dry-run  --max-age-days <N>  --max-count <N>

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
  OMES_AI_PRIVACY_EXPECTED_POSTURE  override for ai.privacy.expected_posture state
                                 (restricted_local_only|unrestricted|unknown)
  OMES_AI_MODEL_ARTIFACTS       override JSON array for ai.model_artifacts.declared state
                                 (see 'omes audit provenance --verify-artifacts', issue #236)
  OMES_AI_PRIVACY_EVIDENCE_MAX_AGE_DAYS   'ai-privacy prune' default max age in days (default 90)
  OMES_AI_PRIVACY_EVIDENCE_MAX_COUNT      'ai-privacy prune' default max record count (default 500)

Exit codes: agent/gateway: 0 ready, 7 not ready. ollama: 0 ready, 7 not ready, 4 service missing.
            ai-privacy: 0 status is PASS/WARN, 7 status is FAIL/BLOCKED.
            ai-privacy prune: 0 ok, 1 a delete failed, 2 usage error (including an
            invalid/out-of-range --max-age-days/--max-count).
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

# =============================================================================
# BEGIN omes health ai-privacy (issue #216) - see
# docs/ai-data-privacy-and-model-security.md section 11
# =============================================================================
#
# `omes health ai-privacy` reports read-only AI privacy posture/egress
# evidence without ever reading prompt/response content, credentials, or
# internal Hermes databases. It reuses lib/omes/py/health/exposure.py's
# listener/firewall audit and lib/omes/py/provenance/versions.py's
# non-secret Hermes config-key allowlist rather than duplicating either,
# and hands the collected (bounded) observation to the pure evaluator in
# lib/omes/py/privacy/posture_evidence.py.
#
# This block is deliberately self-contained (own state-facts collector,
# own runner) so it can be extended or lifted out without touching the
# agent/gateway/ollama/versions targets above.

# _health_ai_privacy_state_facts_json
# Prints a small JSON object carrying OMES's OWN state (never a Hermes
# internal file) that lib/omes/py/health/ai_privacy.py needs: the
# operator-declared expected posture, and the #215 local-only-posture
# integration point (present only once #215 lands and writes these keys;
# absent today, which the evaluator treats as "unavailable", never as a
# healthy default - see posture_evidence.py's module docstring).
_health_ai_privacy_state_facts_json() {
  local expected_posture
  expected_posture="${OMES_AI_PRIVACY_EXPECTED_POSTURE:-}"
  if [[ -z "$expected_posture" ]]; then
    expected_posture="$(state_get "ai.privacy.expected_posture" 2>/dev/null || printf '')"
  fi

  local local_only_available="false"
  local local_only_status="unknown"
  if state_get "ai.local_only_posture.available" >/dev/null 2>&1; then
    local raw_available
    raw_available="$(state_get "ai.local_only_posture.available" 2>/dev/null || printf 'false')"
    [[ "$raw_available" == "true" ]] && local_only_available="true"
    local_only_status="$(state_get "ai.local_only_posture.status" 2>/dev/null || printf 'unknown')"
  fi

  local local_only_obj
  local_only_obj="$(json_obj \
    "$(json_kv available "$local_only_available" --raw)" \
    "$(json_kv status "$local_only_status")" \
    "$(json_kv source "local-only-posture-source:issue-215")")"

  json_obj \
    "$(json_kv expected_posture "${expected_posture:-unknown}")" \
    "$(json_kv local_only_posture_source "$local_only_obj" --raw)" \
    "$(json_kv model_artifact_provenance_source "$(_health_model_artifact_provenance_source_json)" --raw)"
}

# _health_model_artifact_provenance_source_json
# Issue #236 (threat AI-06): a cheap, hashing-free summary of every
# already-recorded `model-artifact:*` provenance record
# (lib/omes/py/provenance/artifacts.py's `summarize`, produced by `omes
# audit provenance --verify-artifacts` runs, never by this call itself).
# This function NEVER hashes artifact bytes - only stats/reads the small
# JSON records already on disk - so `omes health ai-privacy` stays cheap
# even when the declared artifacts are multi-gigabyte model files.
# Prints a fixed "unavailable" object (never fails, never blocks health)
# when python3 is missing or the summarizer errors.
_health_model_artifact_provenance_source_json() {
  if ! command -v python3 >/dev/null 2>&1; then
    json_obj \
      "$(json_kv available "false" --raw)" \
      "$(json_kv status "unknown")" \
      "$(json_kv reason "not_declared")" \
      "$(json_kv declared_count "0" --raw)" \
      "$(json_kv verified_count "0" --raw)" \
      "$(json_kv last_verified_at "null" --raw)"
    return 0
  fi

  local script state_dir output
  script="${OMES_ROOT}/lib/omes/py/provenance/artifacts.py"
  state_dir="$(omes_state_dir)"

  if [[ -r "$script" ]]; then
    output="$(python3 "$script" summarize --state-dir "$state_dir" 2>/dev/null)"
  fi

  if [[ -z "$output" ]]; then
    json_obj \
      "$(json_kv available "false" --raw)" \
      "$(json_kv status "unknown")" \
      "$(json_kv reason "not_declared")" \
      "$(json_kv declared_count "0" --raw)" \
      "$(json_kv verified_count "0" --raw)" \
      "$(json_kv last_verified_at "null" --raw)"
    return 0
  fi

  printf '%s\n' "$output"
}

# _health_print_ai_privacy_human <json>
# Prints a short human-readable summary, never echoing anything beyond the
# already-bounded fields the evidence JSON itself contains.
_health_print_ai_privacy_human() {
  OMES_HEALTH_JSON="$1" python3 -c '
import json
import os

try:
    d = json.loads(os.environ["OMES_HEALTH_JSON"])
except ValueError:
    print("[omes] health ai-privacy: the evidence collector did not return valid JSON")
    raise SystemExit(0)

print("[omes] health ai-privacy: status=%s destination_class=%s" % (d.get("status"), d.get("destination_class")))
print("[omes]   policy_version=%s classification_mode=%s" % (d.get("policy_version"), d.get("classification_mode")))
print("[omes]   local_endpoint=%s cloud_fallback=%s network_isolation=%s" % (
    d.get("local_endpoint_classification"), d.get("cloud_fallback_enabled"), d.get("network_isolation_active")
))
lop = d.get("local_only_posture", {})
print("[omes]   local_only_posture: available=%s status=%s" % (lop.get("available"), lop.get("status")))
map_ = d.get("model_artifact_provenance", {})
print("[omes]   model_artifact_provenance: available=%s status=%s reason=%s declared=%s verified=%s last_verified_at=%s" % (
    map_.get("available"), map_.get("status"), map_.get("reason"), map_.get("declared_count"), map_.get("verified_count"), map_.get("last_verified_at")
))
for code in d.get("reason_codes", []):
    print("[omes]   reason: %s" % code)
'
}

# _health_evidence_retention_py
# Issue #234: retention/rotation for AI-privacy evidence OMES itself
# persists - lib/omes/py/privacy/evidence_retention.py.
_health_evidence_retention_py() {
  printf '%s/lib/omes/py/privacy/evidence_retention.py\n' "$OMES_ROOT"
}

# _health_run_ai_privacy [--json] [--persist]
_health_run_ai_privacy() {
  if [[ "${1:-}" == "prune" ]]; then
    shift
    _health_run_ai_privacy_prune "$@"
    return $?
  fi

  local want_json=0 persist=0
  [[ "${OMES_JSON:-0}" == "1" ]] && want_json=1

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json)
        want_json=1
        shift
        ;;
      --persist)
        persist=1
        shift
        ;;
      -h | --help)
        _health_usage
        exit "$OMES_EX_OK"
        ;;
      *)
        omes_die "$OMES_EX_USAGE" "health ai-privacy: unknown argument: $1"
        ;;
    esac
  done

  if ! command -v python3 >/dev/null 2>&1; then
    log_error "health ai-privacy: python3 not found (required for lib/omes/py/health/ai_privacy.py)"
    exit "$OMES_EX_PREFLIGHT"
  fi

  local script
  script="$(_health_py_script ai_privacy.py)"
  if [[ ! -r "$script" ]]; then
    log_error "health ai-privacy: ${script} not found"
    exit "$OMES_EX_PREFLIGHT"
  fi

  local output rc=0
  output="$(_health_ai_privacy_state_facts_json | python3 "$script" 2>/dev/null)" || rc=$?

  if [[ -z "$output" ]]; then
    log_error "health ai-privacy: the evidence collector produced no output (exit ${rc})"
    exit "$OMES_EX_PREFLIGHT"
  fi

  # Issue #234: opt-in persistence. Default behavior (persist=0) is
  # completely unchanged from #216 - nothing is written anywhere. This
  # runs AFTER the report above is already computed/printed-decided, and
  # never changes stdout's JSON shape or the exit code the report itself
  # produced: persistence is a side effect logged to stderr only.
  if [[ "$persist" -eq 1 ]]; then
    local retention_script state_dir persist_output persist_rc=0
    retention_script="$(_health_evidence_retention_py)"
    state_dir="$(omes_state_dir)"
    if [[ ! -r "$retention_script" ]]; then
      log_warn "health ai-privacy --persist: ${retention_script} not found; evidence was NOT persisted"
    else
      persist_output="$(printf '%s' "$output" | python3 "$retention_script" persist --state-dir "$state_dir" 2>/dev/null)" || persist_rc=$?
      if [[ "$persist_rc" -ne 0 ]] || [[ -z "$persist_output" ]]; then
        log_warn "health ai-privacy --persist: could not persist evidence (exit ${persist_rc}): ${persist_output}"
      else
        OMES_AI_PRIVACY_PERSIST_JSON="$persist_output" python3 -c '
import json
import os

try:
    d = json.loads(os.environ["OMES_AI_PRIVACY_PERSIST_JSON"])
except ValueError:
    d = {}

if d.get("ok"):
    print("[omes] health ai-privacy --persist: wrote %s" % d.get("path"))
else:
    print("[omes] health ai-privacy --persist: refused to persist: %s" % d.get("error"))
' >&2
      fi
    fi
  fi

  if [[ "$want_json" -eq 1 ]]; then
    printf '%s\n' "$output"
  else
    _health_print_ai_privacy_human "$output"
  fi

  exit "$rc"
}

# _health_ai_privacy_prune_usage
_health_ai_privacy_prune_usage() {
  cat <<'EOF'
Usage: omes health ai-privacy prune [--max-age-days <N>] [--max-count <N>] [--dry-run] [--json]

Retention/rotation for AI-privacy evidence persisted by
`omes health ai-privacy --persist` (issue #234). Confined strictly to
the OMES-owned <state-dir>/ai-privacy-evidence/ directory: only files
matching this module's own naming pattern are ever candidates for
deletion, a symlink is never deleted regardless of its target, and a
resolved path that would escape that directory is refused. Idempotent -
running this twice with the same arguments is a no-op the second time.

Options:
  --max-age-days <N>   Delete records older than N days (default 90, or
                        OMES_AI_PRIVACY_EVIDENCE_MAX_AGE_DAYS).
  --max-count <N>       Keep at most the N most recently persisted
                        records (default 500, or
                        OMES_AI_PRIVACY_EVIDENCE_MAX_COUNT).
  --dry-run             Report what would be pruned without deleting it.
  --json                Print the full JSON result instead of a human summary.

A non-positive or absurdly large --max-age-days/--max-count value fails
closed with a usage error rather than being silently clamped.

Exit codes: 0 ok, 1 a delete failed, 2 usage error (including an
invalid/out-of-range --max-age-days/--max-count).
EOF
}

# _health_print_ai_privacy_prune_human <json>
_health_print_ai_privacy_prune_human() {
  OMES_HEALTH_PRUNE_JSON="$1" python3 -c '
import json
import os

try:
    d = json.loads(os.environ["OMES_HEALTH_PRUNE_JSON"])
except ValueError:
    print("[omes] health ai-privacy prune: the pruner did not return valid JSON")
    raise SystemExit(0)

mode = "dry-run" if d.get("dry_run") else "live"
print("[omes] health ai-privacy prune (%s): ok=%s dir=%s" % (mode, d.get("ok"), d.get("evidence_dir")))
print("[omes]   pruned=%d kept=%d skipped=%d" % (len(d.get("pruned", [])), len(d.get("kept", [])), len(d.get("skipped", []))))
for name in d.get("pruned", []):
    print("[omes]   pruned: %s" % name)
for s in d.get("skipped", []):
    print("[omes]   skipped: %s (%s)" % (s.get("name"), s.get("reason")))
'
}

# _health_run_ai_privacy_prune [--max-age-days <N>] [--max-count <N>] [--dry-run] [--json]
_health_run_ai_privacy_prune() {
  local want_json=0 dry_run=0
  [[ "${OMES_JSON:-0}" == "1" ]] && want_json=1
  local max_age_days="${OMES_AI_PRIVACY_EVIDENCE_MAX_AGE_DAYS:-90}"
  local max_count="${OMES_AI_PRIVACY_EVIDENCE_MAX_COUNT:-500}"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json)
        want_json=1
        shift
        ;;
      --dry-run)
        dry_run=1
        shift
        ;;
      --max-age-days)
        [[ $# -ge 2 ]] || omes_die "$OMES_EX_USAGE" "health ai-privacy prune: --max-age-days requires a value"
        max_age_days="$2"
        shift 2
        ;;
      --max-count)
        [[ $# -ge 2 ]] || omes_die "$OMES_EX_USAGE" "health ai-privacy prune: --max-count requires a value"
        max_count="$2"
        shift 2
        ;;
      -h | --help)
        _health_ai_privacy_prune_usage
        exit "$OMES_EX_OK"
        ;;
      *)
        omes_die "$OMES_EX_USAGE" "health ai-privacy prune: unknown argument: $1"
        ;;
    esac
  done

  case "$max_age_days" in
    '' | *[!0-9]*)
      omes_die "$OMES_EX_USAGE" "health ai-privacy prune: --max-age-days must be a positive integer (got '${max_age_days}')"
      ;;
  esac
  case "$max_count" in
    '' | *[!0-9]*)
      omes_die "$OMES_EX_USAGE" "health ai-privacy prune: --max-count must be a positive integer (got '${max_count}')"
      ;;
  esac

  if ! command -v python3 >/dev/null 2>&1; then
    log_error "health ai-privacy prune: python3 not found (required for lib/omes/py/privacy/evidence_retention.py)"
    exit "$OMES_EX_PREFLIGHT"
  fi

  local script
  script="$(_health_evidence_retention_py)"
  if [[ ! -r "$script" ]]; then
    log_error "health ai-privacy prune: ${script} not found"
    exit "$OMES_EX_PREFLIGHT"
  fi

  local state_dir max_age_seconds
  state_dir="$(omes_state_dir)"
  max_age_seconds=$((max_age_days * 86400))

  local -a py_args=(prune --state-dir "$state_dir" --max-age-seconds "$max_age_seconds" --max-count "$max_count")
  [[ "$dry_run" -eq 1 ]] && py_args+=(--dry-run)

  local output rc=0
  output="$(python3 "$script" "${py_args[@]}" 2>/dev/null)" || rc=$?

  if [[ -z "$output" ]]; then
    log_error "health ai-privacy prune: the pruner produced no output (exit ${rc})"
    exit "$OMES_EX_PREFLIGHT"
  fi

  if [[ "$want_json" -eq 1 ]]; then
    printf '%s\n' "$output"
  else
    _health_print_ai_privacy_prune_human "$output"
  fi

  exit "$rc"
}
# =============================================================================
# END omes health ai-privacy (issue #216, retention issue #234)
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

  if [[ "${1:-}" == "ai-privacy" ]]; then
    shift
    _health_run_ai_privacy "$@"
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
