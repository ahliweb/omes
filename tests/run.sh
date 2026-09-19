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
# no python3 (needed by the JSON-validity assertions) and no git (needed by
# tests/unit/*.bats and tests/integration/update.bats, which exercise `omes
# update`'s git fetch/ff-only-merge path - added for #14/#15, previously 7
# tests failed locally without it), and a non-root user cannot `apk add` at
# test time - so build a tiny derived image, once (cached by tag; bump
# OMES_BATS_IMAGE_TAG below if the Dockerfile ever changes and a stale local
# tag needs to be invalidated), that layers python3 and git on top of the
# upstream bats image.
OMES_BATS_IMAGE_TAG="omes-bats-python3-git:local"

_ensure_bats_python_image() {
  local tag="$OMES_BATS_IMAGE_TAG"
  if docker image inspect "$tag" >/dev/null 2>&1; then
    printf '%s' "$tag"
    return 0
  fi
  # This function's stdout is captured via command substitution by its
  # caller (the resolved image tag), so status messages must go to
  # stderr, never stdout - otherwise they'd corrupt the image reference.
  log "building ${tag} (bats + python3 + git, for JSON-validity assertions and 'omes update' tests)" >&2
  if printf 'FROM %s\nRUN apk add --no-cache python3 git\n' "$BATS_IMAGE" \
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
# Python (lib/omes/py) - stdlib-only (ADR-0012): py_compile + unittest
# ---------------------------------------------------------------------------

if ! command -v python3 >/dev/null 2>&1; then
  if [[ -d "${OMES_ROOT}/lib/omes/py" ]] || [[ -d "${OMES_ROOT}/tests/py" ]]; then
    err "python3 not available; cannot run lib/omes/py checks"
    FAILED=1
  fi
else
  if [[ -d "${OMES_ROOT}/lib/omes/py" ]]; then
    mapfile -t PY_FILES < <(find "${OMES_ROOT}/lib/omes/py" -type f -name '*.py' | sort)
    if [[ "${#PY_FILES[@]}" -gt 0 ]]; then
      log "running python3 -m py_compile on ${#PY_FILES[@]} file(s) under lib/omes/py"
      if ! python3 -m py_compile "${PY_FILES[@]}"; then
        err "python3 -m py_compile reported issues under lib/omes/py"
        FAILED=1
      fi
    fi
  fi

  if [[ -d "${OMES_ROOT}/tests/py" ]]; then
    log "running python3 -m unittest discover -s tests/py -t ."
    if ! python3 -m unittest discover -s tests/py -t .; then
      err "python3 -m unittest reported failures under tests/py"
      FAILED=1
    fi
  fi
fi

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
