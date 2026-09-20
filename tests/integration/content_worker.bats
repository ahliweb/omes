#!/usr/bin/env bats
# tests/integration/content_worker.bats - `omes content plan|publish|session`
# against the generic_browser worker's default manual_stub driver (#66).
# No real browser, no network; exercises the full needs_login ->
# manual-review -> session login -> retry -> publish -> succeeded ->
# archived path end to end via the real CLI entry point.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  CONTENT_ROOT="${OMES_TEST_TMPDIR}/content-root"
  export OMES_CONTENT_ROOT="$CONTENT_ROOT"
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  mkdir -p "${CONTENT_ROOT}/inbox"
  echo "tiny-media-bytes" >"${CONTENT_ROOT}/inbox/clip.mp4"
  "$OMES_BIN" content scan --json --settle-seconds 0 >/dev/null
  JOB_ID="$(ls "${CONTENT_ROOT}/state/jobs" | head -1 | sed 's/\.json$//')"
}

teardown() {
  omes_test_teardown
}

@test "plan records targets and moves to approval-required" {
  run "$OMES_BIN" content plan "$JOB_ID" --actor alice --caption "hello" --target generic_browser --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"state": "approval-required"'* ]]
  [[ "$output" == *'generic_browser'* ]]
}

@test "publish before session login goes to manual-review with needs_login" {
  "$OMES_BIN" content plan "$JOB_ID" --actor alice --target generic_browser >/dev/null
  "$OMES_BIN" content approve "$JOB_ID" --actor alice >/dev/null
  run "$OMES_BIN" content publish "$JOB_ID" --platform generic_browser --json
  [ "$status" -ne 0 ]
  [[ "$output" == *'"state": "manual-review"'* ]]
}

@test "publish rejects a platform that is not one of the job's planned targets" {
  "$OMES_BIN" content plan "$JOB_ID" --actor alice --target generic_browser >/dev/null
  "$OMES_BIN" content approve "$JOB_ID" --actor alice >/dev/null
  run "$OMES_BIN" content publish "$JOB_ID" --platform some-other-platform --json
  [ "$status" -ne 0 ]
}

@test "publish rejects an unknown platform (no such worker registered)" {
  "$OMES_BIN" content plan "$JOB_ID" --actor alice >/dev/null
  "$OMES_BIN" content approve "$JOB_ID" --actor alice >/dev/null
  run "$OMES_BIN" content publish "$JOB_ID" --platform does-not-exist --json
  [ "$status" -ne 0 ]
}

@test "session login creates a mode-0700 profile directory and never publishes" {
  run "$OMES_BIN" content session login generic_browser --actor alice --json
  [ "$status" -eq 0 ]
  [ -d "${CONTENT_ROOT}/sessions/generic_browser" ]
  run bash -c "stat -c '%a' '${CONTENT_ROOT}/sessions/generic_browser'"
  [ "$output" = "700" ]
  # no job should have moved past its current state as a side effect.
  [ ! -d "${CONTENT_ROOT}/uploaded" ] || [ -z "$(ls -A "${CONTENT_ROOT}/uploaded" 2>/dev/null)" ]
}

@test "session login then retry then publish reaches succeeded/archived with a URL" {
  "$OMES_BIN" content plan "$JOB_ID" --actor alice --target generic_browser >/dev/null
  "$OMES_BIN" content approve "$JOB_ID" --actor alice >/dev/null
  "$OMES_BIN" content publish "$JOB_ID" --platform generic_browser --json >/dev/null || true
  "$OMES_BIN" content session login generic_browser --actor alice --json >/dev/null
  "$OMES_BIN" content retry "$JOB_ID" --actor alice --yes >/dev/null
  run "$OMES_BIN" content publish "$JOB_ID" --platform generic_browser --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"state": "archived"'* ]]
  [[ "$output" == *"https://"* ]]
  [ -d "${CONTENT_ROOT}/uploaded/${JOB_ID}" ]
}

@test "session revoke without --yes is refused; with --yes clears the profile" {
  "$OMES_BIN" content session login generic_browser --actor alice --json >/dev/null
  run "$OMES_BIN" content session revoke generic_browser --actor alice
  [ "$status" -ne 0 ]
  run "$OMES_BIN" content session revoke generic_browser --actor alice --yes --json
  [ "$status" -eq 0 ]
  run bash -c "ls -A '${CONTENT_ROOT}/sessions/generic_browser' | wc -l"
  [ "$output" -eq 0 ]
}

@test "sessions/ never appears in the job report or a redacted export" {
  "$OMES_BIN" content plan "$JOB_ID" --actor alice --target generic_browser >/dev/null
  "$OMES_BIN" content approve "$JOB_ID" --actor alice >/dev/null
  "$OMES_BIN" content publish "$JOB_ID" --platform generic_browser --json >/dev/null || true
  "$OMES_BIN" content session login generic_browser --actor alice --json >/dev/null
  "$OMES_BIN" content retry "$JOB_ID" --actor alice --yes >/dev/null
  "$OMES_BIN" content publish "$JOB_ID" --platform generic_browser --json >/dev/null

  run bash -c "grep -c 'sessions/' '${CONTENT_ROOT}/reports/${JOB_ID}/report.json' || true"
  [ "$output" -eq 0 ]

  run "$OMES_BIN" content export --since 2020-01-01 --out "${OMES_TEST_TMPDIR}/export" --json
  [ "$status" -eq 0 ]
  [ ! -e "${OMES_TEST_TMPDIR}/export/sessions" ]
  run bash -c "grep -c 'sessions/' '${OMES_TEST_TMPDIR}/export/audit.jsonl' || true"
  [ "$output" -eq 0 ]
}
