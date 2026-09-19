#!/usr/bin/env bash
# shellcheck shell=bash
#
# tests/graphify/real-smoke.sh - OPT-IN real smoke test for the Graphify
# integration (issue #56). Installs the REAL `graphifyy` package (not a
# shim) inside a throwaway `python:3.12-slim` container and runs a real
# `graphify --version` / `graphify extract --code-only` against
# tests/fixtures/graphify/sample-repo (synthetic, no private content).
#
# This is NEVER run by `tests/run.sh` or by default PR CI - it makes a
# real network call to PyPI to install graphifyy, which docs/graphify.md
# §1.5/§1.4 and docs/graphify-privacy.md require every OMES-triggered
# code-only invocation to avoid needing by default. It exists so a human
# (or a weekly/on-demand CI job - see .github/workflows/compatibility.yml)
# can re-verify, on a real schedule, that upstream's CLI surface has not
# drifted from what docs/graphify.md/docs/graphify-privacy.md document.
#
# Usage:
#   tests/graphify/real-smoke.sh
#   OMES_GRAPHIFY_SMOKE_IMAGE=python:3.12-slim tests/graphify/real-smoke.sh
#
# Requires: docker.
#
# Records the installed graphify version and a short pass/fail summary to
# stdout; exits non-zero if the version/extract checks fail.

set -Eeuo pipefail

OMES_SELF="$(readlink -f "$0")"
OMES_ROOT="$(cd "$(dirname "$OMES_SELF")/../.." && pwd)"
FIXTURE_DIR="${OMES_ROOT}/tests/fixtures/graphify/sample-repo"
IMAGE="${OMES_GRAPHIFY_SMOKE_IMAGE:-python:3.12-slim}"

log() { printf '[graphify-real-smoke] %s\n' "$*"; }
err() { printf '[graphify-real-smoke] ERROR %s\n' "$*" >&2; }

if ! command -v docker >/dev/null 2>&1; then
  err "docker is required for this opt-in real smoke test"
  exit 1
fi

if [[ ! -d "$FIXTURE_DIR" ]]; then
  err "fixture not found: ${FIXTURE_DIR}"
  exit 1
fi

log "image: ${IMAGE}"
log "fixture: ${FIXTURE_DIR} (synthetic, no private content)"

SCRIPT_INSIDE='
set -e
pip install -q graphifyy
echo "=== graphify --version ==="
graphify --version
mkdir -p /work
cp -r /fixture/src /work/src
cd /work
echo "=== graphify extract --code-only ==="
graphify extract /work --code-only
echo "=== graph.json summary ==="
python3 -c "
import json
d = json.load(open(\"graphify-out/graph.json\"))
print(\"nodes:\", len(d.get(\"nodes\", [])))
print(\"edges:\", len(d.get(\"edges\", [])))
"
test -f graphify-out/graph.json
'

RESULT=0
OUTPUT="$(docker run --rm \
  -v "${FIXTURE_DIR}:/fixture:ro" \
  "$IMAGE" bash -c "$SCRIPT_INSIDE" 2>&1)" || RESULT=$?

printf '%s\n' "$OUTPUT"

if [[ "$RESULT" -ne 0 ]]; then
  err "real smoke test failed (exit ${RESULT}) against image ${IMAGE}"
  exit "$RESULT"
fi

VERSION_LINE="$(printf '%s\n' "$OUTPUT" | grep -m1 '^graphify ' || true)"
log "PASS - installed version: ${VERSION_LINE:-unknown}"
exit 0
