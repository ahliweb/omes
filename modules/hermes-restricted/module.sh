#!/usr/bin/env bash
# shellcheck shell=bash
# modules/hermes-restricted/module.sh - restricted/local-only inference
# deployment posture for the Hermes SYSTEM gateway (issue #215; depends on
# #214/ADR-0029; docs/ai-data-privacy-and-model-security.md section 10).
#
# The MODULE_* metadata variables below are read by lib/omes/module.sh
# after sourcing this file; ShellCheck cannot see that cross-file usage
# when analyzing this file on its own.
#
# What this module does (and does not do):
#   - It does NOT run or select a model. Hermes remains authoritative for
#     reasoning and model/provider routing (ADR-0017, ADR-0029) - this
#     module never adds a second provider router.
#   - It verifies, via ONLY the supported `hermes config get <key>`
#     interface (HERMES_HOME-redirected, never `sudo -u`, never reading
#     `.hermes/` files/databases directly), that the target user's
#     currently configured model endpoint classifies as local/private per
#     the #214 policy contract, by delegating the actual allow/deny
#     decision to lib/omes/py/privacy/egress_policy.py through
#     lib/omes/py/privacy/restricted_posture.py (issue #215's own new
#     code only derives the bounded `destination` value; it does not
#     reimplement the decision matrix).
#   - It reports GPU/CPU/runtime facts as a READ-ONLY, advisory preflight
#     (module_check never mutates and never hard-fails on hardware
#     shortfalls - only on an unverifiable/non-local endpoint).
#   - Where the host's systemd supports it, it writes an ADDITIONAL,
#     separately OMES-managed drop-in (40-omes-restricted-network.conf,
#     alongside modules/hermes-gateway/hardening.sh's own
#     20-omes-hardening.conf, in the SAME hermes-gateway-system unit's
#     drop-in directory) that denies all outbound network access from the
#     gateway's systemd cgroup except loopback/link-local (and any
#     operator-approved private ranges) - see module_apply's confirmation
#     prompt for the explicit compatibility trade-off this creates for
#     any browser-automation/internet-dependent MCP tool running in that
#     SAME gateway process.
#   - Drift (the endpoint reconfigured toward a cloud/unapproved
#     destination after this module was applied) is reported by
#     module_verify as a hard FAILURE - never a silent fallback to a
#     cloud provider.
#   - "Documented update/admin paths OUTSIDE runtime execution" (apt/pkg
#     updates, an operator's own interactive `hermes` CLI session, OMES's
#     own module_apply run as root) are NOT inside the hermes-gateway
#     systemd unit's cgroup and are therefore unaffected by
#     IPAddressDeny=/IPAddressAllow= - those directives scope only the
#     gateway unit's own process tree.
#
# Scope: MODULE_SCOPE=root (same as modules/hermes-gateway-system, which
# this module hardens). MODULE_REQUIRES=(hermes-gateway-system) documents
# and orders that dependency for `omes install`, but module_check ALSO
# verifies the observable effect directly (the system unit is enabled) -
# the same "verify the effect, not just cross-module state" approach
# modules/hermes-gateway-system/module.sh documents for its own
# root-scope, opt-in dependency, because `run_checks` runs every
# selected module's module_check before any module_apply runs, so a
# fresh combined install cannot rely on hermes-gateway-system's state
# having been written yet.
#
# This module is intentionally NOT wired into any profiles/*.profile
# (opt-in only): `sudo omes install --module hermes-restricted` after
# `sudo omes install --module hermes-gateway-system`.

# shellcheck disable=SC2034
MODULE_NAME="hermes-restricted"
# shellcheck disable=SC2034
MODULE_DESCRIPTION="Restricted/local-only inference deployment posture for the Hermes system gateway (opt-in; verifies no cloud model egress, denies outbound network by default)"
# shellcheck disable=SC2034
MODULE_SCOPE="root"
# shellcheck disable=SC2034
MODULE_REQUIRES=(hermes-gateway-system)
# shellcheck disable=SC2034
MODULE_PROFILES=()

RESTRICTED_UNIT="hermes-gateway"
RESTRICTED_DROPIN_NAME="40-omes-restricted-network.conf"
RESTRICTED_POSTURE_SCRIPT="${OMES_ROOT}/lib/omes/py/privacy/restricted_posture.py"

