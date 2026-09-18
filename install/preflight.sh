#!/usr/bin/env bash
# shellcheck shell=bash
# install/preflight.sh - thin wrapper: run OMES's own preflight checks.
#
# This script performs no mutation itself; it simply execs `bin/omes
# check` with whatever arguments it was given (e.g. --profile server).

set -Eeuo pipefail
IFS=$'\n\t'

OMES_SELF="$(readlink -f "$0")"
OMES_ROOT="$(cd "$(dirname "$OMES_SELF")/.." && pwd)"

exec "${OMES_ROOT}/bin/omes" check "$@"
