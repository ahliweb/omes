#!/usr/bin/env bats
# tests/integration/job.bats - `omes job` extension command (#90).

setup() {
  load '../test_helper.bash'
  omes_test_setup
  export OMES_JOBS_TENANT_ID="tenant-acme"
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"
  REQ="${OMES_TEST_TMPDIR}/request.json"
}

teardown() {
  omes_test_teardown
}

_write_request() {
  local operation="$1"
  local idem="$2"
  local extra="${3:-}"
  cat >"$REQ" <<EOF
{
  "tenant_id": "tenant-acme",
  "correlation_id": "corr-1",
  "idempotency_key": "$idem",
  "actor": {"type": "user", "id": "op-1"},
  "operation": "$operation",
  "target": {"server_id": "srv-1"}${extra}
}
EOF
}

@test "omes job submit creates a queued job" {
  _write_request "status" "idem-0001"
  run "$OMES_BIN" job submit --file "$REQ" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"state": "queued"'* ]]
  run bash -c "ls '${OMES_STATE_DIR}/jobs/'job-*.json | wc -l"
  [ "$output" -eq 1 ]
}

@test "omes job submit is idempotent: replayed request returns the original job" {
  _write_request "status" "idem-dup"
  run "$OMES_BIN" job submit --file "$REQ" --json
  [ "$status" -eq 0 ]
  first_job_id=$(printf '%s' "$output" | grep -o '"job_id": "[^"]*"' | head -1)
  run "$OMES_BIN" job submit --file "$REQ" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"replayed": true'* ]]
  second_job_id=$(printf '%s' "$output" | grep -o '"job_id": "[^"]*"' | head -1)
  [ "$first_job_id" = "$second_job_id" ]
  run bash -c "ls '${OMES_STATE_DIR}/jobs/'job-*.json | wc -l"
  [ "$output" -eq 1 ]
}

@test "a request with a free-form command field is rejected by schema" {
  cat >"$REQ" <<'EOF'
{
  "tenant_id": "tenant-acme",
  "correlation_id": "corr-1",
  "idempotency_key": "idem-command",
  "actor": {"type": "user", "id": "op-1"},
  "operation": "backup",
  "target": {"server_id": "srv-1"},
  "command": "rm -rf /"
}
EOF
  run "$OMES_BIN" job submit --file "$REQ" --json
  [ "$status" -eq 1 ]
  [[ "$output" == *"additional properties not allowed"* ]]
}

@test "an option-like backup_id is rejected by schema, never reaching bin/omes restore" {
  cat >"$REQ" <<'EOF'
{
  "tenant_id": "tenant-acme",
  "correlation_id": "corr-1",
  "idempotency_key": "idem-option-like",
  "actor": {"type": "user", "id": "op-1"},
  "operation": "restore",
  "target": {"server_id": "srv-1"},
  "backup_id": "--yes"
}
EOF
  run "$OMES_BIN" job submit --file "$REQ" --json
  [ "$status" -eq 1 ]
  [[ "$output" == *"does not match pattern"* ]]
  run bash -c "ls '${OMES_STATE_DIR}/jobs/'job-*.json 2>/dev/null | wc -l"
  [ "$output" -eq 0 ]
}

@test "a cross-tenant request is rejected" {
  cat >"$REQ" <<'EOF'
{
  "tenant_id": "tenant-other",
  "correlation_id": "corr-1",
  "idempotency_key": "idem-cross",
  "actor": {"type": "user", "id": "op-1"},
  "operation": "status",
  "target": {"server_id": "srv-1"}
}
EOF
  run "$OMES_BIN" job submit --file "$REQ" --json
  [ "$status" -eq 1 ]
  [[ "$output" == *"cross_tenant_rejected"* ]]
}

@test "a destructive operation requires approval before it can run" {
  _write_request "stop" "idem-stop"
  run "$OMES_BIN" job submit --file "$REQ" --json
  [ "$status" -eq 0 ]
  job_id=$(printf '%s' "$output" | grep -o '"job_id": "[^"]*"' | head -1 | cut -d'"' -f4)
  run "$OMES_BIN" job run "$job_id" --json
  [ "$status" -eq 1 ]
}

@test "status is auto-approvable and succeeds end to end" {
  _write_request "status" "idem-status"
  run "$OMES_BIN" job submit --file "$REQ" --json
  [ "$status" -eq 0 ]
  job_id=$(printf '%s' "$output" | grep -o '"job_id": "[^"]*"' | head -1 | cut -d'"' -f4)
  run "$OMES_BIN" job run "$job_id" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"state": "succeeded"'* ]]
}

@test "an approved destructive job can run after approve" {
  _write_request "stop" "idem-stop-2"
  "$OMES_BIN" job submit --file "$REQ" --json >/dev/null
  job_id=$(bash -c "ls '${OMES_STATE_DIR}/jobs/'job-*.json" | head -1 | xargs basename | sed 's/\.json$//')
  run "$OMES_BIN" job approve "$job_id" --actor approver-1 --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"state": "approved"'* ]]
  run "$OMES_BIN" job run "$job_id" --json
  # stop has no existing OMES command yet -> not_implemented -> failed -> non-zero
  [ "$status" -eq 1 ]
  [[ "$output" == *"not_implemented"* ]]
}

@test "install has no existing OMES command and reports a typed not_implemented failure, never a shell fallback" {
  _write_request "install" "idem-install"
  "$OMES_BIN" job submit --file "$REQ" --json >/dev/null
  job_id=$(bash -c "ls '${OMES_STATE_DIR}/jobs/'job-*.json" | head -1 | xargs basename | sed 's/\.json$//')
  "$OMES_BIN" job approve "$job_id" --actor approver-1 --json >/dev/null
  run "$OMES_BIN" job run "$job_id" --json
  [ "$status" -eq 1 ]
  [[ "$output" == *'"not_implemented"'* ]]
}

@test "omes job list shows the submitted job" {
  _write_request "status" "idem-list"
  "$OMES_BIN" job submit --file "$REQ" --json >/dev/null
  run "$OMES_BIN" job list --json
  [ "$status" -eq 0 ]
  [[ "$output" == *"idem-list"* ]] || [[ "$output" == *'"operation": "status"'* ]]
}

@test "omes job cancel moves a queued job to cancelled" {
  _write_request "status" "idem-cancel"
  "$OMES_BIN" job submit --file "$REQ" --json >/dev/null
  job_id=$(bash -c "ls '${OMES_STATE_DIR}/jobs/'job-*.json" | head -1 | xargs basename | sed 's/\.json$//')
  run "$OMES_BIN" job cancel "$job_id" --actor op-1 --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"state": "cancelled"'* ]]
}

@test "job store and audit log directories are created mode 0700/0600" {
  _write_request "status" "idem-perms"
  "$OMES_BIN" job submit --file "$REQ" --json >/dev/null
  run bash -c "stat -c '%a' '${OMES_STATE_DIR}/jobs'"
  [ "$output" = "700" ]
  run bash -c "stat -c '%a' '${OMES_STATE_DIR}/jobs/audit.jsonl'"
  [ "$output" = "600" ]
}
