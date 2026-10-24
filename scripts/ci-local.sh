#!/usr/bin/env bash
# shellcheck shell=bash
#
# scripts/ci-local.sh - run every CI job locally, without GitHub Actions
# (issue #312).
#
# Reproduces each job of .github/workflows/*.yml with the SAME command and the
# SAME blocking/advisory semantics as the workflow YAML, then prints one
# summary (a Markdown table, or JSON with --json) that can be pasted into a
# pull request description. docs/ci.md section 10 is the long form of this
# header.
#
# Usage:
#   scripts/ci-local.sh [--profile fast|full | --with-matrix]
#                       [--job NAME]... [--fail-fast | --keep-going]
#                       [--json] [--log-dir DIR]
#   scripts/ci-local.sh --list [--json]
#   scripts/ci-local.sh --install-hook | --uninstall-hook
#
# Profiles:
#   fast (default)  every lint.yml job, plus the runner's own workflow-sync check
#   full            fast + the compatibility.yml jobs (container matrix, slow)
#   (opt-in only)   graphify-real-smoke and upstream-drift run only via --job
#
# Job status: pass | fail | advisory_fail | skipped. A SKIPPED BLOCKING JOB (no
# Docker, image pull failed, ...) makes the overall result `incomplete`; it is
# never counted as a pass.
#
# Exit codes:
#   0  every blocking job passed (advisory failures and advisory skips allowed)
#   1  at least one blocking job failed
#   2  usage error (bad option, unknown job, invalid environment setting)
#   3  incomplete: no blocking job failed, but at least one was skipped
#
# Environment (all optional; behaviour is unchanged when they are unset):
#   OMES_CI_REGISTRY_MIRROR  Docker Hub mirror host (for example mirror.gcr.io).
#                            Digest-pinned images keep their digest; tag-only
#                            images are NOT digest-verified through a mirror.
#   OMES_CI_EXTRA_CA_FILE    PEM bundle mounted read-only into `docker run`
#                            containers and trusted there (TLS-intercepting
#                            proxy). Must be a COMPLETE bundle.
#   OMES_CI_DOCKER_NETWORK   network mode added to docker run/create/build
#                            (host), needed when the proxy listens on loopback.
#   OMES_CI_NONROOT_UID/GID  unprivileged identity used for the `bats` job when
#                            this script runs as root (default 65534:65534).
#   OMES_CI_WORKFLOWS_DIR    workflow directory (default .github/workflows).
#   HTTP_PROXY, HTTPS_PROXY, NO_PROXY (and lower-case forms) are passed to
#   containers by name only; their values are never printed.
#
# Never done here: GitHub API calls of its own (only the opt-in, report-only
# upstream-drift job reads public release metadata, with no token), credential
# reads, pipe-to-shell installs, writes outside the repository, the Docker image
# cache and the temp dir.
set -Eeuo pipefail

SELF="$(readlink -f "${BASH_SOURCE[0]}")"
ROOT="$(cd "$(dirname "$SELF")/.." && pwd)"

EXIT_PASS=0
EXIT_FAIL=1
EXIT_USAGE=2
EXIT_INCOMPLETE=3
JOB_SKIP_RC=77

SCHEMA="omes-ci-local/1"
HOOK_MARKER="# omes-ci-local: managed pre-push hook (scripts/ci-local.sh --install-hook)"

# gitleaks v8.30.1 (linux/amd64 + linux/arm64 index digest). The same digest is
# served by Docker Hub (zricethezav/gitleaks) and by mirror.gcr.io; the v8.30.1
# and latest tags both resolved to it on 2026-10-14. Bump it deliberately; see
# docs/ci.md section 6.
GITLEAKS_IMAGE="zricethezav/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"

WORKFLOWS_DIR="${OMES_CI_WORKFLOWS_DIR:-${ROOT}/.github/workflows}"

PROFILE="fast"
JSON=0
LIST=0
FAIL_FAST=0
KEEP_GOING=0
LOG_DIR=""
HOOK_ACTION=""
REQUESTED_JOBS=()

WORK=""
LOGS=""
SHIM_DIR=""
REDACT_SED=""
REDACT_SED_OPTS=()
NR_DIR=""
NR_USER=""
NR_UID="${OMES_CI_NONROOT_UID:-65534}"
NR_GID="${OMES_CI_NONROOT_GID:-65534}"
NR_GROUP_ARGS=(--clear-groups)
JOB_PATH="${PATH}"
JOB_DIR=""
JOB_REASON_FILE=""
CLEAN_ENV=(env)
MATRIX_ENTRIES=()
SYNC_ERRORS=0

log() { printf '[ci-local] %s\n' "$*" >&2; }
err() { printf '[ci-local] ERROR %s\n' "$*" >&2; }
die_usage() {
  err "$*"
  err "run 'scripts/ci-local.sh --help' for usage"
  exit "$EXIT_USAGE"
}