# shellcheck source=../hermes-gateway/hardening.sh
source "${OMES_ROOT}/modules/hermes-gateway/hardening.sh"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# _hr_target_user
# Prints OMES_HERMES_RESTRICTED_SYSTEM_USER, falling back to
# OMES_HERMES_GATEWAY_SYSTEM_USER (the same operator-supplied variable
# modules/hermes-gateway-system/module.sh requires) so an operator who
# already set that variable for the base system-gateway install does not
# have to repeat it. No other default - the operator must name the
# account explicitly.
_hr_target_user() {
  printf '%s\n' "${OMES_HERMES_RESTRICTED_SYSTEM_USER:-${OMES_HERMES_GATEWAY_SYSTEM_USER:-}}"
}

# _hr_user_exists_and_not_root <user>
_hr_user_exists_and_not_root() {
  local user="$1"
  [[ -n "$user" ]] || return 1
  [[ "$user" != "root" ]] || return 1
  local uid
  uid="$(id -u "$user" 2>/dev/null)" || return 1
  [[ "$uid" != "0" ]]
}

# _hr_user_home <user>
_hr_user_home() {
  getent passwd "$1" 2>/dev/null | awk -F: '{print $6}'
}

# _hr_hermes_home <user>
_hr_hermes_home() {
  printf '%s/.hermes\n' "$(_hr_user_home "$1")"
}

# _hr_dropin_dir
# Same drop-in directory hermes-gateway-system/hardening.sh already
# manage (mode="system"); reuses hardening_dropin_dir so all three
# drop-ins (omes-path.conf, 20-omes-hardening.conf,
# 40-omes-restricted-network.conf) live together and honor the same
# OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR test override.
_hr_dropin_dir() {
  hardening_dropin_dir "system"
}

_hr_dropin_file() {
  printf '%s/%s\n' "$(_hr_dropin_dir)" "$RESTRICTED_DROPIN_NAME"
}

# _hr_config_get <hermes-home> <key>
# Reads one `hermes config get <key>` value for the target user's Hermes
# config via the SUPPORTED HERMES_HOME env-var redirection (verified
# against https://hermes-agent.nousresearch.com/docs/user-guide/configuration:
# "Hermes state is still profile-scoped through HERMES_HOME") - never
# `sudo -u`, never a second provider router, never a read of `.hermes/`
# files/databases directly. Always prints (possibly empty) and returns 0;
# a missing/failed read is evidence for the caller to treat as "unset"
# (fail closed), not a hard error in this helper.
_hr_config_get() {
  local home="$1" key="$2"
  command -v hermes >/dev/null 2>&1 || {
    printf ''; return 0
  }
  local out=""
  if command -v timeout >/dev/null 2>&1; then
    out="$(HERMES_HOME="$home" timeout 5 hermes config get "$key" 2>/dev/null)" || out=""
  else
    out="$(HERMES_HOME="$home" hermes config get "$key" 2>/dev/null)" || out=""
  fi
  printf '%s\n' "$out" | head -1
}

