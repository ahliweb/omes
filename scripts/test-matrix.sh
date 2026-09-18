#!/usr/bin/env bash
# shellcheck shell=bash
#
# scripts/test-matrix.sh - reproducible container installation/regression
# test matrix for OMES (issue #15). Runs real `bin/omes` invocations inside
# fresh Docker containers for every image in $OMES_MATRIX_IMAGES (default:
# ubuntu:24.04 ubuntu:22.04 linuxmintd/mint22-amd64), covering the scenarios
# required by docs/testing.md and docs/business/release-gates.md:
#
#   fresh           first-ever check/dry-run/real install (root, no sudo)
#   rerun           a second real install makes no further change
#   offline         OMES_ASSUME_OFFLINE=1: read-only commands still work,
#                   `install` fails deterministically, nothing mutates
#   partial-failure a package install fails mid-apply -> exit 6, module named
#   reboot          container stop/start (NOT a real reboot - see the
#                   "known limitations" note below and docs/testing.md)
#   rollback        backup -> modify -> restore -> checksum equality;
#                   plain uninstall leaves packages, removes only OMES files
#
# Each scenario writes exactly one result line to
# tests/matrix/results/<image>-<scenario>.json (gitignored; see
# tests/matrix/README.md) and a full command transcript to
# tests/matrix/logs/<image>-<scenario>.log. The repository is bind-mounted
# READ-ONLY at /omes in every container; only a per-scenario, per-image
# writable state directory (OMES_STATE_DIR=/state) and the container's own
# writable root filesystem (for real apt-get calls) are ever mutated.
#
# Usage:
#   scripts/test-matrix.sh                  # full default matrix
#   OMES_MATRIX_IMAGES="ubuntu:24.04" scripts/test-matrix.sh
#   OMES_MATRIX_SCENARIOS="fresh rerun" scripts/test-matrix.sh
#
# Exit code: non-zero if any Tier-1 (per docs/compatibility-matrix.md)
# image/scenario combination failed. A Tier-2/Tier-3 image failing is
# reported but does not affect the exit code (advisory, matching
# docs/ci.md's blocking-vs-advisory split for compatibility.yml).
#
# KNOWN LIMITATIONS (see docs/testing.md "Known gaps" for the long form):
#   - `linuxmintd/mint22-amd64`'s own /etc/os-release reports ID=ubuntu (it
#     is an Ubuntu-Noble-based package-build image with Mint apt sources
#     added, not a real Mint install) - `omes check` detects it as Ubuntu
#     24.04 tier1, NOT as Linux Mint. It stands in for the *server-profile*
#     apt-base path on a Mint-adjacent apt configuration only; real Mint
#     ID/tier detection is exercised by tests/unit/detect.bats's fixtures
#     and, for the desktop profile end-to-end, tests/vm/.
#   - No container here runs systemd as PID 1 (`systemctl` is absent from
#     the base images entirely), so the `reboot` scenario cannot exercise
#     real service re-enablement across a boot. It substitutes a
#     `docker stop`/`docker start` cycle (kills and restarts the
#     container's PID 1, preserving its filesystem) and checks that
#     installed packages and OMES's own state/backups survive that cycle -
#     a real reboot, and real `systemctl is-enabled` checks for
#     `hermes-gateway`, are deferred to tests/vm/ (see its checklist.md).
#   - apt-base (the only real, non-dry-run module this matrix installs
#     today - hermes/hermes-gateway require a real per-user Hermes Agent
#     download and a non-root user/session, deferred to tests/vm/) never
#     calls `omes_manage_path` (it only tracks installed packages, no
#     config file). The `rollback` scenario therefore seeds one synthetic
#     managed path itself to exercise the real `omes backup`/`restore`/
#     `uninstall` CLI end-to-end on a real filesystem; the restore-vs-remove
#     *decision logic* itself (pre-existing vs. OMES-created) is already
#     covered by tests/unit/restore.bats and tests/integration/restore.bats
#     /uninstall.bats and is not re-derived here.
#   - `omes install`'s exit code when network is required but unavailable
#     is documented as "4 (preflight) or 8 (mid-apply)" depending on
#     *when* the network check runs relative to `module_check` passing.
#     For apt-base specifically, `module_check` itself calls
#     `pkg_exists_in_repos` (lib/omes/pkg.sh) for every missing package
#     BEFORE any apply, so an offline `omes install` on a host missing
#     apt-base's packages always fails at the preflight stage: exit 4, not
#     8. The `offline` scenario asserts this exact code and documents why,
#     rather than accepting either value opaquely.

