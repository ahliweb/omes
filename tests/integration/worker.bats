#!/usr/bin/env bats
# tests/integration/worker.bats - `omes worker` extension command (ADR-0027, issue #192).

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"
}

teardown() {
  omes_test_teardown
}

@test "omes worker without args prints usage" {
  run "$OMES_BIN" worker
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage: omes worker <enroll|poll|heartbeat|status>"* ]]
}

@test "omes worker enroll --help prints enroll options" {
  run "$OMES_BIN" worker enroll --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--challenge CHALLENGE"* ]]
  [[ "$output" == *"--endpoint ENDPOINT"* ]]
}

@test "omes worker poll --help prints poll options" {
  run "$OMES_BIN" worker poll --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--once"* ]]
}

@test "omes worker status before enrollment fails with unenrolled error" {
  run "$OMES_BIN" worker status
  [ "$status" -ne 0 ]
  [[ "$output" == *"Host is not enrolled"* ]]
}

@test "omes worker heartbeat before enrollment fails with unenrolled error" {
  run "$OMES_BIN" worker heartbeat
  [ "$status" -ne 0 ]
  [[ "$output" == *"Host is not enrolled"* ]]
}
