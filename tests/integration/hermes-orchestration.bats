#!/usr/bin/env bats
# tests/integration/hermes-orchestration.bats - `omes agent orchestration` CLI and projection (ADR-0028, issue #183).

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"
  export OMES_JOBS_TENANT_ID="tenant-acme"
  export OMES_JOBS_SERVER_ID="srv-01"
}

teardown() {
  omes_test_teardown
}

@test "omes agent orchestration --help prints options" {
  run "$OMES_BIN" agent orchestration --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--session SESSION"* ]]
  [[ "$output" == *"--json"* ]]
}

@test "omes agent orchestration for nonexistent session reports error" {
  run "$OMES_BIN" agent orchestration --session "sess-nonexistent-123"
  [ "$status" -ne 0 ]
  [[ "$output" == *"session 'sess-nonexistent-123' not found"* ]]
}

@test "omes agent orchestration outputs tree for ingested Hermes events" {
  # Ingest event using python helper
  python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, '${OMES_TEST_ROOT}/lib/omes/py')
from agent import orchestration

orchestration.ingest_event({
    'schema_version': '1.0.0',
    'event_type': 'subagent_start',
    'tenant_id': 'tenant-acme',
    'server_id': 'srv-01',
    'session_id': 'sess-bats-01',
    'subagent_id': 'sub-lead-bats',
    'parent_subagent_id': None,
    'role': 'lead_planner',
    'goal': 'Run bats integration verification',
    'state': 'RUNNING',
    'timestamp': '2026-09-21T12:00:00Z',
    'hermes_version': 'v2026.9.14',
})
"
  run "$OMES_BIN" agent orchestration --session "sess-bats-01" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"session_id": "sess-bats-01"'* ]]
  [[ "$output" == *'"root_subagent_id": "sub-lead-bats"'* ]]
  [[ "$output" == *'"active_count": 1'* ]]

  run "$OMES_BIN" agent orchestration --session "sess-bats-01"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Hermes Orchestration Session: sess-bats-01"* ]]
  [[ "$output" == *"sub-lead-bats [RUNNING]"* ]]
}
