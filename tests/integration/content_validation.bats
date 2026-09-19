#!/usr/bin/env bats
# tests/integration/content_validation.bats - `omes content plan|edit|
# publish` platform-aware validation (#69) through the real `omes` CLI.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  CONTENT_ROOT="${OMES_TEST_TMPDIR}/content-root"
  export OMES_CONTENT_ROOT="$CONTENT_ROOT"
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  mkdir -p "${CONTENT_ROOT}/inbox"
  echo "tiny-media-bytes" > "${CONTENT_ROOT}/inbox/clip.mp4"
  "$OMES_BIN" content scan --json --settle-seconds 0 >/dev/null
  JOB_ID="$(ls "${CONTENT_ROOT}/state/jobs" | head -1 | sed 's/\.json$//')"
}

teardown() {
  omes_test_teardown
}

@test "plan shows a per-platform validation preview without blocking" {
  run "$OMES_BIN" content plan "$JOB_ID" --actor alice --caption "guaranteed results" --target youtube --json
  [ "$status" -eq 0 ]
  [[ "$output" == *"unsupported_claim"* ]]
  [[ "$output" == *'"state": "approval-required"'* ]]
}

@test "publish blocks a platform with an unsupported-claim caption and moves to manual-review" {
  "$OMES_BIN" content plan "$JOB_ID" --actor alice --caption "guaranteed results" --target generic_browser >/dev/null
  "$OMES_BIN" content approve "$JOB_ID" --actor alice >/dev/null
  run "$OMES_BIN" content publish "$JOB_ID" --platform generic_browser --json
  [ "$status" -ne 0 ]
  [[ "$output" == *'"state": "manual-review"'* ]]
  [[ "$output" == *"unsupported_claim"* ]]
}

@test "edit --platform --caption-file saves a versioned variant and leaves plan.caption untouched" {
  "$OMES_BIN" content plan "$JOB_ID" --actor alice --caption "original caption" --target generic_browser >/dev/null
  echo "a clean, edited caption" > "${OMES_TEST_TMPDIR}/caption.txt"
  run "$OMES_BIN" content edit "$JOB_ID" --actor alice --platform generic_browser --caption-file "${OMES_TEST_TMPDIR}/caption.txt" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *"caption.v1"* ]]
  [ -f "${CONTENT_ROOT}/processing/${JOB_ID}/variants/generic_browser/caption.v1" ]
  run "$OMES_BIN" content list --json
  [[ "$output" == *"$JOB_ID"* ]]
}

@test "a clean edited variant passes validation where the original caption would have blocked" {
  "$OMES_BIN" content plan "$JOB_ID" --actor alice --caption "guaranteed results" --target generic_browser >/dev/null
  echo "a clean, edited caption" > "${OMES_TEST_TMPDIR}/caption.txt"
  "$OMES_BIN" content edit "$JOB_ID" --actor alice --platform generic_browser --caption-file "${OMES_TEST_TMPDIR}/caption.txt" >/dev/null
  "$OMES_BIN" content approve "$JOB_ID" --actor alice >/dev/null
  run "$OMES_BIN" content publish "$JOB_ID" --platform generic_browser --json
  [[ "$output" != *"unsupported_claim"* ]]
  [[ "$output" == *"needs_login"* ]]
}

@test "--force-validation bypasses a blocking validation error" {
  "$OMES_BIN" content plan "$JOB_ID" --actor alice --caption "guaranteed results" --target generic_browser >/dev/null
  "$OMES_BIN" content approve "$JOB_ID" --actor alice >/dev/null
  run "$OMES_BIN" content publish "$JOB_ID" --platform generic_browser --force-validation --json
  [[ "$output" == *"needs_login"* ]]
  [[ "$output" != *"unsupported_claim"* ]]
}