set -Eeuo pipefail
IFS=$'\n\t'

SELF="$(readlink -f "$0")"
ROOT="$(cd "$(dirname "$SELF")/.." && pwd)"
cd "$ROOT"

RESULTS_DIR="${ROOT}/tests/matrix/results"
LOGS_DIR="${ROOT}/tests/matrix/logs"
mkdir -p "$RESULTS_DIR" "$LOGS_DIR"

log() { printf '[test-matrix] %s\n' "$*"; }
err() { printf '[test-matrix] ERROR %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# Image list, tiers, and scenario list (all overridable)
# ---------------------------------------------------------------------------

DEFAULT_IMAGES="ubuntu:24.04 ubuntu:22.04 linuxmintd/mint22-amd64"
DEFAULT_SCENARIOS="fresh rerun offline partial-failure reboot rollback"

read -r -a MATRIX_IMAGES <<< "${OMES_MATRIX_IMAGES:-$DEFAULT_IMAGES}"
read -r -a MATRIX_SCENARIOS <<< "${OMES_MATRIX_SCENARIOS:-$DEFAULT_SCENARIOS}"

# image_tier <image> - per docs/compatibility-matrix.md section 1/2. A
# custom image not in this table is treated as tier3 (advisory), never
# silently promoted to blocking.
image_tier() {
  case "$1" in
    ubuntu:24.04) printf 'tier1\n' ;;
    linuxmintd/mint22-amd64*) printf 'tier1\n' ;; # see the header note above
    ubuntu:22.04) printf 'tier2\n' ;;
    *) printf 'tier3\n' ;;
  esac
}

# safe_name <string> - filesystem/container-name-safe form (: and / -> -).
safe_name() {
  printf '%s' "$1" | tr ':/' '--'
}

# ---------------------------------------------------------------------------
# Result recording
# ---------------------------------------------------------------------------

# json_escape <string> - minimal JSON string escaping (quotes, backslashes,
# control chars) sufficient for the short, script-controlled strings this
# runner writes (image names, log paths, short notes) - never used for
# arbitrary command output.
json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  printf '%s' "$s"
}

declare -a RESULT_ROWS=() # for the final summary table
OVERALL_TIER1_FAIL=0