has_cmd() { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<'EOF'
usage: scripts/ci-local.sh [options]

Runs the CI jobs of .github/workflows/*.yml locally with the same commands and
the same blocking/advisory semantics. See docs/ci.md section 10.

Options:
  --profile fast|full   fast (default): lint.yml jobs. full: fast + the
                        compatibility.yml container matrix (slow, needs Docker).
  --with-matrix         alias for --profile full
  --job NAME            run only this job (repeatable, comma-separated lists
                        accepted); `matrix` selects every matrix image,
                        `matrix:ubuntu:24.04` one image. Ignores --profile.
                        Opt-in only jobs: graphify-real-smoke, upstream-drift.
  --list                list jobs (with --json: machine-readable) and exit
  --json                print the machine-readable summary on stdout; logs
                        always go to stderr
  --keep-going          run every selected job and report all of them (default)
  --fail-fast           stop after the first blocking job failure
  --log-dir DIR         keep the per-job logs in DIR (default: a temp dir that
                        is removed on exit)
  --install-hook        install an opt-in .git pre-push hook that runs
                        `scripts/ci-local.sh --profile fast` (refuses to
                        overwrite a hook that is not its own)
  --uninstall-hook      remove that hook
  -h, --help            show this help

Exit codes: 0 all blocking jobs passed; 1 a blocking job failed; 2 usage
error; 3 incomplete (a blocking job was skipped, for example no Docker).
A skipped blocking job is never counted as a pass.

Environment: OMES_CI_REGISTRY_MIRROR, OMES_CI_EXTRA_CA_FILE,
OMES_CI_DOCKER_NETWORK, OMES_CI_NONROOT_UID, OMES_CI_NONROOT_GID,
OMES_CI_WORKFLOWS_DIR (see the header of this script).
EOF
}

# ---------------------------------------------------------------------------
# Job registry (one row per workflow job; the matrix job expands per image)
#
#   kind generic   run `command` as-is (the workflow runs the same command plus
#                  workflow_suffix)
#   kind custom    run by a dedicated job_* function
#   kind internal  runner-owned check with no workflow counterpart
#   blocking       blocking | advisory | per-image (decided by the matrix row)
#   profile        fast | full | optin (only via --job)
#
# job-workflow-sync (below) fails when a workflow job has no row here, when a
# row has no workflow job, when blocking semantics differ, or when a generic
# command differs from the workflow's `run:` line. That is the drift guard.
# ---------------------------------------------------------------------------

job_table() {
  cat <<'EOF'
# name|workflow|workflow_job|profile|blocking|kind|needs|workflow_suffix|command
workflow-sync|-|-|fast|blocking|internal|none||scripts/ci-local.sh --job workflow-sync
shellcheck|lint.yml|shellcheck|fast|blocking|generic|tool:shellcheck||scripts/lint.sh shellcheck
shfmt|lint.yml|shfmt|fast|advisory|generic|tool:shfmt||scripts/lint.sh shfmt
bats|lint.yml|bats|fast|blocking|custom|-||tests/run.sh (as a non-root uid)
gitleaks|lint.yml|gitleaks|fast|blocking|custom|-||gitleaks git --redact -v (pinned image, full history)
yamllint|lint.yml|yamllint|fast|blocking|generic|tool:yamllint||scripts/lint.sh yamllint
actionlint|lint.yml|actionlint|fast|blocking|custom|-||docker run rhysd/actionlint@sha256 (digest read from lint.yml)
supply-chain|lint.yml|supply-chain|fast|blocking|generic|none||scripts/check-supply-chain.sh
check-links|lint.yml|check-links|fast|blocking|generic|python3||python3 scripts/check-links.py
check-contracts|lint.yml|check-contracts|fast|blocking|generic|python3||python3 scripts/check-contracts.py
check-architecture|lint.yml|check-architecture|fast|blocking|generic|python3||python3 scripts/check-architecture.py
check-control-center-data|lint.yml|check-control-center-data|fast|blocking|generic|python3||python3 scripts/generate-control-center-data.py --check
validate-change-fragments|lint.yml|validate-change-fragments|fast|blocking|generic|none||scripts/release.sh --validate-fragments
matrix|compatibility.yml|matrix|full|per-image|custom|-||scripts/test-matrix.sh (OMES_MATRIX_IMAGES=<image>)
real-install-ubuntu-26-04|compatibility.yml|real-install-ubuntu-26-04|full|blocking|custom|-||scripts/test-matrix.sh (images and scenarios from the workflow)
real-install-ubuntu-24-04|compatibility.yml|real-install-ubuntu-24-04|full|blocking|custom|-||scripts/test-matrix.sh (images and scenarios from the workflow)
graphify-real-smoke|compatibility.yml|graphify-real-smoke|optin|advisory|custom|-||tests/graphify/real-smoke.sh
upstream-drift|upstream-drift.yml|drift-review|optin|advisory|generic|python3| --update-issue|python3 scripts/upstream-drift.py
EOF
}

R_NAME=()
R_WF=()
R_WFJOB=()
R_PROFILE=()
R_BLOCK=()
R_KIND=()
R_NEEDS=()
R_SUFFIX=()
R_CMD=()

load_registry() {
  local name wf wfjob profile block kind needs suffix cmd
  R_NAME=() R_WF=() R_WFJOB=() R_PROFILE=() R_BLOCK=() R_KIND=() R_NEEDS=() R_SUFFIX=() R_CMD=()
  while IFS='|' read -r name wf wfjob profile block kind needs suffix cmd; do
    [[ -z "$name" || "$name" == \#* ]] && continue
    R_NAME+=("$name")
    R_WF+=("$wf")
    R_WFJOB+=("$wfjob")
    R_PROFILE+=("$profile")
    R_BLOCK+=("$block")
    R_KIND+=("$kind")
    R_NEEDS+=("$needs")
    R_SUFFIX+=("$suffix")
    R_CMD+=("$cmd")
  done < <(job_table)
}

# ---------------------------------------------------------------------------
# Workflow YAML readers (awk; the workflows are simple enough that a YAML
# parser is not needed, and ADR-0012 keeps the tooling stdlib-only)
# ---------------------------------------------------------------------------

wf_job_ids() {
  awk '
    /^jobs:[ \t]*$/ { on = 1; next }
    on && /^[^ \t#]/ { exit }
    on && /^  [A-Za-z0-9_-]+:[ \t]*(#.*)?$/ { id = $0; sub(/^  /, "", id); sub(/:.*$/, "", id); print id }
  ' "$1"
}

wf_job_block() {
  awk -v id="$2" '
    BEGIN { pat = "^  " id ":[ \t]*(#.*)?$" }
    $0 ~ pat { on = 1; print; next }
    on && /^  [A-Za-z0-9_-]+:/ { exit }
    on && /^[^ \t#]/ { exit }
    on { print }
  ' "$1"
}

# wf_block_runs: every `run:` command line of a job block on stdin (the lines of
# a `run: |` block are listed one by one), with a leading ./ stripped.
wf_block_runs() {
  awk '
    {
      if (blk) {
        if ($0 ~ /^[ \t]*$/) next
        match($0, /^[ \t]*/)
        if (RLENGTH > keycol) {
          v = $0
          sub(/^[ \t]+/, "", v)
          sub(/^\.\//, "", v)
          print v
          next
        }
        blk = 0
      }
    }
    /^[ \t]+(-[ \t]+)?run:[ \t]*/ {
      v = $0
      sub(/^[ \t]+(-[ \t]+)?run:[ \t]*/, "", v)
      if (v == "" || v ~ /^[|>]/) {
        blk = 1
        keycol = index($0, "run:") - 1
        next
      }
      sub(/[ \t]+#.*$/, "", v)
      sub(/^\.\//, "", v)
      print v
    }
  '
}

wf_block_coe() {
  awk '
    /^    continue-on-error:/ {
      v = $0
      sub(/^    continue-on-error:[ \t]*/, "", v)
      sub(/[ \t]+#.*$/, "", v)
      print v
      exit
    }
  '
}

# wf_block_name: the display name of a job block on stdin - the GitHub check-run
# name of that job (`name:` at the job level, surrounding quotes removed). Prints
# nothing when the job has no `name:` (GitHub then names the check run after the
# job id, see wf_check_name).
wf_block_name() {
  awk '
    /^    name:[ \t]*/ {
      v = $0
      sub(/^    name:[ \t]*/, "", v)
      if (v ~ /^"/) {
        sub(/^"/, "", v)
        sub(/"[ \t]*(#.*)?$/, "", v)
      } else if (v ~ /^\047/) {
        sub(/^\047/, "", v)
        sub(/\047[ \t]*(#.*)?$/, "", v)
      } else {
        sub(/[ \t]+#.*$/, "", v)
      }
      print v
      exit
    }
  '
}

# wf_check_name FILE JOB_ID [IMAGE TIER] - the check-run name GitHub gives this
# job: its `name:` (matrix.image / matrix.tier substituted for a matrix row) or
# its id when it has none. scripts/release.sh matches check runs against this
# value (issue #315); an expression that is not substituted stays literal, so
# it can never match a real check run (fail closed).
wf_check_name() {
  local file="$1" id="$2" image="${3:-}" tier="${4:-}" name
  name="$(wf_job_block "$file" "$id" | wf_block_name)"
  [[ -n "$name" ]] || name="$id"
  # shellcheck disable=SC2016 # literal GitHub expressions, not shell ones
  {
    name="${name//'${{ matrix.image }}'/$image}"
    name="${name//'${{ matrix.tier }}'/$tier}"
  }
  printf '%s' "$name"
}

# wf_block_env: KEY=VALUE for every literal OMES_* assignment in the block.
wf_block_env() {
  awk '
    /^[ \t]+OMES_[A-Z0-9_]+:[ \t]*/ {
      k = $0
      sub(/^[ \t]+/, "", k)
      v = k
      sub(/:.*$/, "", k)
      sub(/^[^:]*:[ \t]*/, "", v)
      sub(/[ \t]+#.*$/, "", v)
      gsub(/"/, "", v)
      if (v !~ /[$][{][{]/) print k "=" v
    }
  '
}

# wf_matrix_entries: image|tier|advisory for every include row of a block.
wf_matrix_entries() {
  awk '
    function flush() { if (img != "") print img "|" tier "|" adv; img = ""; tier = ""; adv = "" }
    /^[ \t]+-[ \t]+image:/ { flush(); img = $0; sub(/^[^:]*:[ \t]*/, "", img); gsub(/"/, "", img); sub(/[ \t]+#.*$/, "", img); next }
    /^[ \t]+tier:/ { tier = $0; sub(/^[^:]*:[ \t]*/, "", tier); gsub(/"/, "", tier); next }
    /^[ \t]+advisory:/ { adv = $0; sub(/^[^:]*:[ \t]*/, "", adv); sub(/[ \t]+#.*$/, "", adv); next }
    END { flush() }
  '
}

load_matrix_entries() {
  local file="${WORKFLOWS_DIR}/compatibility.yml" line
  MATRIX_ENTRIES=()
  [[ -f "$file" ]] || return 0
  while IFS= read -r line; do
    [[ -n "$line" ]] && MATRIX_ENTRIES+=("$line")
  done < <(wf_job_block "$file" matrix | wf_matrix_entries)
}

# ---------------------------------------------------------------------------
# Units (a registry row, or one matrix image) and selection
# ---------------------------------------------------------------------------

U_NAME=()
U_IDX=()
U_BLOCK=()
U_VARIANT=()
U_TIER=()
SEL=()

add_unit() {
  U_NAME+=("$1")
  U_IDX+=("$2")
  U_BLOCK+=("$3")
  U_VARIANT+=("${4:-}")
  U_TIER+=("${5:-}")
}

build_units() {
  local i entry image tier adv block
  U_NAME=() U_IDX=() U_BLOCK=() U_VARIANT=() U_TIER=()
  load_matrix_entries
  for i in "${!R_NAME[@]}"; do
    if [[ "${R_NAME[$i]}" == "matrix" ]]; then
      if ((${#MATRIX_ENTRIES[@]} == 0)); then
        add_unit "matrix" "$i" "blocking" "" ""
        continue
      fi
      for entry in "${MATRIX_ENTRIES[@]}"; do
        IFS='|' read -r image tier adv <<<"$entry"
        block="blocking"
        [[ "$adv" == "true" ]] && block="advisory"
        add_unit "matrix:${image}" "$i" "$block" "$image" "$tier"
      done
    else
      add_unit "${R_NAME[$i]}" "$i" "${R_BLOCK[$i]}" "" ""
    fi
  done
}

sel_add() {
  local u="$1" s
  for s in "${SEL[@]+"${SEL[@]}"}"; do
    [[ "$s" == "$u" ]] && return 0
  done
  SEL+=("$u")
}

select_units() {
  local u req found
  SEL=()
  if ((${#REQUESTED_JOBS[@]} > 0)); then
    for req in "${REQUESTED_JOBS[@]}"; do
      found=0
      for u in "${!U_NAME[@]}"; do
        if [[ "${U_NAME[$u]}" == "$req" || "${R_NAME[${U_IDX[$u]}]}" == "$req" ]]; then
          sel_add "$u"
          found=1
        fi
      done
      ((found)) || die_usage "unknown job '${req}' (run with --list to see the job names)"
    done
    return 0
  fi
  for u in "${!U_NAME[@]}"; do
    case "${R_PROFILE[${U_IDX[$u]}]}" in
      fast) sel_add "$u" ;;
      full) [[ "$PROFILE" == "full" ]] && sel_add "$u" ;;
    esac
  done
  return 0
}

# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  printf '%s' "$s" | tr -d '\000-\010\013\014\016-\037'
}

json_str() { printf '"%s"' "$(json_escape "$1")"; }

# ---------------------------------------------------------------------------
# Registry mirror, proxy and CA handling
# ---------------------------------------------------------------------------

# mirror_rewrite_ref REF - print REF rewritten to the Docker Hub mirror named by
# OMES_CI_REGISTRY_MIRROR (digest and tag are kept); print REF unchanged when no
# mirror is configured or REF names another registry.
mirror_rewrite_ref() {
  local ref="$1" mirror="${OMES_CI_REGISTRY_MIRROR:-}" rest first
  local re='^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*(:[A-Za-z0-9_][A-Za-z0-9._-]*)?(@sha256:[0-9a-f]{64})?$'
  mirror="${mirror%/}"
  if [[ -z "$mirror" ]]; then
    printf '%s' "$ref"
    return 0
  fi
  rest="$ref"
  rest="${rest#docker.io/}"
  rest="${rest#index.docker.io/}"
  rest="${rest#registry-1.docker.io/}"
  if [[ "$rest" == */* ]]; then
    first="${rest%%/*}"
    if [[ "$first" == *.* || "$first" == *:* || "$first" == localhost ]]; then
      printf '%s' "$ref"
      return 0
    fi
  else
    rest="library/${rest}"
  fi
  if ! [[ "$rest" =~ $re ]]; then
    printf '%s' "$ref"
    return 0
  fi
  printf '%s/%s' "$mirror" "$rest"
}

# proxy_host_is_loopback URL - true when the proxy host is a loopback address
# (a container on a bridge network cannot reach it).
proxy_host_is_loopback() {
  local v="$1" host
  v="${v#*://}"
  v="${v##*@}"
  v="${v%%/*}"
  if [[ "$v" == \[* ]]; then
    host="${v%%]*}"
    host="${host#[}"
  else
    host="${v%%:*}"
  fi
  case "$host" in
    localhost | 127.* | ::1 | 0.0.0.0) return 0 ;;
  esac
  return 1
}

# proxy_vars_to_pass - names (never values) of the proxy variables to pass into
# containers. Loopback proxies are skipped unless OMES_CI_DOCKER_NETWORK=host.
proxy_vars_to_pass() {
  local n url_set=0
  local -a out=()
  for n in HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; do
    [[ -n "${!n:-}" ]] || continue
    if proxy_host_is_loopback "${!n}" && [[ "${OMES_CI_DOCKER_NETWORK:-}" != "host" ]]; then
      continue
    fi
    out+=("$n")
    url_set=1
  done
  if ((url_set)); then
    for n in NO_PROXY no_proxy; do
      [[ -n "${!n:-}" ]] && out+=("$n")
    done
  fi
  if ((${#out[@]} > 0)); then
    printf '%s\n' "${out[@]}"
  fi
}

warn_loopback_proxies() {
  local n skipped=""
  [[ "${OMES_CI_DOCKER_NETWORK:-}" == "host" ]] && return 0
  for n in HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; do
    if [[ -n "${!n:-}" ]] && proxy_host_is_loopback "${!n}"; then
      skipped="${skipped:+${skipped} }${n}"
    fi
  done
  if [[ -n "$skipped" ]]; then
    log "not passing loopback proxy variable(s) into containers: ${skipped} (values never printed). A bridge-network container cannot reach a loopback proxy; set OMES_CI_DOCKER_NETWORK=host or use a routable proxy address."
  fi
}

# validate_environment - validate the OMES_CI_* settings; exits 2 on error.
validate_environment() {
  local m="${OMES_CI_REGISTRY_MIRROR:-}" ca="${OMES_CI_EXTRA_CA_FILE:-}" net="${OMES_CI_DOCKER_NETWORK:-}"
  local mirror_re='^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?(/[A-Za-z0-9._-]+)*$'
  local net_re='^[A-Za-z0-9][A-Za-z0-9_.-]*$'
  if [[ -n "$m" ]]; then
    m="${m%/}"
    [[ "$m" =~ $mirror_re ]] || die_usage "OMES_CI_REGISTRY_MIRROR must look like host[:port][/prefix]"
    export OMES_CI_REGISTRY_MIRROR="$m"
  fi
  if [[ -n "$net" ]]; then
    [[ "$net" =~ $net_re ]] || die_usage "OMES_CI_DOCKER_NETWORK must be a docker network name such as host"
  fi
  if [[ -n "$ca" ]]; then
    [[ -f "$ca" && -r "$ca" ]] || die_usage "OMES_CI_EXTRA_CA_FILE is not a readable file"
    ca="$(readlink -f "$ca")"
    if grep -q 'PRIVATE KEY' "$ca"; then
      die_usage "OMES_CI_EXTRA_CA_FILE contains a private key; it must be a certificate bundle only"
    fi
    grep -q 'BEGIN CERTIFICATE' "$ca" || die_usage "OMES_CI_EXTRA_CA_FILE does not look like a PEM certificate bundle"
    export OMES_CI_EXTRA_CA_FILE="$ca"
  fi
  case "${NR_UID}:${NR_GID}" in
    *[!0-9:]* | :* | *:) die_usage "OMES_CI_NONROOT_UID/GID must be numeric" ;;
  esac
  if [[ "$NR_UID" == "0" ]]; then
    die_usage "OMES_CI_NONROOT_UID must not be 0"
  fi
}

# sed_escape VALUE - escape VALUE for use in a sed s||| expression.
sed_escape() {
  printf '%s' "$1" | sed -e 's/[][\\.*^$|&/]/\\&/g'
}

# setup_redaction - write a sed script that blanks proxy URLs (and the
# host:port they name) so no log line can leak a proxy credential.
setup_redaction() {
  local n v hp escaped
  REDACT_SED="${WORK}/redact.sed"
  : >"$REDACT_SED"
  for n in HTTP_PROXY HTTPS_PROXY ALL_PROXY FTP_PROXY http_proxy https_proxy all_proxy ftp_proxy DOCKER_HTTP_PROXY DOCKER_HTTPS_PROXY; do
    v="${!n:-}"
    [[ -n "$v" ]] || continue
    escaped="$(sed_escape "$v")"
    printf 's|%s|[REDACTED]|g\n' "$escaped" >>"$REDACT_SED"
    hp="${v#*://}"
    hp="${hp##*@}"
    hp="${hp%%/*}"
    if [[ -n "$hp" && "$hp" != "$v" ]]; then
      printf 's|%s|[REDACTED]|g\n' "$(sed_escape "$hp")" >>"$REDACT_SED"
    fi
  done
  printf '%s\n' 's|://[^/@[:space:]]*:[^/@[:space:]]*@|://[REDACTED]@|g' >>"$REDACT_SED"
  # `sed -u` (unbuffered) is a GNU extension; BusyBox sed rejects it (#329).
  REDACT_SED_OPTS=()
  if sed -u -e '' </dev/null >/dev/null 2>&1; then
    REDACT_SED_OPTS=(-u)
  fi
}

redact_stream() {
  if [[ -n "$REDACT_SED" && -s "$REDACT_SED" ]]; then
    sed "${REDACT_SED_OPTS[@]}" -f "$REDACT_SED"
  else
    cat
  fi
}

# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

job_reason() {
  if [[ -n "${JOB_REASON_FILE:-}" ]]; then
    printf '%s\n' "$*" >>"$JOB_REASON_FILE"
  else
    log "$*"
  fi
}

# require_docker WHY - skip (return 77 for the caller to propagate) when Docker
# is unusable. The reason is recorded for the summary.
require_docker() {
  if ! has_cmd docker; then
    job_reason "docker not found in PATH (${1:-needed by this job})"
    return 1
  fi
  if ! docker version >/dev/null 2>&1; then
    job_reason "docker daemon not reachable (docker version failed; ${1:-needed by this job})"
    return 1
  fi
  return 0
}

# ensure_image REF - make REF available locally. With a registry mirror the
# mirror reference is pulled; a tag reference is then tagged as REF so that
# `docker run REF` finds it. A digest reference cannot be tagged, so callers
# run it through resolve_run_ref (or the docker shim, for scripts we do not
# own), which keeps the digest and so the image identity.
ensure_image() {
  local ref="$1" pull_ref pull_log hint=""
  if docker image inspect "$ref" >/dev/null 2>&1; then
    return 0
  fi
  pull_ref="$(mirror_rewrite_ref "$ref")"
  pull_log="${JOB_DIR:-${TMPDIR:-/tmp}}/pull.log"
  log "pulling ${pull_ref}"
  if docker pull "$pull_ref" >"$pull_log" 2>&1; then
    if [[ "$pull_ref" != "$ref" && "$ref" != *@sha256:* ]]; then
      if ! docker tag "$pull_ref" "$ref"; then
        job_reason "could not tag ${pull_ref} as ${ref}"
        return 1
      fi
    fi
    return 0
  fi
  redact_stream <"$pull_log" >&2
  if grep -qiE 'toomanyrequests|rate limit|429' "$pull_log"; then
    hint=" (registry rate limit; set OMES_CI_REGISTRY_MIRROR, for example mirror.gcr.io)"
  fi
  job_reason "image pull failed: ${pull_ref}${hint}"
  return 1
}

# resolve_run_ref REF - the reference to pass to `docker run`: REF itself when it
# is present locally, otherwise its mirror reference (the digest is kept).
resolve_run_ref() {
  local ref="$1"
  if docker image inspect "$ref" >/dev/null 2>&1; then
    printf '%s' "$ref"
  else
    mirror_rewrite_ref "$ref"
  fi
}

lint_image() {
  sed -n "s/^${1}=\"\\([^\"]*\\)\".*\$/\\1/p" "${ROOT}/scripts/lint.sh" | head -n1
}

tests_run_shellcheck_image() {
  grep -oE 'koalaman/shellcheck:[A-Za-z0-9._-]+' "${ROOT}/tests/run.sh" | head -n1 || true
}

tests_run_bats_image() {
  sed -n 's/^BATS_IMAGE="\([^"]*\)".*$/\1/p' "${ROOT}/tests/run.sh" | head -n1
}

tests_run_bats_tag() {
  sed -n 's/^OMES_BATS_IMAGE_TAG="\([^"]*\)".*$/\1/p' "${ROOT}/tests/run.sh" | head -n1
}

# docker_shim_needed - true when any mirror/proxy/CA/network setting asks for
# docker invocations to be adjusted.
docker_shim_needed() {
  [[ -n "${OMES_CI_REGISTRY_MIRROR:-}" || -n "${OMES_CI_EXTRA_CA_FILE:-}" || -n "${OMES_CI_DOCKER_NETWORK:-}" ]] && return 0
  [[ -n "$(proxy_vars_to_pass)" ]]
}

# write_docker_shim DIR REAL_DOCKER - write a `docker` wrapper that is put first
# on PATH for the jobs. Scripts we do not own (lint.sh, tests/run.sh,
# test-matrix.sh) call `docker`, so this is the one place that can:
#   - rewrite digest-pinned Docker Hub references to the registry mirror,
#   - pass proxy variables to `run`/`create` (-e NAME) and `build`
#     (--build-arg NAME) by NAME ONLY, so values never reach a command line,
#   - mount OMES_CI_EXTRA_CA_FILE read-only into `run`/`create` containers,
#   - add --network when OMES_CI_DOCKER_NETWORK is set and none was given.
# It forwards everything else untouched. With no setting configured the
# runner does not install it at all.
write_docker_shim() {
  local dir="$1" real="$2"
  mkdir -p "$dir"
  {
    printf '#!/usr/bin/env bash\n'
    printf '# Generated by scripts/ci-local.sh (docker wrapper for local CI); do not edit.\n'
    printf 'set -Eeuo pipefail\n'
    printf 'REAL_DOCKER=%q\n' "$real"
    declare -f mirror_rewrite_ref
    cat <<'EOF'
sub=""
prefix=1
case "${1:-}" in
  run | create) sub="run" ;;
  build) sub="build" ;;
  container)
    if [[ "${2:-}" == "run" || "${2:-}" == "create" ]]; then
      sub="run"
      prefix=2
    fi
    ;;
  image | builder | buildx)
    if [[ "${2:-}" == "build" ]]; then
      sub="build"
      prefix=2
    fi
    ;;
esac

args=()
for a in "$@"; do
  if [[ "$a" == *@sha256:* && -n "${OMES_CI_REGISTRY_MIRROR:-}" ]]; then
    m="$(mirror_rewrite_ref "$a")"
    if [[ "$m" != "$a" ]] && ! "$REAL_DOCKER" image inspect "$a" >/dev/null 2>&1; then
      a="$m"
    fi
  fi
  args+=("$a")
done

if [[ -z "$sub" ]]; then
  exec "$REAL_DOCKER" ${args[@]+"${args[@]}"}
fi

inject=()
has_net=0
for a in "${args[@]:$prefix}"; do
  case "$a" in
    --network | --network=* | --net | --net=*) has_net=1 ;;
  esac
done
if [[ "$sub" == "run" ]]; then
  for n in ${OMES_CI_SHIM_PROXY_VARS:-}; do
    inject+=(-e "$n")
  done
  if [[ -n "${OMES_CI_EXTRA_CA_FILE:-}" ]]; then
    ca_in="/usr/local/share/ca-certificates/omes-ci-extra-ca.crt"
    inject+=(-v "${OMES_CI_EXTRA_CA_FILE}:${ca_in}:ro")
    if [[ -n "${OMES_CI_SHIM_APT_CONF:-}" ]]; then
      inject+=(-v "${OMES_CI_SHIM_APT_CONF}:/etc/apt/apt.conf.d/99omes-ci-extra-ca:ro")
    fi
    for n in SSL_CERT_FILE CURL_CA_BUNDLE REQUESTS_CA_BUNDLE PIP_CERT GIT_SSL_CAINFO NODE_EXTRA_CA_CERTS; do
      inject+=(-e "${n}=${ca_in}")
    done
  fi
else
  for n in ${OMES_CI_SHIM_PROXY_VARS:-}; do
    inject+=(--build-arg "$n")
  done
fi
if [[ -n "${OMES_CI_DOCKER_NETWORK:-}" && "$has_net" == "0" ]]; then
  inject+=(--network "${OMES_CI_DOCKER_NETWORK}")
fi

exec "$REAL_DOCKER" "${args[@]:0:$prefix}" ${inject[@]+"${inject[@]}"} "${args[@]:$prefix}"
EOF
  } >"${dir}/docker"
  chmod 755 "${dir}/docker"
}

# ---------------------------------------------------------------------------
# Job execution environment
# ---------------------------------------------------------------------------

# build_clean_env - an `env -u ...` prefix that removes credentials and every
# OMES_* variable (other than OMES_CI_*) from the environment of a job, so a
# local run starts from the same clean slate as a GitHub runner.
build_clean_env() {
  local v
  CLEAN_ENV=(env)
  for v in GITHUB_TOKEN GH_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN GITLEAKS_LICENSE; do
    CLEAN_ENV+=(-u "$v")
  done
  while IFS= read -r v; do
    case "$v" in
      OMES_CI_*) ;;
      OMES_*) CLEAN_ENV+=(-u "$v") ;;
    esac
  done < <(compgen -e)
}

# job_run CMD [ARGS...] - run a command with the scrubbed environment and the
# job PATH (which carries the docker shim when one is needed).
job_run() {
  "${CLEAN_ENV[@]}" "PATH=${JOB_PATH}" "$@"
}

docker_socket_gid() {
  local host="${DOCKER_HOST:-unix:///var/run/docker.sock}" sock
  case "$host" in
    unix://*)
      sock="${host#unix://}"
      if [[ -S "$sock" ]]; then
        stat -c '%g' "$sock" 2>/dev/null || true
      fi
      ;;
  esac
}

# prepare_nonroot - (root only) create the unprivileged identity's scratch
# directory. HOME, TMPDIR and the Python bytecode cache live there so the
# repository checkout can stay root-owned and read-only.
prepare_nonroot() {
  local base d gid
  has_cmd setpriv || return 1
  NR_USER="$(getent passwd "$NR_UID" 2>/dev/null | cut -d: -f1 || true)"
  NR_USER="${NR_USER:-ci-nonroot}"
  gid="$(docker_socket_gid)"
  if [[ -n "$gid" && "$gid" != "0" ]]; then
    NR_GROUP_ARGS=("--groups=${gid}")
  else
    NR_GROUP_ARGS=(--clear-groups)
  fi
  for base in "${TMPDIR:-/tmp}" /tmp; do
    d="$(mktemp -d "${base}/omes-ci-local-nonroot.XXXXXX" 2>/dev/null)" || continue
    if ! chown "${NR_UID}:${NR_GID}" "$d" 2>/dev/null; then
      rm -rf "$d"
      continue
    fi
    chmod 700 "$d"
    if setpriv --reuid="$NR_UID" --regid="$NR_GID" --clear-groups test -w "$d" 2>/dev/null; then
      NR_DIR="$d"
      break
    fi
    rm -rf "$d"
  done
  [[ -n "$NR_DIR" ]] || return 1
  setpriv --reuid="$NR_UID" --regid="$NR_GID" --clear-groups mkdir -p "${NR_DIR}/home" "${NR_DIR}/tmp" "${NR_DIR}/pycache"
}

# nonroot_run CMD [ARGS...] - run a command as the unprivileged identity with
# the scrubbed environment.
nonroot_run() {
  setpriv --reuid="$NR_UID" --regid="$NR_GID" "${NR_GROUP_ARGS[@]}" \
    "${CLEAN_ENV[@]}" "PATH=${JOB_PATH}" "HOME=${NR_DIR}/home" "TMPDIR=${NR_DIR}/tmp" \
    "PYTHONPYCACHEPREFIX=${NR_DIR}/pycache" "USER=${NR_USER}" "LOGNAME=${NR_USER}" "$@"
}

# ---------------------------------------------------------------------------
# Jobs. Each returns 0 (pass), 77 (skipped; reason recorded) or another
# non-zero status (failed).
# ---------------------------------------------------------------------------

sync_error() {
  SYNC_ERRORS=$((SYNC_ERRORS + 1))
  err "workflow-sync: $*"
}

# custom_sync_checks NAME BLOCK RUNS - extra drift checks for custom jobs.
custom_sync_checks() {
  local name="$1" block="$2" runs="$3" env_lines
  case "$name" in
    bats)
      grep -qxF -- 'tests/run.sh' <<<"$runs" || sync_error "${name}: lint.yml no longer runs tests/run.sh"
      ;;
    gitleaks)
      grep -q 'gitleaks/gitleaks-action@' <<<"$block" || sync_error "${name}: lint.yml no longer uses gitleaks/gitleaks-action"
      ;;
    actionlint)
      grep -qE 'rhysd/actionlint@sha256:[0-9a-f]{64}' <<<"$block" || sync_error "${name}: lint.yml has no digest-pinned rhysd/actionlint image"
      ;;
    matrix)
      grep -qxF -- 'scripts/test-matrix.sh' <<<"$runs" || sync_error "${name}: compatibility.yml no longer runs scripts/test-matrix.sh"
      # shellcheck disable=SC2016 # a literal GitHub expression, not a shell one
      grep -qF 'OMES_MATRIX_IMAGES: ${{ matrix.image }}' <<<"$block" || sync_error "${name}: OMES_MATRIX_IMAGES is no longer set from matrix.image"
      ((${#MATRIX_ENTRIES[@]} > 0)) || sync_error "${name}: no include rows found in the matrix"
      ;;
    real-install-*)
      env_lines="$(wf_block_env <<<"$block")"
      grep -qxF -- 'scripts/test-matrix.sh' <<<"$runs" || sync_error "${name}: compatibility.yml no longer runs scripts/test-matrix.sh"
      grep -q '^OMES_MATRIX_IMAGES=' <<<"$env_lines" || sync_error "${name}: OMES_MATRIX_IMAGES not set in the workflow"
      ;;
    graphify-real-smoke)
      grep -qxF -- 'tests/graphify/real-smoke.sh' <<<"$runs" || sync_error "${name}: compatibility.yml no longer runs tests/graphify/real-smoke.sh"
      ;;
  esac
}

# job_workflow_sync - the drift guard (see the registry comment).
job_workflow_sync() {
  local file base id ids i found block coe expected runs want
  local -a files=()
  SYNC_ERRORS=0
  load_matrix_entries
  while IFS= read -r file; do
    files+=("$file")
  done < <(find "$WORKFLOWS_DIR" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) 2>/dev/null | sort)
  if ((${#files[@]} == 0)); then
    job_reason "no workflow files found in ${WORKFLOWS_DIR}"
    return 1
  fi

  for file in "${files[@]}"; do
    base="$(basename "$file")"
    ids="$(wf_job_ids "$file")"
    while IFS= read -r id; do
      [[ -n "$id" ]] || continue
      found=0
      for i in "${!R_NAME[@]}"; do
        if [[ "${R_WF[$i]}" == "$base" && "${R_WFJOB[$i]}" == "$id" ]]; then
          found=1
        fi
      done
      ((found)) || sync_error "${base}: job '${id}' has no local counterpart (add it to job_table in scripts/ci-local.sh)"
    done <<<"$ids"
  done

  for i in "${!R_NAME[@]}"; do
    [[ "${R_WF[$i]}" == "-" ]] && continue
    file="${WORKFLOWS_DIR}/${R_WF[$i]}"
    if [[ ! -f "$file" ]]; then
      sync_error "${R_NAME[$i]}: workflow file ${R_WF[$i]} not found"
      continue
    fi
    ids="$(wf_job_ids "$file")"
    if ! grep -qxF -- "${R_WFJOB[$i]}" <<<"$ids"; then
      sync_error "${R_NAME[$i]}: ${R_WF[$i]} has no job '${R_WFJOB[$i]}' (remove or rename the registry row)"
      continue
    fi
    block="$(wf_job_block "$file" "${R_WFJOB[$i]}")"
    coe="$(wf_block_coe <<<"$block")"
    # shellcheck disable=SC2016 # the pattern is a literal GitHub expression, not a shell one
    case "$coe" in
      true) expected="advisory" ;;
      *'${{'*) expected="per-image" ;;
      *) expected="blocking" ;;
    esac
    if [[ "${R_PROFILE[$i]}" == "optin" && "$expected" == "blocking" ]]; then
      expected="advisory"
    fi
    if [[ "${R_BLOCK[$i]}" != "$expected" ]]; then
      sync_error "${R_NAME[$i]}: workflow says ${expected} (continue-on-error: ${coe:-unset}) but scripts/ci-local.sh says ${R_BLOCK[$i]}"
    fi
    runs="$(wf_block_runs <<<"$block")"
    if [[ "${R_KIND[$i]}" == "generic" ]]; then
      want="${R_CMD[$i]}${R_SUFFIX[$i]}"
      if ! grep -qxF -- "$want" <<<"$runs"; then
        sync_error "${R_NAME[$i]}: workflow does not run '${want}' (it runs: $(tr '\n' ';' <<<"$runs"))"
      fi
    else
      custom_sync_checks "${R_NAME[$i]}" "$block" "$runs"
    fi
  done

  if ((SYNC_ERRORS > 0)); then
    job_reason "${SYNC_ERRORS} drift error(s) between ${WORKFLOWS_DIR} and scripts/ci-local.sh"
    return 1
  fi
  log "workflow-sync: every workflow job has a local counterpart with the same blocking semantics and command"
  return 0
}

# prepare_tool_or_image TOOL - lint.sh uses a local TOOL when present and
# otherwise its pinned container image; make whichever it will use available.
prepare_tool_or_image() {
  local tool="$1" var ref
  has_cmd "$tool" && return 0
  case "$tool" in
    shellcheck) var="SHELLCHECK_IMAGE" ;;
    shfmt) var="SHFMT_IMAGE" ;;
    yamllint) var="YAMLLINT_IMAGE" ;;
    *) return 0 ;;
  esac
  ref="$(lint_image "$var")"
  if [[ -z "$ref" ]]; then
    job_reason "cannot read ${var} from scripts/lint.sh"
    return 1
  fi
  require_docker "${tool} is not installed, so scripts/lint.sh uses its pinned image" || return "$JOB_SKIP_RC"
  ensure_image "$ref" || return "$JOB_SKIP_RC"
  return 0
}

job_generic() {
  local idx="$1" needs cmd rc=0
  local -a argv
  needs="${R_NEEDS[$idx]}"
  cmd="${R_CMD[$idx]}"
  case "$needs" in
    none) ;;
    python3)
      if ! has_cmd python3; then
        job_reason "python3 not found in PATH"
        return "$JOB_SKIP_RC"
      fi
      ;;
    tool:*)
      prepare_tool_or_image "${needs#tool:}" || return $?
      ;;
  esac
  read -r -a argv <<<"$cmd"
  job_run "${argv[@]}" || rc=$?
  if [[ "${R_NAME[$idx]}" == "upstream-drift" ]]; then
    case "$rc" in
      0) job_reason "report only: no --update-issue and no token; updating the tracking issue needs a manual workflow_dispatch" ;;
      2)
        job_reason "upstream inspection blocked (network or API error, or usage error; exit 2)"
        return "$JOB_SKIP_RC"
        ;;
    esac
  fi
  return "$rc"
}

# run_tests_nonroot - run tests/run.sh as an unprivileged identity. The suites
# assert non-root behaviour, and the GitHub runner is not root either.
run_tests_nonroot() {
  local need_docker="$1"
  if ! has_cmd setpriv; then
    job_reason "running as root and setpriv (util-linux) is not available to drop privileges; run this script as a non-root user"
    return "$JOB_SKIP_RC"
  fi
  if [[ -z "$NR_DIR" ]]; then
    job_reason "could not prepare a scratch directory writable by uid ${NR_UID}; run this script as a non-root user"
    return "$JOB_SKIP_RC"
  fi
  if ! nonroot_run test -r "${ROOT}/tests/run.sh" 2>/dev/null; then
    job_reason "uid ${NR_UID} cannot read ${ROOT}; relax directory permissions or run this script as a non-root user"
    return "$JOB_SKIP_RC"
  fi
  if ((need_docker)) && ! nonroot_run docker version >/dev/null 2>&1; then
    job_reason "uid ${NR_UID} cannot reach the Docker daemon (socket group: $(docker_socket_gid)); run this script as a non-root user in the docker group"
    return "$JOB_SKIP_RC"
  fi
  log "running tests/run.sh as uid ${NR_UID}:${NR_GID} (the GitHub runner is not root and the suites assert non-root behaviour)"
  nonroot_run ./tests/run.sh
}

job_bats() {
  local need_docker=0 sc_img="" bats_img="" bats_tag=""
  if ! has_cmd python3; then
    job_reason "python3 not found in PATH (tests/run.sh needs it)"
    return "$JOB_SKIP_RC"
  fi
  if ! has_cmd shellcheck; then
    need_docker=1
    sc_img="$(tests_run_shellcheck_image)"
  fi
  if ! has_cmd bats; then
    need_docker=1
    bats_img="$(tests_run_bats_image)"
    bats_tag="$(tests_run_bats_tag)"
  fi
  if ((need_docker)); then
    require_docker "tests/run.sh falls back to containers for the tool(s) that are not installed" || return "$JOB_SKIP_RC"
    if [[ -n "$sc_img" ]]; then
      ensure_image "$sc_img" || return "$JOB_SKIP_RC"
    fi
    if [[ -n "$bats_img" ]] && ! { [[ -n "$bats_tag" ]] && docker image inspect "$bats_tag" >/dev/null 2>&1; }; then
      ensure_image "$bats_img" || return "$JOB_SKIP_RC"
    fi
  fi
  if ((EUID == 0)); then
    run_tests_nonroot "$need_docker"
  else
    job_run ./tests/run.sh
  fi
}

job_gitleaks() {
  local run_ref common_dir git_dir rc=0
  local -a mounts
  require_docker "gitleaks runs from a pinned container image" || return "$JOB_SKIP_RC"
  if ! has_cmd git || ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    job_reason "not a git checkout: gitleaks needs the full git history"
    return "$JOB_SKIP_RC"
  fi
  if [[ "$(git -C "$ROOT" rev-parse --is-shallow-repository 2>/dev/null || true)" == "true" ]]; then
    job_reason "shallow clone: gitleaks must scan the full history (run: git fetch --unshallow)"
    return "$JOB_SKIP_RC"
  fi
  ensure_image "$GITLEAKS_IMAGE" || return "$JOB_SKIP_RC"
  run_ref="$(resolve_run_ref "$GITLEAKS_IMAGE")"
  mounts=(-v "${ROOT}:/repo:ro")
  if [[ -f "${ROOT}/.git" ]]; then
    # Linked worktree: .git is a file pointing into the main repository's git
    # directory, which must be visible at the same absolute path.
    common_dir="$(cd "$ROOT" && readlink -f "$(git rev-parse --git-common-dir)")"
    git_dir="$(cd "$ROOT" && git rev-parse --absolute-git-dir)"
    mounts+=(-v "${common_dir}:${common_dir}:ro")
    if [[ "$git_dir" != "$common_dir"/* ]]; then
      mounts+=(-v "${git_dir}:${git_dir}:ro")
    fi
  fi
  # Same detection as gitleaks/gitleaks-action (redacted, verbose, exit code 2 on
  # leaks) but over every commit, with the repository's own .gitleaks.toml. No
  # token or license is passed; the SARIF artifact upload is not reproduced.
  job_run docker run --rm --user "$(id -u):$(id -g)" \
    -e HOME=/tmp -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e "GIT_CONFIG_VALUE_0=*" \
    "${mounts[@]}" -w /repo "$run_ref" \
    git --no-banner --no-color --redact -v --exit-code=2 --config /repo/.gitleaks.toml /repo || rc=$?
  case "$rc" in
    0) ;;
    2) job_reason "gitleaks reported potential secrets (values redacted in the log)" ;;
    *) job_reason "gitleaks exited ${rc}" ;;
  esac
  return "$rc"
}

job_actionlint() {
  local ref run_ref
  ref="$(grep -oE 'rhysd/actionlint@sha256:[0-9a-f]{64}' "${WORKFLOWS_DIR}/lint.yml" 2>/dev/null | head -n1 || true)"
  if [[ -z "$ref" ]]; then
    job_reason "no digest-pinned rhysd/actionlint image found in lint.yml"
    return 1
  fi
  require_docker "actionlint runs from the image pinned in lint.yml" || return "$JOB_SKIP_RC"
  ensure_image "$ref" || return "$JOB_SKIP_RC"
  run_ref="$(resolve_run_ref "$ref")"
  # lint.yml: docker run --rm -v "$PWD:/repo" -w /repo <image> -color
  # (-color only forces ANSI colours for the GitHub log viewer).
  job_run docker run --rm -v "${ROOT}:/repo:ro" -w /repo "$run_ref"
}

# run_test_matrix IMAGE [SCENARIOS] - scripts/test-matrix.sh for one image.
run_test_matrix() {
  local image="$1" scenarios="${2:-}"
  local -a envs=("OMES_MATRIX_IMAGES=${image}")
  if ! has_cmd python3; then
    job_reason "python3 not found in PATH (scripts/test-matrix.sh needs it)"
    return "$JOB_SKIP_RC"
  fi
  require_docker "scripts/test-matrix.sh runs disposable containers" || return "$JOB_SKIP_RC"
  ensure_image "$image" || return "$JOB_SKIP_RC"
  [[ -n "$scenarios" ]] && envs+=("OMES_MATRIX_SCENARIOS=${scenarios}")
  job_run "${envs[@]}" ./scripts/test-matrix.sh
}

job_matrix() {
  local u="$1" image
  image="${U_VARIANT[$u]}"
  if [[ -z "$image" ]]; then
    job_reason "no include rows found in the matrix of compatibility.yml"
    return 1
  fi
  run_test_matrix "$image"
}

job_real_install() {
  local idx="$1" block line image="" scenarios=""
  block="$(wf_job_block "${WORKFLOWS_DIR}/compatibility.yml" "${R_WFJOB[$idx]}")"
  while IFS= read -r line; do
    case "$line" in
      OMES_MATRIX_IMAGES=*) image="${line#OMES_MATRIX_IMAGES=}" ;;
      OMES_MATRIX_SCENARIOS=*) scenarios="${line#OMES_MATRIX_SCENARIOS=}" ;;
    esac
  done < <(wf_block_env <<<"$block")
  if [[ -z "$image" ]]; then
    job_reason "OMES_MATRIX_IMAGES not found for ${R_WFJOB[$idx]} in compatibility.yml"
    return 1
  fi
  run_test_matrix "$image" "$scenarios"
}

job_graphify_smoke() {
  local img
  require_docker "tests/graphify/real-smoke.sh runs a throwaway container" || return "$JOB_SKIP_RC"
  # shellcheck disable=SC2016 # the sed expression matches a literal ${...} in real-smoke.sh
  img="$(sed -n 's/^IMAGE="\${OMES_GRAPHIFY_SMOKE_IMAGE:-\([^}]*\)}".*$/\1/p' "${ROOT}/tests/graphify/real-smoke.sh" | head -n1)"
  if [[ -n "$img" ]]; then
    ensure_image "$img" || return "$JOB_SKIP_RC"
  fi
  job_run ./tests/graphify/real-smoke.sh
}

dispatch_job() {
  local u="$1" idx
  idx="${U_IDX[$u]}"
  case "${R_NAME[$idx]}" in
    workflow-sync) job_workflow_sync ;;
    bats) job_bats ;;
    gitleaks) job_gitleaks ;;
    actionlint) job_actionlint ;;
    matrix) job_matrix "$u" ;;
    real-install-*) job_real_install "$idx" ;;
    graphify-real-smoke) job_graphify_smoke ;;
    *) job_generic "$idx" ;;
  esac
}

# classify_env_failure NAME LOG - print a reason when a failed job's output
# shows an ENVIRONMENT problem (rate limit, Docker daemon, missing tools in a
# container image) rather than a code failure; print nothing otherwise.
classify_env_failure() {
  local name="$1" logfile="$2"
  case "$name" in
    bats)
      if grep -qE '^# .*: (git|python3): command not found' "$logfile"; then
        printf '%s' "environment: the bats container image lacks git/python3 (the derived image could not be built, usually because its package mirror is unreachable); install bats natively or pre-build the image"
        return 0
      fi
      ;;
  esac
  case "$name" in
    bats | shellcheck | shfmt | yamllint | gitleaks | actionlint | matrix* | real-install-* | graphify-real-smoke)
      if grep -qiE 'toomanyrequests|pull rate limit|429 Too Many Requests' "$logfile"; then
        printf '%s' "environment: registry rate limit while pulling an image (set OMES_CI_REGISTRY_MIRROR, for example mirror.gcr.io)"
      elif grep -qE 'Cannot connect to the Docker daemon|permission denied while trying to connect to the [Dd]ocker daemon' "$logfile"; then
        printf '%s' "environment: the Docker daemon is not reachable"
      fi
      ;;
  esac
}

# ---------------------------------------------------------------------------
# Running the selected jobs
# ---------------------------------------------------------------------------

RES_NAME=()
RES_WF=()
RES_WFJOB=()
RES_BLOCK=()
RES_STATUS=()
RES_REASON=()
RES_DUR=()
RES_CMD=()
RES_PROFILE=()

record_result() {
  RES_NAME+=("$1")
  RES_WF+=("$2")
  RES_WFJOB+=("$3")
  RES_BLOCK+=("$4")
  RES_STATUS+=("$5")
  RES_REASON+=("$6")
  RES_DUR+=("$7")
  RES_CMD+=("$8")
  RES_PROFILE+=("$9")
}

run_unit() {
  local u="$1" idx name block safe jobdir logfile rcfile start dur rc status reason env_reason
  idx="${U_IDX[$u]}"
  name="${U_NAME[$u]}"
  block="${U_BLOCK[$u]}"
  safe="${name//[^A-Za-z0-9._-]/_}"
  jobdir="${WORK}/jobs/${safe}"
  mkdir -p "$jobdir"
  logfile="${LOGS}/${safe}.log"
  rcfile="${jobdir}/rc"
  JOB_DIR="$jobdir"
  JOB_REASON_FILE="${jobdir}/reason"
  : >"$JOB_REASON_FILE"
  : >"$logfile"
  rm -f "$rcfile"

  log "=== ${name} (${block}) ==="
  start=$SECONDS
  {
    (
      set +e
      cd "$ROOT"
      dispatch_job "$u"
      printf '%s' "$?" >"$rcfile"
    ) 2>&1
  } | redact_stream | tee "$logfile" >&2 || true
  dur=$((SECONDS - start))

  rc=1
  if [[ -s "$rcfile" ]]; then
    rc="$(<"$rcfile")"
  fi
  reason="$(tr '\n' ' ' <"$JOB_REASON_FILE")"
  reason="${reason% }"

  if ((rc == 0)); then
    status="pass"
  elif ((rc == JOB_SKIP_RC)); then
    status="skipped"
    [[ -n "$reason" ]] || reason="skipped"
  else
    env_reason="$(classify_env_failure "$name" "$logfile")"
    if [[ -n "$env_reason" ]]; then
      status="skipped"
      reason="$env_reason"
    elif [[ "$block" == "blocking" ]]; then
      status="fail"
      [[ -n "$reason" ]] || reason="exit status ${rc}"
    else
      status="advisory_fail"
      [[ -n "$reason" ]] || reason="exit status ${rc} (advisory, does not block)"
    fi
  fi
  log "--- ${name}: ${status} in ${dur}s${reason:+ (${reason})}"
  record_result "$name" "${R_WF[$idx]}" "${R_WFJOB[$idx]}" "$block" "$status" "$reason" "$dur" "${R_CMD[$idx]}" "${R_PROFILE[$idx]}"
}

RESULT=""
EXIT_CODE=0
N_PASS=0
N_FAIL=0
N_ADVISORY=0
N_SKIPPED=0
N_BLOCKING_SKIPPED=0
TOTAL_DUR=0
GIT_HEAD="unknown"
GIT_DIRTY="unknown"
GIT_SHALLOW="unknown"

compute_overall() {
  local i
  N_PASS=0 N_FAIL=0 N_ADVISORY=0 N_SKIPPED=0 N_BLOCKING_SKIPPED=0
  for i in "${!RES_NAME[@]}"; do
    case "${RES_STATUS[$i]}" in
      pass) N_PASS=$((N_PASS + 1)) ;;
      fail) N_FAIL=$((N_FAIL + 1)) ;;
      advisory_fail) N_ADVISORY=$((N_ADVISORY + 1)) ;;
      skipped)
        N_SKIPPED=$((N_SKIPPED + 1))
        [[ "${RES_BLOCK[$i]}" == "blocking" ]] && N_BLOCKING_SKIPPED=$((N_BLOCKING_SKIPPED + 1))
        ;;
    esac
  done
  if ((N_FAIL > 0)); then
    RESULT="fail"
    EXIT_CODE=$EXIT_FAIL
  elif ((N_BLOCKING_SKIPPED > 0)); then
    RESULT="incomplete"
    EXIT_CODE=$EXIT_INCOMPLETE
  else
    RESULT="pass"
    EXIT_CODE=$EXIT_PASS
  fi
}

collect_git_info() {
  local dirty
  if has_cmd git && git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    GIT_HEAD="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || printf 'unknown')"
    if dirty="$(git -C "$ROOT" status --porcelain 2>/dev/null)"; then
      if [[ -n "$dirty" ]]; then GIT_DIRTY="true"; else GIT_DIRTY="false"; fi
    fi
    GIT_SHALLOW="$(git -C "$ROOT" rev-parse --is-shallow-repository 2>/dev/null || printf 'unknown')"
  fi
}

selection_label() {
  if ((${#REQUESTED_JOBS[@]} > 0)); then
    printf 'jobs: %s' "${REQUESTED_JOBS[*]}"
  else
    printf 'profile: %s' "$PROFILE"
  fi
}

print_human_summary() {
  local i reason
  printf '## Local CI summary (scripts/ci-local.sh)\n\n'
  printf -- '- result: **%s** (exit %s)\n' "$RESULT" "$EXIT_CODE"
  printf -- '- %s, commit: %s (%s), shallow: %s\n' "$(selection_label)" "${GIT_HEAD:0:12}" "$([[ "$GIT_DIRTY" == "true" ]] && echo "dirty working tree" || echo "clean tree")" "$GIT_SHALLOW"
  printf -- '- pass: %s, fail: %s, advisory_fail: %s, skipped: %s (blocking skipped: %s), duration: %ss\n' \
    "$N_PASS" "$N_FAIL" "$N_ADVISORY" "$N_SKIPPED" "$N_BLOCKING_SKIPPED" "$TOTAL_DUR"
  if [[ -n "${OMES_CI_REGISTRY_MIRROR:-}" ]]; then
    printf -- '- registry mirror: %s (tag-only images are not digest-verified through a mirror)\n' "$OMES_CI_REGISTRY_MIRROR"
  fi
  printf '\n| job | blocking | status | seconds | note |\n|---|---|---|---|---|\n'
  for i in "${!RES_NAME[@]}"; do
    reason="${RES_REASON[$i]//|/\\|}"
    printf '| %s | %s | %s | %s | %s |\n' "${RES_NAME[$i]}" "${RES_BLOCK[$i]}" "${RES_STATUS[$i]}" "${RES_DUR[$i]}" "$reason"
  done
  printf '\n'
  case "$RESULT" in
    incomplete) printf 'Result is INCOMPLETE: a blocking job was skipped, so this run is not a pass.\n' ;;
    fail) printf 'Result is FAIL: at least one blocking job failed.\n' ;;
  esac
}

print_json_summary() {
  local i first=1 n first_name=1
  printf '{\n'
  printf '  "schema": %s,\n' "$(json_str "$SCHEMA")"
  printf '  "result": %s,\n' "$(json_str "$RESULT")"
  printf '  "exit_code": %s,\n' "$EXIT_CODE"
  printf '  "generated_at": %s,\n' "$(json_str "$(date -u +%Y-%m-%dT%H:%M:%SZ)")"
  if ((${#REQUESTED_JOBS[@]} > 0)); then
    printf '  "profile": "custom",\n'
  else
    printf '  "profile": %s,\n' "$(json_str "$PROFILE")"
  fi
  printf '  "fail_fast": %s,\n' "$([[ "$FAIL_FAST" == "1" ]] && echo true || echo false)"
  printf '  "duration_seconds": %s,\n' "$TOTAL_DUR"
  printf '  "git": {"head": %s, "dirty": %s, "shallow": %s},\n' "$(json_str "$GIT_HEAD")" \
    "$([[ "$GIT_DIRTY" == "unknown" ]] && echo null || echo "$GIT_DIRTY")" \
    "$([[ "$GIT_SHALLOW" == "unknown" ]] && echo null || echo "$GIT_SHALLOW")"
  printf '  "mirror": %s,\n' "$([[ -n "${OMES_CI_REGISTRY_MIRROR:-}" ]] && json_str "$OMES_CI_REGISTRY_MIRROR" || echo null)"
  printf '  "extra_ca": %s,\n' "$([[ -n "${OMES_CI_EXTRA_CA_FILE:-}" ]] && echo true || echo false)"
  printf '  "proxy_variables_passed": ['
  while IFS= read -r n; do
    [[ -n "$n" ]] || continue
    if ((first_name)); then first_name=0; else printf ', '; fi
    json_str "$n"
  done < <(proxy_vars_to_pass)
  printf '],\n'
  printf '  "counts": {"pass": %s, "fail": %s, "advisory_fail": %s, "skipped": %s, "blocking_skipped": %s},\n' \
    "$N_PASS" "$N_FAIL" "$N_ADVISORY" "$N_SKIPPED" "$N_BLOCKING_SKIPPED"
  printf '  "jobs": [\n'
  for i in "${!RES_NAME[@]}"; do
    if ((first)); then first=0; else printf ',\n'; fi
    printf '    {"name": %s, "workflow": %s, "workflow_job": %s, "blocking": %s, "status": %s, "reason": %s, "duration_seconds": %s, "command": %s}' \
      "$(json_str "${RES_NAME[$i]}")" "$(json_str "${RES_WF[$i]}")" "$(json_str "${RES_WFJOB[$i]}")" \
      "$([[ "${RES_BLOCK[$i]}" == "blocking" ]] && echo true || echo false)" \
      "$(json_str "${RES_STATUS[$i]}")" "$(json_str "${RES_REASON[$i]}")" "${RES_DUR[$i]}" "$(json_str "${RES_CMD[$i]}")"
  done
  printf '\n  ]\n}\n'
}

print_list() {
  local u idx first=1 sel s check_name
  if ((JSON)); then
    printf '{\n  "schema": %s,\n  "jobs": [\n' "$(json_str "$SCHEMA")"
    for u in "${!U_NAME[@]}"; do
      idx="${U_IDX[$u]}"
      sel=false
      for s in "${SEL[@]+"${SEL[@]}"}"; do [[ "$s" == "$u" ]] && sel=true; done
      if ((first)); then first=0; else printf ',\n'; fi
      check_name=null
      if [[ "${R_WF[$idx]}" != "-" && -f "${WORKFLOWS_DIR}/${R_WF[$idx]}" ]]; then
        check_name="$(json_str "$(wf_check_name "${WORKFLOWS_DIR}/${R_WF[$idx]}" "${R_WFJOB[$idx]}" "${U_VARIANT[$u]}" "${U_TIER[$u]}")")"
      fi
      printf '    {"name": %s, "workflow": %s, "workflow_job": %s, "check_name": %s, "profile": %s, "blocking": %s, "selected": %s, "image": %s, "command": %s}' \
        "$(json_str "${U_NAME[$u]}")" "$(json_str "${R_WF[$idx]}")" "$(json_str "${R_WFJOB[$idx]}")" "$check_name" \
        "$(json_str "${R_PROFILE[$idx]}")" "$([[ "${U_BLOCK[$u]}" == "blocking" ]] && echo true || echo false)" "$sel" \
        "$([[ -n "${U_VARIANT[$u]}" ]] && json_str "${U_VARIANT[$u]}" || echo null)" "$(json_str "${R_CMD[$idx]}")"
    done
    printf '\n  ]\n}\n'
    return 0
  fi
  printf '%-32s %-19s %-8s %-9s %s\n' "JOB" "WORKFLOW" "PROFILE" "BLOCKING" "COMMAND"
  for u in "${!U_NAME[@]}"; do
    idx="${U_IDX[$u]}"
    printf '%-32s %-19s %-8s %-9s %s\n' "${U_NAME[$u]}" "${R_WF[$idx]}" "${R_PROFILE[$idx]}" "${U_BLOCK[$u]}" "${R_CMD[$idx]}"
  done
  printf '\nprofile fast = lint.yml jobs + workflow-sync; full adds compatibility.yml; optin jobs run only with --job.\n'
}

# ---------------------------------------------------------------------------
# Opt-in pre-push hook
# ---------------------------------------------------------------------------

hook_path() {
  local p
  p="$(git -C "$ROOT" rev-parse --git-path hooks/pre-push)" || return 1
  [[ "$p" == /* ]] || p="${ROOT}/${p}"
  printf '%s' "$p"
}

install_hook() {
  local hook tmp
  has_cmd git || die_usage "git is required to install the hook"
  hook="$(hook_path)" || die_usage "not a git checkout: cannot locate .git/hooks"
  if [[ -e "$hook" ]] && ! grep -qF -- "$HOOK_MARKER" "$hook"; then
    err "refusing to overwrite ${hook}: it is not an OMES local-CI hook (remove or merge it by hand)"
    return 1
  fi
  mkdir -p "$(dirname "$hook")"
  tmp="$(mktemp "${hook}.XXXXXX")"
  cat >"$tmp" <<EOF
#!/usr/bin/env bash
${HOOK_MARKER}
# Runs the fast local CI profile before every push. Bypass once with
# \`git push --no-verify\`; remove with \`scripts/ci-local.sh --uninstall-hook\`.
set -Eeuo pipefail
root="\$(git rev-parse --show-toplevel)"
if [[ ! -x "\${root}/scripts/ci-local.sh" ]]; then
  echo "pre-push: scripts/ci-local.sh not found in this checkout; skipping local CI" >&2
  exit 0
fi
exec "\${root}/scripts/ci-local.sh" --profile fast
EOF
  chmod 755 "$tmp"
  mv -f "$tmp" "$hook"
  log "installed ${hook} (runs: scripts/ci-local.sh --profile fast)"
}

uninstall_hook() {
  local hook
  has_cmd git || die_usage "git is required to remove the hook"
  hook="$(hook_path)" || die_usage "not a git checkout: cannot locate .git/hooks"
  if [[ ! -e "$hook" ]]; then
    log "no pre-push hook installed at ${hook}"
    return 0
  fi
  if ! grep -qF -- "$HOOK_MARKER" "$hook"; then
    err "refusing to remove ${hook}: it is not an OMES local-CI hook"
    return 1
  fi
  rm -f "$hook"
  log "removed ${hook}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

add_requested() {
  local v="$1" item
  [[ -n "$v" ]] || die_usage "--job needs a non-empty job name"
  while [[ -n "$v" ]]; do
    item="${v%%,*}"
    if [[ "$v" == *,* ]]; then v="${v#*,}"; else v=""; fi
    [[ -n "$item" ]] && REQUESTED_JOBS+=("$item")
  done
}

parse_args() {
  while (($# > 0)); do
    case "$1" in
      -h | --help)
        usage
        exit 0
        ;;
      --list) LIST=1 ;;
      --json) JSON=1 ;;
      --keep-going) KEEP_GOING=1 ;;
      --fail-fast) FAIL_FAST=1 ;;
      --with-matrix) PROFILE="full" ;;
      --install-hook) HOOK_ACTION="install" ;;
      --uninstall-hook) HOOK_ACTION="uninstall" ;;
      --profile)
        shift
        (($# > 0)) || die_usage "--profile needs a value (fast or full)"
        PROFILE="$1"
        ;;
      --profile=*) PROFILE="${1#--profile=}" ;;
      --job)
        shift
        (($# > 0)) || die_usage "--job needs a job name"
        add_requested "$1"
        ;;
      --job=*) add_requested "${1#--job=}" ;;
      --log-dir)
        shift
        (($# > 0)) || die_usage "--log-dir needs a directory"
        LOG_DIR="$1"
        ;;
      --log-dir=*) LOG_DIR="${1#--log-dir=}" ;;
      *) die_usage "unknown option: $1" ;;
    esac
    shift
  done
  case "$PROFILE" in
    fast | full) ;;
    *) die_usage "invalid --profile '${PROFILE}' (expected fast or full)" ;;
  esac
  if ((KEEP_GOING && FAIL_FAST)); then
    die_usage "--keep-going and --fail-fast are mutually exclusive"
  fi
  if [[ -n "$HOOK_ACTION" ]] && ((LIST || JSON || ${#REQUESTED_JOBS[@]} > 0 || FAIL_FAST || KEEP_GOING)); then
    die_usage "--install-hook/--uninstall-hook cannot be combined with job or output options"
  fi
}

cleanup() {
  if [[ -n "$NR_DIR" ]]; then rm -rf "$NR_DIR"; fi
  if [[ -n "$WORK" ]]; then rm -rf "$WORK"; fi
}

# absolutize_paths - make the user-supplied paths absolute relative to the
# directory the script was started from, before main changes into the repository.
absolutize_paths() {
  if [[ -n "$LOG_DIR" && "$LOG_DIR" != /* ]]; then
    LOG_DIR="${PWD}/${LOG_DIR}"
  fi
  if [[ "$WORKFLOWS_DIR" != /* ]]; then
    WORKFLOWS_DIR="${PWD}/${WORKFLOWS_DIR}"
  fi
  if [[ -n "${OMES_CI_EXTRA_CA_FILE:-}" && "${OMES_CI_EXTRA_CA_FILE}" != /* ]]; then
    OMES_CI_EXTRA_CA_FILE="${PWD}/${OMES_CI_EXTRA_CA_FILE}"
    export OMES_CI_EXTRA_CA_FILE
  fi
}

main() {
  local u real_docker start_all stop_after=""
  parse_args "$@"
  absolutize_paths
  cd "$ROOT"
  load_registry

  case "$HOOK_ACTION" in
    install)
      install_hook
      return $?
      ;;
    uninstall)
      uninstall_hook
      return $?
      ;;
  esac

  build_units
  select_units
  if ((LIST)); then
    print_list
    return 0
  fi

  validate_environment
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/omes-ci-local.XXXXXX")"
  trap cleanup EXIT
  if [[ -n "$LOG_DIR" ]]; then
    mkdir -p "$LOG_DIR"
    LOGS="$(cd "$LOG_DIR" && pwd)"
  else
    LOGS="${WORK}/logs"
    mkdir -p "$LOGS"
  fi
  mkdir -p "${WORK}/jobs"

  setup_redaction
  build_clean_env
  warn_loopback_proxies
  if docker_shim_needed && has_cmd docker; then
    real_docker="$(command -v docker)"
    SHIM_DIR="${WORK}/shim"
    write_docker_shim "$SHIM_DIR" "$real_docker"
    JOB_PATH="${SHIM_DIR}:${PATH}"
    OMES_CI_SHIM_PROXY_VARS="$(proxy_vars_to_pass | tr '\n' ' ')"
    export OMES_CI_SHIM_PROXY_VARS
    if [[ -n "${OMES_CI_EXTRA_CA_FILE:-}" ]]; then
      OMES_CI_SHIM_APT_CONF="${WORK}/apt-extra-ca.conf"
      printf 'Acquire::https::CaInfo "/usr/local/share/ca-certificates/omes-ci-extra-ca.crt";\n' >"$OMES_CI_SHIM_APT_CONF"
      chmod 644 "$OMES_CI_SHIM_APT_CONF"
      export OMES_CI_SHIM_APT_CONF
    fi
    OMES_CI_SHIM_PROXY_VARS="${OMES_CI_SHIM_PROXY_VARS% }"
    log "docker wrapper active (mirror: ${OMES_CI_REGISTRY_MIRROR:-none}, proxy variables: ${OMES_CI_SHIM_PROXY_VARS:-none}, extra CA: $([[ -n "${OMES_CI_EXTRA_CA_FILE:-}" ]] && echo yes || echo no), network: ${OMES_CI_DOCKER_NETWORK:-default})"
  fi
  if [[ -n "${OMES_CI_REGISTRY_MIRROR:-}" ]]; then
    log "registry mirror ${OMES_CI_REGISTRY_MIRROR}: digest-pinned images keep their digest; tag-only images are not digest-verified"
  fi

  for u in "${SEL[@]}"; do
    if [[ "${R_NAME[${U_IDX[$u]}]}" == "bats" ]] && ((EUID == 0)); then
      prepare_nonroot || log "could not prepare the non-root scratch directory; the bats job will be skipped"
      break
    fi
  done

  collect_git_info
  start_all=$SECONDS
  for u in "${SEL[@]}"; do
    if [[ -n "$stop_after" ]]; then
      record_result "${U_NAME[$u]}" "${R_WF[${U_IDX[$u]}]}" "${R_WFJOB[${U_IDX[$u]}]}" "${U_BLOCK[$u]}" "skipped" \
        "not run: --fail-fast after ${stop_after} failed" 0 "${R_CMD[${U_IDX[$u]}]}" "${R_PROFILE[${U_IDX[$u]}]}"
      continue
    fi
    run_unit "$u"
    if ((FAIL_FAST)) && [[ "${RES_STATUS[$((${#RES_STATUS[@]} - 1))]}" == "fail" ]]; then
      stop_after="${U_NAME[$u]}"
    fi
  done
  TOTAL_DUR=$((SECONDS - start_all))

  compute_overall
  if ((JSON)); then
    print_json_summary
  else
    print_human_summary
  fi
  return "$EXIT_CODE"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
