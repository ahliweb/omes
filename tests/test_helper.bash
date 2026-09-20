#!/usr/bin/env bash
# shellcheck shell=bash
# tests/test_helper.bash - shared bats setup for OMES unit + integration tests.
#
# Sourced from every *.bats file's `setup()`. Provides a fresh, isolated
# state dir per test, puts tests/shims first on PATH, and defaults the
# network probe to "online" so tests never depend on real connectivity.

OMES_TEST_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export OMES_TEST_ROOT
export OMES_ROOT="$OMES_TEST_ROOT"

omes_test_setup() {
  OMES_TEST_TMPDIR="$(mktemp -d "${BATS_TEST_TMPDIR:-${TMPDIR:-/tmp}}/omes-test.XXXXXX")"
  export OMES_TEST_TMPDIR

  export OMES_STATE_DIR="${OMES_TEST_TMPDIR}/state"
  mkdir -p "$OMES_STATE_DIR"

  export SHIM_LOG="${OMES_TEST_TMPDIR}/shim.log"
  : >"$SHIM_LOG"

  export SHIM_INSTALLED_PKGS_FILE="${OMES_TEST_TMPDIR}/installed-pkgs.txt"
  : >"$SHIM_INSTALLED_PKGS_FILE"

  export PATH="${OMES_TEST_ROOT}/tests/shims:${PATH}"

  export OMES_ASSUME_ONLINE=1
  unset OMES_ASSUME_OFFLINE || true

  export OMES_TEST=1
  unset OMES_FAKE_ROOT || true

  unset OMES_DRY_RUN OMES_JSON OMES_VERBOSE OMES_NONINTERACTIVE OMES_LOG_FILE OMES_OS_RELEASE_FILE || true
}

omes_test_teardown() {
  if [[ -n "${OMES_TEST_TMPDIR:-}" ]] && [[ -d "$OMES_TEST_TMPDIR" ]]; then
    rm -rf "$OMES_TEST_TMPDIR"
  fi
}

# omes_write_os_release <target-path> <heredoc-content-file>
# Small helper: tests build an /etc/os-release-shaped fixture inline via a
# heredoc into $OMES_TEST_TMPDIR and point OMES_OS_RELEASE_FILE at it
# (tests/fixtures/os-release is intentionally not used here; it belongs to
# another part of the test suite).
omes_fixture_path() {
  printf '%s/%s\n' "$OMES_TEST_TMPDIR" "$1"
}

# omes_run_stdout_only <command...>
# Like bats' `run`, but captures ONLY stdout into $output (discarding
# stderr) and sets $status, exactly the two variables `run` would have
# set. bats' own `run` captures merged stdout+stderr into $output, which
# is the right default for human-mode assertions but WRONG for asserting
# the --json contract (docs/architecture.md Section 8.2: exactly one JSON
# object on stdout, all logging on stderr) whenever the command under
# test also logs via log_info/log_warn - a merged $output would contain
# both and fail to parse as JSON even though the CLI is behaving
# correctly. Use this instead of `run` whenever a test pipes $output into
# `python3 -m json.tool`/`json.loads` for a command that also logs.
omes_run_stdout_only() {
  # status/output are bats convention names, read by the test body exactly
  # like a normal `run` would set them. The `|| status=$?` form (rather
  # than a bare assignment followed by `status=$?`) is required: bats
  # runs helper functions under errexit-like semantics, so a bare
  # `output="$(cmd)"` whose cmd exits non-zero would abort this function
  # immediately - before status is ever set - for every non-success
  # command under test (usage errors, preflight failures, etc.), which is
  # exactly the case this helper exists to support.
  local rc=0
  # shellcheck disable=SC2034
  output="$("$@" 2>/dev/null)" || rc=$?
  # shellcheck disable=SC2034
  status="$rc"
}