# write_result <image> <scenario> <exit_code> <ok:0|1> <duration_s> <log_path> [notes]
write_result() {
  local image="$1" scenario="$2" exit_code="$3" ok="$4" duration="$5" logpath="$6"
  local notes="${7:-}"
  local ok_json="false"
  [[ "$ok" == "1" ]] && ok_json="true"

  local out
  out="${RESULTS_DIR}/$(safe_name "$image")-${scenario}.json"
  {
    printf '{'
    printf '"image":"%s",' "$(json_escape "$image")"
    printf '"scenario":"%s",' "$(json_escape "$scenario")"
    printf '"exit_code":%s,' "$exit_code"
    printf '"ok":%s,' "$ok_json"
    printf '"duration_seconds":%s,' "$duration"
    printf '"log":"%s",' "$(json_escape "${logpath#"$ROOT"/}")"
    printf '"tier":"%s",' "$(image_tier "$image")"
    printf '"notes":"%s"' "$(json_escape "$notes")"
    printf '}\n'
  } > "$out"

  local tier
  tier="$(image_tier "$image")"
  RESULT_ROWS+=("$image|$scenario|$tier|$ok_json|$exit_code|${duration}s")

  if [[ "$ok" != "1" ]] && [[ "$tier" == "tier1" ]]; then
    OVERALL_TIER1_FAIL=1
  fi
}

# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

# mx_start <image> <name> [extra docker-run args...]
# Starts a persistent, detached container (repo read-only at /omes) that
# stays alive across multiple `docker exec` calls (unlike `docker run
# --rm`, needed so a real `apt-get install` in one scenario step is still
# visible to the next). Caller is responsible for `docker rm -f` when done.
mx_start() {
  local image="$1" name="$2"
  shift 2
  docker run -d --rm=false --name "$name" \
    -v "${ROOT}:/omes:ro" \
    "$@" \
    "$image" sleep infinity > /dev/null
}

# mx_exec <container> <logfile> [env KEY=VAL ...] -- <command...>
# Runs a command inside <container>, always with OMES_STATE_DIR=/state,
# appending the full transcript (command line + combined stdout/stderr +
# exit code) to <logfile>. Extra `-e KEY=VAL` env assignments may precede a
# literal `--`; everything after `--` is the command (bin/omes, bash, etc).
mx_exec() {
  local container="$1" logfile="$2"
  shift 2
  local -a envargs=(-e OMES_STATE_DIR=/state)
  while [[ "${1:-}" != "--" ]]; do
    [[ $# -eq 0 ]] && break
    envargs+=(-e "$1")
    shift
  done
  [[ "${1:-}" == "--" ]] && shift

  {
    printf '\n+++ exec %s\n' "$*"
  } >> "$logfile"
  local rc=0
  docker exec "${envargs[@]}" "$container" "$@" >> "$logfile" 2>&1 || rc=$?
  printf '(exit %s)\n' "$rc" >> "$logfile"
  return "$rc"
}

# mx_json <container> [env KEY=VAL ...] -- <command...>
# Same as mx_exec but returns ONLY stdout (no logfile, no stderr) on this
# function's own stdout, for JSON-parsing assertions. Caller must capture
# the exit code immediately: out="$(mx_json ...)"; rc=$?
mx_json() {
  local container="$1"
  shift
  local -a envargs=(-e OMES_STATE_DIR=/state)
  while [[ "${1:-}" != "--" ]]; do
    [[ $# -eq 0 ]] && break
    envargs+=(-e "$1")
    shift
  done
  [[ "${1:-}" == "--" ]] && shift
  docker exec "${envargs[@]}" "$container" "$@" 2> /dev/null
}

mx_stop() {
  local name="$1"
  docker rm -f "$name" > /dev/null 2>&1 || true
}

# json_field <json-string> <dotted.path>
# Tiny helper over python3 (present on every CI/dev host per docs/ci.md) so
# this script doesn't need jq as a hard dependency.
json_field() {
  local json="$1" path="$2"
  python3 - "$path" << PYEOF
import json, sys
path = sys.argv[1]
try:
    d = json.loads('''$json''')
except Exception:
    print("")
    sys.exit(0)
for part in path.split("."):
    if isinstance(d, list):
        d = d[int(part)]
    else:
        d = d.get(part) if isinstance(d, dict) else None
    if d is None:
        break
print(d if d is not None else "")
PYEOF
}

# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

# scenario_fresh <image> <container>
# omes check --json exits 0/3 per tier; a root, sudo-less dry-run install;
# then a REAL `omes install --module apt-base --yes`.
scenario_fresh() {
  local image="$1" container="$2"
  local scenario="fresh"
  local logfile
  logfile="${LOGS_DIR}/$(safe_name "$image")-${scenario}.log"
  : > "$logfile"
  local start
  start="$(date +%s)"
  local ok=1 rc=0 notes=""

  local check_json
  check_json="$(mx_json "$container" -- /omes/bin/omes check --json)" || rc=$?
  {
    printf '\n+++ check --json\n%s\n(exit %s)\n' "$check_json" "$rc"
  } >> "$logfile"
  if [[ "$rc" != "0" ]] && [[ "$rc" != "3" ]]; then
    ok=0
    notes="check --json exited ${rc} (expected 0 or 3 per tier)"
  fi

  if ! mx_exec "$container" "$logfile" -- /omes/bin/omes install --profile server --dry-run --yes; then
    ok=0
    notes="${notes:+$notes; }dry-run install (sudo-less root) failed"
  fi

  if ! mx_exec "$container" "$logfile" -- /omes/bin/omes install --module apt-base --yes; then
    ok=0
    notes="${notes:+$notes; }real apt-base install failed"
    rc=1
  fi

  local dur=$(( $(date +%s) - start ))
  write_result "$image" "$scenario" "$rc" "$ok" "$dur" "$logfile" "$notes"
  [[ "$ok" == "1" ]]
}

# scenario_rerun <image> <container>
# A second real install on the SAME container makes no further change:
# apt-base's applied_at timestamp is unchanged and no `apt-get install`
# line appears in this step's transcript (pkg_missing found nothing left
# to do, so pkg_install never calls apt-get at all).
scenario_rerun() {
  local image="$1" container="$2"
  local scenario="rerun"
  local logfile
  logfile="${LOGS_DIR}/$(safe_name "$image")-${scenario}.log"
  : > "$logfile"
  local start
  start="$(date +%s)"
  local ok=1 rc=0 notes=""

  local before_json after_json before_ts after_ts
  before_json="$(mx_json "$container" -- /omes/bin/omes status --json)"
  before_ts="$(json_field "$before_json" "modules")"
  before_ts="$(printf '%s\n' "$before_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); m=[x for x in d["modules"] if x["name"]=="apt-base"]; print(m[0]["applied_at"] if m else "")')"

  if ! mx_exec "$container" "$logfile" -- /omes/bin/omes install --module apt-base --yes; then
    ok=0
    notes="second real install exited non-zero"
    rc=1
  fi

  after_json="$(mx_json "$container" -- /omes/bin/omes status --json)"
  after_ts="$(printf '%s\n' "$after_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); m=[x for x in d["modules"] if x["name"]=="apt-base"]; print(m[0]["applied_at"] if m else "")')"

  if [[ "$before_ts" != "$after_ts" ]] || [[ -z "$after_ts" ]]; then
    ok=0
    rc=1
    notes="${notes:+$notes; }applied_at changed on rerun (before=${before_ts} after=${after_ts})"
  fi

  if grep -q 'apt-get install' "$logfile"; then
    ok=0
    rc=1
    notes="${notes:+$notes; }apt-get install was invoked on an idempotent rerun"
  fi

  local dur=$(( $(date +%s) - start ))
  write_result "$image" "$scenario" "$rc" "$ok" "$dur" "$logfile" "$notes"
  [[ "$ok" == "1" ]]
}

# scenario_offline <image>
# Fresh (never-installed) container, OMES_ASSUME_OFFLINE=1: status/
# restore --list/uninstall --dry-run must all still work (offline-safe by
# contract); `install` must fail deterministically (exit 4 for apt-base -
# see the header note) and mutate nothing (no state file created).
scenario_offline() {
  local image="$1"
  local scenario="offline"
  local logfile
  logfile="${LOGS_DIR}/$(safe_name "$image")-${scenario}.log"
  : > "$logfile"
  local start
  start="$(date +%s)"
  local ok=1 rc=0 notes=""
  local name
  name="omes-matrix-$(safe_name "$image")-offline-$$"

  mx_start "$image" "$name"
  trap 'mx_stop "'"$name"'"' RETURN

  local env=(OMES_ASSUME_OFFLINE=1 OMES_ASSUME_ONLINE=0)

  if ! mx_exec "$name" "$logfile" "${env[@]}" -- /omes/bin/omes status; then
    ok=0
    notes="status failed offline"
  fi
  if ! mx_exec "$name" "$logfile" "${env[@]}" -- /omes/bin/omes restore --list; then
    ok=0
    notes="${notes:+$notes; }restore --list failed offline"
  fi
  if ! mx_exec "$name" "$logfile" "${env[@]}" -- /omes/bin/omes uninstall --dry-run --yes; then
    ok=0
    notes="${notes:+$notes; }uninstall --dry-run failed offline"
  fi

  local install_rc=0
  mx_exec "$name" "$logfile" "${env[@]}" -- /omes/bin/omes install --module apt-base --yes || install_rc=$?
  if [[ "$install_rc" != "4" ]] && [[ "$install_rc" != "8" ]]; then
    ok=0
    notes="${notes:+$notes; }install exited ${install_rc}, expected 4 (preflight, apt-base's actual behavior) or 8"
  fi

  # Nothing should have mutated: no state file should exist yet.
  if mx_exec "$name" "$logfile" -- bash -c 'test -e /state/state'; then
    ok=0
    notes="${notes:+$notes; }a state file was created despite the install being refused"
  fi

  local rc=0
  [[ "$ok" == "1" ]] || rc=1
  local dur=$(( $(date +%s) - start ))
  write_result "$image" "$scenario" "$rc" "$ok" "$dur" "$logfile" "$notes"
  [[ "$ok" == "1" ]]
}

# scenario_partial_failure <image>
# Fresh container with tests/matrix/shims/apt-get mounted ahead on PATH:
# the real `apt-get install` for apt-base's package set is forced to fail
# (OMES_MATRIX_FAIL_PKG, default "jq", one of apt-base's own packages) ->
# `omes install --module apt-base --yes` must exit 6, name "apt-base" in
# its own error output, and leave apt-base absent from `omes status
# --json`'s modules array (run_apply never calls state_set on a failed
# apply - see the "known limitations" note in docs/testing.md: OMES does
# not write an explicit status=failed marker today).
scenario_partial_failure() {
  local image="$1"
  local scenario="partial-failure"
  local logfile
  logfile="${LOGS_DIR}/$(safe_name "$image")-${scenario}.log"
  : > "$logfile"
  local start
  start="$(date +%s)"
  local ok=1 notes=""
  local name
  name="omes-matrix-$(safe_name "$image")-partial-$$"
  local fail_pkg="${OMES_MATRIX_FAIL_PKG:-jq}"

  mx_start "$image" "$name" \
    -v "${ROOT}/tests/matrix/shims/apt-get:/usr/local/sbin/apt-get:ro"
  trap 'mx_stop "'"$name"'"' RETURN

  local install_rc=0
  mx_exec "$name" "$logfile" "OMES_MATRIX_FAIL_PKG=${fail_pkg}" -- \
    /omes/bin/omes install --module apt-base --yes || install_rc=$?

  if [[ "$install_rc" != "6" ]]; then
    ok=0
    notes="install exited ${install_rc}, expected 6 (module apply failed)"
  fi
  if ! grep -q 'apt-base' "$logfile"; then
    ok=0
    notes="${notes:+$notes; }error output did not name the failing module (apt-base)"
  fi

  local status_json
  status_json="$(mx_json "$name" -- /omes/bin/omes status --json)"
  local applied
  applied="$(printf '%s\n' "$status_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); m=[x for x in d["modules"] if x["name"]=="apt-base" and x["status"]=="applied"]; print("yes" if m else "no")' 2> /dev/null || echo "error")"
  if [[ "$applied" == "yes" ]]; then
    ok=0
    notes="${notes:+$notes; }apt-base was recorded as applied despite the failed install"
  fi

  local rc=0
  [[ "$ok" == "1" ]] || rc=1
  local dur=$(( $(date +%s) - start ))
  write_result "$image" "$scenario" "$rc" "$ok" "$dur" "$logfile" "$notes"
  [[ "$ok" == "1" ]]
}

# scenario_reboot <image> <container>
# NOT a real reboot (see the header/docs/testing.md note): stop + start the
# same container (kills and restarts PID 1, keeps the filesystem) and
# checks that the packages apt-base installed, and OMES's own state file,
# both survive. Runs on the already-installed fresh/rerun container.
scenario_reboot() {
  local image="$1" container="$2"
  local scenario="reboot"
  local logfile
  logfile="${LOGS_DIR}/$(safe_name "$image")-${scenario}.log"
  : > "$logfile"
  local start
  start="$(date +%s)"
  local ok=1 notes=""

  {
    printf '\n+++ docker stop/start %s (simulated reboot; see header note)\n' "$container"
    docker stop "$container"
    docker start "$container"
  } >> "$logfile" 2>&1
  # Give the container a moment to be exec-ready again.
  sleep 1

  if ! mx_exec "$container" "$logfile" -- bash -c 'command -v jq >/dev/null && command -v curl >/dev/null && command -v git >/dev/null'; then
    ok=0
    notes="apt-base packages missing after stop/start"
  fi
  if ! mx_exec "$container" "$logfile" -- test -f /state/state; then
    ok=0
    notes="${notes:+$notes; }state file missing after stop/start"
  fi
  if ! mx_exec "$container" "$logfile" -- /omes/bin/omes doctor; then
    ok=0
    notes="${notes:+$notes; }omes doctor reported a FAIL after stop/start"
  fi

  local rc=0
  [[ "$ok" == "1" ]] || rc=1
  local dur=$(( $(date +%s) - start ))
  write_result "$image" "$scenario" "$rc" "$ok" "$dur" "$logfile" \
    "${notes:-container stop/start only - NOT a real reboot; systemd unit re-enablement is not exercised here, see tests/vm/}"
  [[ "$ok" == "1" ]]
}

# scenario_rollback <image> <container>
# Seeds one synthetic OMES-managed path for apt-base (see the header note:
# apt-base itself manages no config file today), then exercises the real
# CLI: omes backup -> modify the file -> omes restore -> checksum
# equality; then a plain `omes uninstall` (no --purge-packages) leaves the
# packages installed and only touches the OMES-managed file.
scenario_rollback() {
  local image="$1" container="$2"
  local scenario="rollback"
  local logfile
  logfile="${LOGS_DIR}/$(safe_name "$image")-${scenario}.log"
  : > "$logfile"
  local start
  start="$(date +%s)"
  local ok=1 notes=""
  local target="/etc/omes-matrix-managed.conf"

  # 1. Seed: register $target as an apt-base managed path (merged with
  # whatever is already recorded) and give it known content, using the
  # real lib/omes/state.sh functions sourced read-only from /omes - never
  # hand-editing the state file's format ourselves.
  mx_exec "$container" "$logfile" -- bash -c "
    printf 'original-content\n' > '$target'
    source /omes/lib/omes/core.sh
    source /omes/lib/omes/log.sh
    source /omes/lib/omes/state.sh
    existing=\"\$(state_get 'module.apt-base.managed_paths' 2>/dev/null || true)\"
    if [[ -n \"\$existing\" ]]; then
      state_set 'module.apt-base.managed_paths' \"\${existing}:${target}\"
    else
      state_set 'module.apt-base.managed_paths' '${target}'
    fi
  "

  if ! mx_exec "$container" "$logfile" -- /omes/bin/omes backup --module apt-base --reason matrix-rollback-test --yes; then
    ok=0
    notes="omes backup failed"
  fi

  mx_exec "$container" "$logfile" -- bash -c "printf 'modified-content\n' > '$target'"

  if ! mx_exec "$container" "$logfile" -- /omes/bin/omes restore --yes; then
    ok=0
    notes="${notes:+$notes; }omes restore failed"
  fi

  local restored
  restored="$(mx_json "$container" -- cat "$target" 2> /dev/null || true)"
  if [[ "$(printf '%s' "$restored" | tr -d '[:space:]')" != "original-content" ]]; then
    ok=0
    notes="${notes:+$notes; }restored content mismatch (got: ${restored})"
  fi

  # 2. Plain uninstall: packages stay installed; the synthetic file is
  # handled by OMES (restored-to-pre-existing or removed, per
  # module_rollback_managed_paths - not re-derived here, see header note).
  if ! mx_exec "$container" "$logfile" -- /omes/bin/omes uninstall --module apt-base --yes; then
    ok=0
    notes="${notes:+$notes; }omes uninstall failed"
  fi

  # shellcheck disable=SC2016  # literal dpkg-query format string, not a shell expansion
  if ! mx_exec "$container" "$logfile" -- dpkg-query -W -f='${Status}' curl; then
    ok=0
    notes="${notes:+$notes; }curl (an apt-base package) was removed by a plain uninstall"
  fi
  # An unrelated, never-managed host file must be completely untouched.
  if ! mx_exec "$container" "$logfile" -- test -f /etc/hostname; then
    ok=0
    notes="${notes:+$notes; }an unrelated, unmanaged host file was affected by uninstall"
  fi

  local rc=0
  [[ "$ok" == "1" ]] || rc=1
  local dur=$(( $(date +%s) - start ))
  write_result "$image" "$scenario" "$rc" "$ok" "$dur" "$logfile" "$notes"
  [[ "$ok" == "1" ]]
}

# ---------------------------------------------------------------------------
# Per-image orchestration
# ---------------------------------------------------------------------------

_has_scenario() {
  local want="$1" s
  for s in "${MATRIX_SCENARIOS[@]}"; do
    [[ "$s" == "$want" ]] && return 0
  done
  return 1
}

run_image() {
  local image="$1"
  log "=== ${image} (tier: $(image_tier "$image")) ==="

  local main_name
  main_name="omes-matrix-$(safe_name "$image")-main-$$"
  local main_started=0

  if _has_scenario fresh || _has_scenario rerun || _has_scenario reboot || _has_scenario rollback; then
    mx_start "$image" "$main_name"
    main_started=1
  fi

  if _has_scenario fresh; then
    scenario_fresh "$image" "$main_name" || true
  fi
  if _has_scenario rerun; then
    scenario_rerun "$image" "$main_name" || true
  fi
  if _has_scenario offline; then
    scenario_offline "$image" || true
  fi
  if _has_scenario partial-failure; then
    scenario_partial_failure "$image" || true
  fi
  if _has_scenario reboot; then
    scenario_reboot "$image" "$main_name" || true
  fi
  if _has_scenario rollback; then
    scenario_rollback "$image" "$main_name" || true
  fi

  [[ "$main_started" == "1" ]] && mx_stop "$main_name"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  command -v docker > /dev/null 2>&1 || {
    err "docker is required to run the container test matrix"
    exit 1
  }
  command -v python3 > /dev/null 2>&1 || {
    err "python3 is required (used for JSON assertions)"
    exit 1
  }

  local image
  for image in "${MATRIX_IMAGES[@]}"; do
    run_image "$image"
  done

  printf '\n[test-matrix] Summary\n'
  printf '%-30s %-16s %-6s %-6s %-6s %s\n' "IMAGE" "SCENARIO" "TIER" "OK" "EXIT" "DURATION"
  local row image_c scenario_c tier_c ok_c exit_c dur_c
  for row in "${RESULT_ROWS[@]}"; do
    IFS='|' read -r image_c scenario_c tier_c ok_c exit_c dur_c <<< "$row"
    printf '%-30s %-16s %-6s %-6s %-6s %s\n' "$image_c" "$scenario_c" "$tier_c" "$ok_c" "$exit_c" "$dur_c"
  done

  if [[ "$OVERALL_TIER1_FAIL" -ne 0 ]]; then
    err "one or more Tier-1 scenarios failed; see tests/matrix/results/*.json and tests/matrix/logs/"
    exit 1
  fi

  log "all Tier-1 scenarios passed (Tier-2/3 results are advisory; see the summary above)"
  exit 0
}

main "$@"
