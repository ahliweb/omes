#!/usr/bin/env bash
# shellcheck shell=bash
# Managed by OMES (`omes graphify skill install`) - do not edit by hand;
# re-run `omes graphify skill install --yes` to refresh this file from
# modules/graphify/skill/run.sh in the OMES checkout instead.
#
# Hermes skill wrapper for Graphify: shells out to the OMES-installed
# `omes graphify run`, never to the `graphify` CLI directly. See SKILL.md
# in this same directory, and docs/graphify.md §3 in the OMES repo, for
# what this actually does (path validation, mode gating, provenance
# sidecar, and the EXTRACTED/INFERRED provenance model).
set -Eeuo pipefail

if ! command -v omes >/dev/null 2>&1; then
  printf '[graphify skill] "omes" is not on PATH - install OMES first (see docs/graphify.md)\n' >&2
  exit 1
fi

exec omes graphify run "$@"