# _hr_provider_id <model>
# Parses the provider segment out of a "provider/model" `model` config
# value and validates it against a strict allowlist pattern BEFORE it is
# ever used as a `hermes config get providers.<id>.base_url` argv value.
# Prints nothing (not a guess) on anything that does not match.
_hr_provider_id() {
  local model="$1"
  [[ "$model" == */* ]] || return 0
  local id="${model%%/*}"
  [[ "$id" =~ ^[A-Za-z0-9_-]{1,64}$ ]] || return 0
  printf '%s\n' "$id"
}

# _hr_evaluate_posture <hermes-home>
# Reads the two verified, non-secret hermes config keys (`model`,
# `providers.<id>.base_url`) for the target user, then hands off to
# lib/omes/py/privacy/restricted_posture.py -> egress_policy.py for the
# actual decision. Prints the evaluator's JSON result on stdout; returns
# 0 only when decision == "allow" (restricted_posture.py's own exit code
# convention: 0 = allow, 2 = deny/approval_required, 1 = internal error).
_hr_evaluate_posture() {
  local home="$1"
  local model provider_id base_url

  model="$(_hr_config_get "$home" "model")"
  provider_id="$(_hr_provider_id "$model")"
  base_url=""
  if [[ -n "$provider_id" ]]; then
    base_url="$(_hr_config_get "$home" "providers.${provider_id}.base_url")"
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    log_error "hermes-restricted: python3 not found - cannot evaluate the restricted posture"
    printf '{"ok": false, "error": "python3 not found"}\n'
    return 1
  fi
  if [[ ! -r "$RESTRICTED_POSTURE_SCRIPT" ]]; then
    log_error "hermes-restricted: posture evaluator not found at ${RESTRICTED_POSTURE_SCRIPT}"
    printf '{"ok": false, "error": "evaluator not found"}\n'
    return 1
  fi

  local approved="false"
  if [[ "${OMES_HERMES_RESTRICTED_PRIVATE_ENDPOINT_APPROVED:-0}" == "1" ]]; then
    approved="true"
  fi

  local request_json
  request_json="$(OMES_HR_MODEL="$model" OMES_HR_BASE_URL="$base_url" OMES_HR_PROVIDER_ID="$provider_id" OMES_HR_APPROVED="$approved" python3 -c '
import json
import os

print(json.dumps({
    "model": os.environ.get("OMES_HR_MODEL") or None,
    "base_url": os.environ.get("OMES_HR_BASE_URL") or None,
    "provider_id": os.environ.get("OMES_HR_PROVIDER_ID") or None,
    "private_endpoint_approved": os.environ.get("OMES_HR_APPROVED") == "true",
}))
')"

  local result rc=0
  result="$(printf '%s' "$request_json" | python3 "$RESTRICTED_POSTURE_SCRIPT")" || rc=$?
  printf '%s\n' "$result"
  return "$rc"
}

# _hr_fallback_configured <hermes-home>
# Best-effort, read-only check for the LEGACY `fallback_model` Hermes
# config key (a single scalar - verified against
# https://hermes-agent.nousresearch.com/docs/user-guide/features/fallback-providers
# as readable the same way `model`/`providers.<id>.base_url` already are).
# A non-empty value means Hermes has a configured cloud-fallback target,
# which is directly incompatible with "no silent cloud fallback" (docs/
# ai-data-privacy-and-model-security.md section 10).
#
# Deliberately does NOT attempt to read the newer `fallback_providers`
# key: it is a LIST, and neither that page nor
# https://hermes-agent.nousresearch.com/docs/user-guide/configuration
# documents what `hermes config get fallback_providers` actually prints
# for a list-valued path (that configuration page notes `hermes config
# get` on some paths "prints the value from your file together with a
# stderr notice that Hermes may not read it" - i.e. even a successful
# read is not proof the value is authoritative). Parsing an undocumented,
# possibly multi-line format with an unverified read-back guarantee would
# risk exactly the kind of confident-looking-but-wrong evidence issue
# #216/ADR-0029 warn against, so this is an honest, documented gap rather
# than a guess - see the module.sh header comment and docs/ai-data-
# privacy-and-model-security.md section 10.
_hr_fallback_configured() {
  local home="$1"
  local value
  value="$(_hr_config_get "$home" "fallback_model")"
  [[ -n "$value" ]]
}

# _hr_evaluate_full <hermes-home>
# Combines _hr_evaluate_posture's endpoint-locality decision with
# _hr_fallback_configured's independent fallback-configuration check into
# ONE pass/fail result for module_check/module_apply/module_verify to
# share. Prints TWO lines: the endpoint-locality evaluator's JSON
# (unchanged and always on line 1 - the fallback check is intentionally
# layered ON TOP, never folded into egress_policy's own decision/reason-
# code vocabulary), then "true"/"false" for whether a legacy fallback is
# configured. Two lines (not a bash global) deliberately: a global set
# inside this function would be invisible to the caller the moment this
# function is invoked via command substitution (a subshell), which is
# exactly how every call site here needs to capture the JSON - see
# _hr_split_evaluate_full for how callers pull the two lines back apart.
# Returns 0 only when BOTH checks pass.
_hr_evaluate_full() {
  local home="$1"
  local result rc=0
  result="$(_hr_evaluate_posture "$home")" || rc=$?
  printf '%s\n' "$result"

  local fallback_configured="false"
  if _hr_fallback_configured "$home"; then
    fallback_configured="true"
    rc=1
  fi
  printf '%s\n' "$fallback_configured"
  return "$rc"
}

# _hr_split_evaluate_full <combined-output>
# Splits _hr_evaluate_full's two-line output back into the globals
# _HR_LAST_RESULT_JSON and _HR_LAST_FALLBACK_CONFIGURED for the caller to
# read immediately afterward (in the SAME shell, not a subshell, so this
# assignment is visible).
_HR_LAST_RESULT_JSON=""
_HR_LAST_FALLBACK_CONFIGURED="false"
_hr_split_evaluate_full() {
  local combined="$1"
  _HR_LAST_FALLBACK_CONFIGURED="${combined##*$'\n'}"
  _HR_LAST_RESULT_JSON="${combined%$'\n'*}"
}

# _hr_log_posture_result <json>
# Logs a one-line, bounded (secret-free, prompt-free) summary of the
# posture evaluator's output - only the evidence fields docs/ai-data-
# privacy-and-model-security.md section 11 allows (classification bucket,
# destination, decision, reason codes), never a raw endpoint URL or
# credential.
_hr_log_posture_result() {
  local result="$1"
  if [[ -z "$result" ]]; then
    log_error "hermes-restricted: posture evaluator produced no output"
    return 0
  fi
  local summary
  summary="$(printf '%s' "$result" | python3 -c '
import json
import sys

try:
    d = json.load(sys.stdin)
except ValueError:
    print("posture evaluator returned invalid JSON")
    raise SystemExit(0)
if not d.get("ok", True):
    print("posture evaluator error: %s" % d.get("error"))
    raise SystemExit(0)
print("endpoint_classification=%s destination=%s decision=%s reason_codes=%s" % (
    d.get("endpoint_classification"), d.get("destination"), d.get("decision"),
    ",".join(d.get("reason_codes") or []),
))
' 2>/dev/null || printf 'posture evaluator output could not be summarized')"
  log_info "hermes-restricted: restricted posture check - ${summary}"
}

# _hr_216_status_from_decision <decision>
# Maps egress_policy.py's decision vocabulary (allow/deny/approval_required)
# onto issue #216's independently-shipped `ai.local_only_posture.status`
# vocabulary (pass/fail/warn/unknown - see
# lib/omes/py/privacy/posture_evidence.py's LOCAL_ONLY_SOURCE_STATUSES).
# #216 documents this exact mapping target in docs/ai-data-privacy-and-
# model-security.md section 10 - this function is the one place #215
# satisfies that seam, so the two features do not contradict each other. `approval_required` maps to `warn`
# (never `pass`): OMES must never let a decision that still requires human
# review read as a clean pass.
_hr_216_status_from_decision() {
  case "$1" in
    allow) printf 'pass\n' ;;
    deny) printf 'fail\n' ;;
    approval_required) printf 'warn\n' ;;
    *) printf 'unknown\n' ;;
  esac
}

# _hr_persist_posture_evidence <json> <fallback-configured: true|false>
# Persists ONLY the bounded evidence fields (docs section 11) to state -
# never the raw endpoint URL, model name, or provider config value. Writes
# two things:
#   1. This module's own detailed evidence (module.hermes-restricted.*).
#   2. Issue #216's integration seam (`ai.local_only_posture.available` /
#      `ai.local_only_posture.status`), using EXACTLY the state keys and
#      closed vocabulary #216 documents as the
#      contract #215 must satisfy - see _hr_216_status_from_decision.
#      `available` is "true" whenever this evaluator actually ran and
#      produced a decision (including "deny" - #216 must see a real FAIL,
#      not an absence that reads as merely "not configured yet"); it is
#      "false" only when the evaluator itself could not run at all (e.g.
#      python3 missing). A configured legacy Hermes fallback
#      (_hr_fallback_configured) always forces status=fail regardless of
#      the endpoint-locality decision, because a configured cloud fallback
#      is itself a restricted-posture violation independent of where the
#      primary endpoint points.
_hr_persist_posture_evidence() {
  local result="$1"
  local fallback_configured="${2:-false}"
  [[ -n "$result" ]] || return 0
  local line
  line="$(printf '%s' "$result" | python3 -c '
import json
import sys

try:
    d = json.load(sys.stdin)
except ValueError:
    print("false\t\t\t\t")
    raise SystemExit(0)
print("\t".join([
    "true" if d.get("ok", False) else "false",
    d.get("endpoint_classification") or "",
    d.get("destination") or "",
    d.get("decision") or "",
    ",".join(d.get("reason_codes") or []),
]))
' 2>/dev/null || true)"
  [[ -n "$line" ]] || return 0

  local ok classification destination decision reasons
  IFS=$'\t' read -r ok classification destination decision reasons <<<"$line"
  state_set "module.hermes-restricted.endpoint_classification" "$classification"
  state_set "module.hermes-restricted.destination" "$destination"
  state_set "module.hermes-restricted.decision" "$decision"
  state_set "module.hermes-restricted.reason_codes" "$reasons"
  state_set "module.hermes-restricted.fallback_configured" "$fallback_configured"
  state_set "module.hermes-restricted.evaluated_at" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  local seam_available="false"
  local seam_status="unknown"
  if [[ "$ok" == "true" ]]; then
    seam_available="true"
    seam_status="$(_hr_216_status_from_decision "$decision")"
    if [[ "$fallback_configured" == "true" ]] && [[ "$seam_status" != "fail" ]]; then
      seam_status="fail"
    fi
  fi
  state_set "ai.local_only_posture.available" "$seam_available"
  state_set "ai.local_only_posture.status" "$seam_status"
}

# _hr_preflight_report
# Read-only, advisory-only GPU/CPU/runtime facts (issue #215 scope:
# "Preflight GPU/CPU/runtime requirements without mutation"). Never
# returns non-zero and never blocks module_check - a hardware shortfall
# is an operator warning, not a posture failure (the posture failure is
# the endpoint-locality check, evaluated separately).
_hr_preflight_report() {
  local cores mem_kb mem_gb
  cores="$(nproc --all 2>/dev/null || printf 'unknown')"
  mem_kb="$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || true)"
  if [[ -n "$mem_kb" ]]; then
    mem_gb=$((mem_kb / 1024 / 1024))
  else
    mem_gb="unknown"
  fi
  log_info "hermes-restricted preflight (read-only, advisory only): cpu_cores=${cores} mem_total_gib=${mem_gb}"

  local min_mem="${OMES_HERMES_RESTRICTED_MIN_MEM_GB:-8}"
  if [[ "$mem_gb" != "unknown" ]] && [[ "$mem_gb" -lt "$min_mem" ]]; then
    log_warn "hermes-restricted preflight: total memory (${mem_gb} GiB) is below the suggested minimum (${min_mem} GiB) for local inference - advisory only, does not block module_check"
  fi

  local gpu="none detected"
  if command -v nvidia-smi >/dev/null 2>&1; then
    gpu="nvidia (nvidia-smi present)"
  elif command -v rocm-smi >/dev/null 2>&1; then
    gpu="amd rocm (rocm-smi present)"
  elif [[ -d /dev/dri ]]; then
    gpu="generic DRI device present (/dev/dri)"
  fi
  log_info "hermes-restricted preflight: gpu=${gpu} - informational only; OMES does not select, install, or manage a model runtime (Hermes remains authoritative, ADR-0017/ADR-0029)"

  local major
  major="$(hardening_systemd_major)"
  if [[ -n "$major" ]] && [[ "$major" -lt 235 ]]; then
    log_warn "hermes-restricted preflight: systemd ${major} detected; IPAddressDeny=/IPAddressAllow= require systemd >= 235 - the network-egress policy may not be enforced on this host"
  fi

  return 0
}

# _hr_render_network_policy
# Prints the [Service] drop-in content that denies all outbound network
# access from the gateway unit's cgroup except loopback/link-local, plus
# any operator-approved private ranges (OMES_HERMES_RESTRICTED_ALLOW_CIDRS,
# colon-separated CIDRs/hostnames - e.g. an approved private_endpoint
# network). This is a full-unit, all-or-nothing network policy: it
# applies to every process in the hermes-gateway unit, not just model
# calls (see module.sh header comment's compatibility caveat).
_hr_render_network_policy() {
  local allow="localhost link-local"
  local extra="${OMES_HERMES_RESTRICTED_ALLOW_CIDRS:-}"
  if [[ -n "$extra" ]]; then
    local IFS=':'
    local c
    for c in $extra; do
      [[ -z "$c" ]] && continue
      allow="${allow} ${c}"
    done
  fi
  printf '[Service]\n'
  printf 'IPAddressDeny=any\n'
  printf 'IPAddressAllow=%s\n' "$allow"
}

# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------

module_check() {
  if ! omes_is_root; then
    log_error "hermes-restricted: requires root - run via: sudo omes install --module hermes-restricted"
    return 1
  fi

  local user
  user="$(_hr_target_user)"
  if [[ -z "$user" ]]; then
    log_error "hermes-restricted: OMES_HERMES_RESTRICTED_SYSTEM_USER (or OMES_HERMES_GATEWAY_SYSTEM_USER) must be set to the non-root account whose Hermes system gateway this restricted posture applies to"
    return 1
  fi

  if ! _hr_user_exists_and_not_root "$user"; then
    log_error "hermes-restricted: refusing to configure a restricted posture for user '${user}' - it must be an existing, non-root account (OMES never runs the gateway as root)"
    return 1
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    log_error "hermes-restricted: systemctl not found"
    return 1
  fi

  if ! _hardening_systemctl "system" is-enabled "$RESTRICTED_UNIT" >/dev/null 2>&1; then
    log_error "hermes-restricted: system unit '${RESTRICTED_UNIT}' is not enabled - install/apply the hermes-gateway-system module first (sudo omes install --module hermes-gateway-system)"
    return 1
  fi

  _hr_preflight_report

  local home combined rc=0
  home="$(_hr_hermes_home "$user")"
  combined="$(_hr_evaluate_full "$home")" || rc=$?
  _hr_split_evaluate_full "$combined"
  _hr_log_posture_result "$_HR_LAST_RESULT_JSON"
  if [[ "$_HR_LAST_FALLBACK_CONFIGURED" == "true" ]]; then
    log_error "hermes-restricted: a legacy Hermes 'fallback_model' config key is set - a configured cloud-fallback target is incompatible with the restricted/local-only posture (docs/ai-data-privacy-and-model-security.md section 10: 'no silent cloud fallback'). Best-effort/read-only check; the newer 'fallback_providers' list is not parsed (see module.sh)."
  fi

  if [[ "$rc" -ne 0 ]]; then
    log_error "hermes-restricted: restricted/local-only posture check FAILED (see reason_codes above). No silent cloud fallback is performed. Fix the configured Hermes model endpoint (providers.<id>.base_url for the active 'model' provider) to a loopback/private address, or - for an approved private_endpoint only - set OMES_HERMES_RESTRICTED_PRIVATE_ENDPOINT_APPROVED=1 after an explicit reviewed decision (docs/ai-data-privacy-and-model-security.md section 6); a RESTRICTED classification is never auto-approved for cloud_sanitized."
    return 1
  fi

  return 0
}

module_apply() {
  local user home
  user="$(_hr_target_user)"
  home="$(_hr_hermes_home "$user")"

  if omes_dry_run; then
    log_info "[dry-run] would re-evaluate the restricted posture and, if it still allows, write $(_hr_dropin_file) and systemctl daemon-reload + restart ${RESTRICTED_UNIT}"
    return 0
  fi

  local combined rc=0
  combined="$(_hr_evaluate_full "$home")" || rc=$?
  _hr_split_evaluate_full "$combined"
  _hr_log_posture_result "$_HR_LAST_RESULT_JSON"
  if [[ "$_HR_LAST_FALLBACK_CONFIGURED" == "true" ]]; then
    log_error "hermes-restricted: a legacy Hermes 'fallback_model' config key is set - a configured cloud-fallback target is incompatible with the restricted/local-only posture (docs/ai-data-privacy-and-model-security.md section 10: 'no silent cloud fallback'). Best-effort/read-only check; the newer 'fallback_providers' list is not parsed (see module.sh)."
  fi

  # Issue #216's evidence seam is persisted regardless of pass/fail below
  # (a failed/refused apply is exactly the FAIL evidence #216 needs to
  # see - see docs/ai-data-privacy-and-model-security.md section 10;
  # never leave the seam at a stale/absent value just because this apply
  # was refused). `module.hermes-restricted.target_user`/`network_policy`
  # (this module's own record of actually-applied host state) are set
  # further below, ONLY on a successful apply.
  state_set "ai.privacy.expected_posture" "restricted_local_only"
  _hr_persist_posture_evidence "$_HR_LAST_RESULT_JSON" "$_HR_LAST_FALLBACK_CONFIGURED"

  if [[ "$rc" -ne 0 ]]; then
    log_error "hermes-restricted: refusing to apply - the restricted posture check failed (see above). This is a fail-closed refusal; no network policy is written and nothing is left half-applied."
    return 1
  fi

  local dropin_dir dropin_file
  dropin_dir="$(_hr_dropin_dir)"
  dropin_file="$(_hr_dropin_file)"
  mkdir -p "$dropin_dir"
  omes_manage_path "$dropin_file"
  _hr_render_network_policy >"$dropin_file"
  chmod 644 "$dropin_file"
  log_info "hermes-restricted: wrote ${dropin_file} (deny-by-default outbound network policy for ${RESTRICTED_UNIT})"

  _hardening_systemctl "system" daemon-reload

  if ! omes_confirm "Restart ${RESTRICTED_UNIT} now to apply the restricted-network policy? This denies ALL outbound network access from the gateway's process tree except loopback/link-local (and any operator-approved ranges) - any browser-automation or internet-dependent MCP tool running in this SAME gateway will stop working."; then
    log_warn "hermes-restricted: network policy drop-in written but the unit was not restarted (declined); it only takes effect on the next restart"
  else
    if ! _hardening_systemctl "system" restart "$RESTRICTED_UNIT"; then
      log_error "hermes-restricted: failed to restart ${RESTRICTED_UNIT} - automatically rolling back the network policy drop-in"
      rm -f "$dropin_file"
      rmdir "$dropin_dir" 2>/dev/null || true
      _hardening_systemctl "system" daemon-reload
      _hardening_systemctl "system" restart "$RESTRICTED_UNIT" >/dev/null 2>&1 || true
      return 1
    fi

    local timeout_s="${OMES_HERMES_RESTRICTED_TIMEOUT:-15}"
    local waited=0
    local active=0
    while ((waited < timeout_s)); do
      if _hardening_systemctl "system" is-active "$RESTRICTED_UNIT" >/dev/null 2>&1; then
        active=1
        break
      fi
      sleep 1
      waited=$((waited + 1))
    done
    if [[ "$active" -ne 1 ]]; then
      log_error "hermes-restricted: ${RESTRICTED_UNIT} did not become active within ${timeout_s}s under the restricted-network policy - automatically rolling back"
      rm -f "$dropin_file"
      rmdir "$dropin_dir" 2>/dev/null || true
      _hardening_systemctl "system" daemon-reload
      _hardening_systemctl "system" restart "$RESTRICTED_UNIT" >/dev/null 2>&1 || true
      return 1
    fi
  fi

  # `ai.privacy.expected_posture`/the #216 evidence seam were already
  # persisted above, unconditionally, before this success-only host state.
  state_set "module.hermes-restricted.target_user" "$user"
  state_set "module.hermes-restricted.network_policy" "deny_by_default"

  return 0
}

module_verify() {
  if omes_dry_run; then
    log_info "[dry-run] would verify: restricted-posture drift + ${RESTRICTED_UNIT} network policy drop-in"
    return 0
  fi

  local user home combined rc=0
  user="$(_hr_target_user)"
  home="$(_hr_hermes_home "$user")"
  combined="$(_hr_evaluate_full "$home")" || rc=$?
  _hr_split_evaluate_full "$combined"
  _hr_log_posture_result "$_HR_LAST_RESULT_JSON"
  if [[ "$_HR_LAST_FALLBACK_CONFIGURED" == "true" ]]; then
    log_error "hermes-restricted: a legacy Hermes 'fallback_model' config key is set - a configured cloud-fallback target is incompatible with the restricted/local-only posture (docs/ai-data-privacy-and-model-security.md section 10: 'no silent cloud fallback'). Best-effort/read-only check; the newer 'fallback_providers' list is not parsed (see module.sh)."
  fi
  # Refresh issue #216's evidence seam on every verification pass (not
  # only on apply) - module_verify's whole purpose is re-observing current
  # reality, and #216's `omes health ai-privacy` must see a live FAIL on
  # drift rather than a stale "pass" left over from the last successful
  # apply. This is a narrow, deliberate exception to this repo's usual
  # convention that module_verify never calls state_set - the keys
  # written here are OMES's own bounded evidence state, never host
  # configuration, so refreshing them here does not blur module_check's
  # read-only/module_apply's mutating boundary.
  _hr_persist_posture_evidence "$_HR_LAST_RESULT_JSON" "$_HR_LAST_FALLBACK_CONFIGURED"
  if [[ "$rc" -ne 0 ]]; then
    log_error "hermes-restricted: DRIFT DETECTED - the restricted/local-only posture no longer holds (the configured endpoint no longer classifies as local/approved-private, or a cloud fallback became configured). Reported as a FAILURE / action-required - never a silent fallback to a cloud provider."
    return 1
  fi

  local dropin_file
  dropin_file="$(_hr_dropin_file)"
  if [[ ! -f "$dropin_file" ]]; then
    log_error "hermes-restricted: network policy drop-in ${dropin_file} is missing"
    return 1
  fi

  local props
  props="$(_hardening_systemctl "system" show "$RESTRICTED_UNIT" -p IPAddressDeny -p IPAddressAllow 2>/dev/null || true)"
  [[ -n "$props" ]] && log_info "hermes-restricted: ${props}"

  return 0
}

module_rollback() {
  log_warn "hermes-restricted: rollback removes only the OMES-managed restricted-network drop-in and this module's own state - it never touches hermes-gateway-system's own unit/drop-ins, the Hermes account, or its data"

  local dropin_dir dropin_file
  dropin_dir="$(_hr_dropin_dir)"
  dropin_file="$(_hr_dropin_file)"

  if omes_dry_run; then
    log_info "[dry-run] would remove ${dropin_file}, reload, and (with confirmation) restart ${RESTRICTED_UNIT}"
    return 0
  fi

  if [[ -f "$dropin_file" ]]; then
    rm -f "$dropin_file"
    rmdir "$dropin_dir" 2>/dev/null || true
    _hardening_systemctl "system" daemon-reload
    log_info "hermes-restricted: removed ${dropin_file}"
    if omes_confirm "Restart ${RESTRICTED_UNIT} now that the restricted-network policy has been rolled back?"; then
      _hardening_systemctl "system" restart "$RESTRICTED_UNIT" >/dev/null 2>&1 || true
    fi
  fi

  state_unset "module.hermes-restricted.target_user"
  state_unset "module.hermes-restricted.network_policy"
  state_unset "module.hermes-restricted.endpoint_classification"
  state_unset "module.hermes-restricted.destination"
  state_unset "module.hermes-restricted.decision"
  state_unset "module.hermes-restricted.reason_codes"
  state_unset "module.hermes-restricted.fallback_configured"
  state_unset "module.hermes-restricted.evaluated_at"

  # Issue #216 evidence seam: rollback removes the enforcement this
  # module provided, so the "available" observation goes back to false
  # (never left as a stale "true"/"pass" for a posture that is no longer
  # being checked or enforced).
  state_unset "ai.local_only_posture.available"
  state_unset "ai.local_only_posture.status"

  # Only clear the declared expected posture when it is still exactly
  # what this module's own module_apply set. This module is presently the
  # only OMES code that sets `ai.privacy.expected_posture=restricted_local_only`
  # automatically, but an operator can also set it directly (env var or
  # `state_set`, per docs/ai-data-privacy-and-model-security.md section
  # 10) - never clear a value this module did not set.
  local current_expected
  current_expected="$(state_get "ai.privacy.expected_posture" 2>/dev/null || printf '')"
  if [[ "$current_expected" == "restricted_local_only" ]]; then
    state_unset "ai.privacy.expected_posture"
  fi

  return 0
}

# module_doctor
# Additive, optional `omes doctor` hook: one-line summary of the last
# recorded restricted-posture decision and whether the gateway unit is
# currently active. Advisory only - does not itself gate anything;
# module_verify is the authoritative drift check.
module_doctor() {
  local decision destination classification
  decision="$(state_get "module.hermes-restricted.decision" 2>/dev/null || printf 'unknown')"
  destination="$(state_get "module.hermes-restricted.destination" 2>/dev/null || printf 'unknown')"
  classification="$(state_get "module.hermes-restricted.endpoint_classification" 2>/dev/null || printf 'unknown')"
  [[ -n "$decision" ]] || decision="unknown"
  [[ -n "$destination" ]] || destination="unknown"
  [[ -n "$classification" ]] || classification="unknown"

  local active="unknown"
  if _hardening_systemctl "system" is-active "$RESTRICTED_UNIT" >/dev/null 2>&1; then
    active="active"
  else
    active="inactive"
  fi

  printf 'restricted-posture: endpoint=%s destination=%s decision=%s unit=%s\n' "$classification" "$destination" "$decision" "$active"

  [[ "$decision" == "allow" ]]
}
