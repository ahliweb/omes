#!/usr/bin/env bash
# shellcheck shell=bash
# tests/run.sh - run ShellCheck + bats unit/integration tests.
#
# Uses local tools when available, falling back to Docker
# (koalaman/shellcheck:stable, bats/bats:latest) otherwise. Exits non-zero
# if any check fails.

set -Eeuo pipefail
IFS=$'\n\t'

OMES_SELF="$(readlink -f "$0")"
OMES_ROOT="$(cd "$(dirname "$OMES_SELF")/.." && pwd)"
cd "$OMES_ROOT"

log() { printf '[tests/run.sh] %s\n' "$*"; }
err() { printf '[tests/run.sh] ERROR %s\n' "$*" >&2; }

FAILED=0

# ---------------------------------------------------------------------------
# ShellCheck
# ---------------------------------------------------------------------------

mapfile -t SHELL_FILES < <(
  {
    printf '%s\n' "bin/omes" "install/bootstrap.sh" "install/preflight.sh" "tests/run.sh"
    find lib/omes modules -type f -name '*.sh' 2>/dev/null
    find tests/shims -type f 2>/dev/null
  } | sort -u
)

log "running ShellCheck (-S style) on ${#SHELL_FILES[@]} files"

if command -v shellcheck >/dev/null 2>&1; then
  if ! shellcheck -x -S style "${SHELL_FILES[@]}"; then
    err "shellcheck (local) reported issues"
    FAILED=1
  fi
elif command -v docker >/dev/null 2>&1; then
  if ! docker run --rm -v "${OMES_ROOT}:/mnt" -w /mnt koalaman/shellcheck:stable \
    -x -S style "${SHELL_FILES[@]}"; then
    err "shellcheck (docker) reported issues"
    FAILED=1
  fi
else
  err "neither shellcheck nor docker is available; cannot lint"
  FAILED=1
fi

# ---------------------------------------------------------------------------
# bats: unit + integration
# ---------------------------------------------------------------------------

BATS_IMAGE="bats/bats:latest"

# The unit/integration suites assert real (non-root) EUID behavior for
# root/user MODULE_SCOPE filtering, so bats must run as a non-root user
# inside the container (--user). bats/bats:latest is Alpine-based and has
# no python3 (needed by the JSON-validity assertions), and a non-root user
# cannot `apk add` at test time - so build a tiny derived image, once,
# that layers python3 on top of the upstream bats image.
_ensure_bats_python_image() {
  local tag="omes-bats-python3:local"
  if docker image inspect "$tag" >/dev/null 2>&1; then
    printf '%s' "$tag"
    return 0
  fi
  # This function's stdout is captured via command substitution by its
  # caller (the resolved image tag), so status messages must go to
  # stderr, never stdout - otherwise they'd corrupt the image reference.
  log "building ${tag} (bats + python3, for JSON-validity assertions)" >&2
  if printf 'FROM %s\nRUN apk add --no-cache python3\n' "$BATS_IMAGE" \
    | docker build -q -t "$tag" - >/dev/null; then
    printf '%s' "$tag"
  else
    printf '%s' "$BATS_IMAGE"
  fi
}

run_bats() {
  local suite="$1"
  log "running bats: ${suite}"
  if command -v bats >/dev/null 2>&1; then
    if ! bats "$suite"; then
      err "bats (local) failed: ${suite}"
      FAILED=1
    fi
  elif command -v docker >/dev/null 2>&1; then
    local image
    image="$(_ensure_bats_python_image)"
    if ! docker run --rm -v "${OMES_ROOT}:/code" -w /code \
      --user "$(id -u):$(id -g)" \
      "$image" "$suite"; then
      err "bats (docker) failed: ${suite}"
      FAILED=1
    fi
  else
    err "neither bats nor docker is available; cannot run ${suite}"
    FAILED=1
  fi
}

run_bats "tests/unit"
run_bats "tests/integration"

# ---------------------------------------------------------------------------
# bash -n on every shell script (fast syntax sanity check)
# ---------------------------------------------------------------------------

log "running bash -n on ${#SHELL_FILES[@]} files"
for f in "${SHELL_FILES[@]}"; do
  case "$f" in
    *.profile) continue ;;
  esac
  if ! bash -n "$f"; then
    err "bash -n failed: ${f}"
    FAILED=1
  fi
done

if [[ "$FAILED" -ne 0 ]]; then
  err "one or more checks failed"
  exit 1
fi

log "all checks passed"
exit 0
