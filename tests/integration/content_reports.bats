#!/usr/bin/env bats
# tests/integration/content_reports.bats - `omes content report|export|prune`
# and archive-on-terminal-state behavior (#68).

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

@test "cancel archives the job and generates a report" {
  run "$OMES_BIN" content cancel "$JOB_ID" --actor alice --yes --json
  [ "$status" -eq 0 ]
  [ -d "${CONTENT_ROOT}/review/${JOB_ID}" ]
  [ -f "${CONTENT_ROOT}/reports/${JOB_ID}/report.json" ]
  [ -f "${CONTENT_ROOT}/reports/${JOB_ID}/report.md" ]
}

@test "omes content report --md prints markdown" {
  "$OMES_BIN" content cancel "$JOB_ID" --actor alice --yes >/dev/null
  run "$OMES_BIN" content report "$JOB_ID" --md
  [ "$status" -eq 0 ]
  [[ "$output" == *"# Content job report"* ]]
}

@test "audit log exists, is private, and never inside sessions" {
  "$OMES_BIN" content cancel "$JOB_ID" --actor alice --yes >/dev/null
  [ -f "${CONTENT_ROOT}/state/audit.jsonl" ]
  run bash -c "stat -c '%a' '${CONTENT_ROOT}/state/audit.jsonl'"
  [ "$output" = "600" ]
  run bash -c "ls -A '${CONTENT_ROOT}/sessions' | wc -l"
  [ "$output" -eq 0 ]
}

@test "export writes reports and audit but no sessions" {
  "$OMES_BIN" content cancel "$JOB_ID" --actor alice --yes >/dev/null
  run "$OMES_BIN" content export --since 2020-01-01 --out "${OMES_TEST_TMPDIR}/export" --json
  [ "$status" -eq 0 ]
  [ -f "${OMES_TEST_TMPDIR}/export/reports/${JOB_ID}/report.json" ]
  [ -f "${OMES_TEST_TMPDIR}/export/audit.jsonl" ]
  [ ! -e "${OMES_TEST_TMPDIR}/export/sessions" ]
}

@test "prune without --yes and without --dry-run is refused" {
  "$OMES_BIN" content cancel "$JOB_ID" --actor alice --yes >/dev/null
  run "$OMES_BIN" content prune --older-than 0
  [ "$status" -ne 0 ]
  [ -d "${CONTENT_ROOT}/review/${JOB_ID}" ]
}

@test "prune --dry-run never deletes" {
  "$OMES_BIN" content cancel "$JOB_ID" --actor alice --yes >/dev/null
  run "$OMES_BIN" content prune --older-than 0 --dry-run --json
  [ "$status" -eq 0 ]
  [[ "$output" == *"$JOB_ID"* ]]
  [ -d "${CONTENT_ROOT}/review/${JOB_ID}" ]
}

@test "prune --yes deletes archived evidence and reports but keeps sessions/audit" {
  "$OMES_BIN" content cancel "$JOB_ID" --actor alice --yes >/dev/null
  run "$OMES_BIN" content prune --older-than 0 --yes --json
  [ "$status" -eq 0 ]
  [ ! -d "${CONTENT_ROOT}/review/${JOB_ID}" ]
  [ ! -d "${CONTENT_ROOT}/reports/${JOB_ID}" ]
  [ -f "${CONTENT_ROOT}/state/audit.jsonl" ]
  [ -d "${CONTENT_ROOT}/sessions" ]
}
