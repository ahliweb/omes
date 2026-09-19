#!/usr/bin/env bats
# tests/integration/content_jobs.bats - `omes content` state machine CLI
# surface (#67): approve/reject/retry/cancel/resume/reconcile.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  CONTENT_ROOT="${OMES_TEST_TMPDIR}/content-root"
  export OMES_CONTENT_ROOT="$CONTENT_ROOT"
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  # Seed one job directly in approval-required via a small python helper
  # (there is no CLI `plan` yet - that lands with #66/#69's caption/worker
  # integration; #67 only needs the state machine + retry/reconcile CLI,
  # which this test exercises against a hand-seeded job record).
  mkdir -p "${CONTENT_ROOT}/state/jobs" "${CONTENT_ROOT}/processing/job1"
  echo "tiny-media" > "${CONTENT_ROOT}/processing/job1/source.mp4"
  PYTHONPATH="${OMES_TEST_ROOT}/lib/omes/py" python3 - "$CONTENT_ROOT" <<'PYEOF'
import sys
from content import jobs, paths
root = __import__("pathlib").Path(sys.argv[1])
paths.ensure_layout(root)
record = jobs.new_job_record(
    job_id="job1",
    original_path="inbox/x.mp4",
    processing_path="processing/job1/source.mp4",
    sha256_hex="f" * 64,
    size_bytes=11,
    mime_guess="video/mp4",
)
jobs.plan_job(record)
jobs.save_job(record, root)
PYEOF
}

teardown() {
  omes_test_teardown
}

@test "approve requires --actor" {
  run "$OMES_BIN" content approve job1
  [ "$status" -ne 0 ]
}

@test "approve records an approval and moves the job to approved" {
  run "$OMES_BIN" content approve job1 --actor alice --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"state": "approved"'* ]]
}

@test "cancel without --yes is refused" {
  run "$OMES_BIN" content cancel job1 --actor bob
  [ "$status" -ne 0 ]
}

@test "cancel with --yes records the actor" {
  run "$OMES_BIN" content cancel job1 --actor bob --yes --json
  [ "$status" -eq 0 ]
  # cancel is a terminal outcome, so cli.py's _persist() immediately
  # archives it (issue #68) - the printed state reflects that.
  [[ "$output" == *'"state": "archived"'* ]]
  run grep -o '"actor": "bob"' "${CONTENT_ROOT}/state/jobs/job1.json"
  [ "$status" -eq 0 ]
}

@test "reconcile lists a manual-review job with a reason" {
  "$OMES_BIN" content approve job1 --actor alice >/dev/null
  # Force it into manual-review by hand (as an uncertain publish would).
  PYTHONPATH="${OMES_TEST_ROOT}/lib/omes/py" python3 - "$CONTENT_ROOT" <<'PYEOF'
import sys
from content import jobs
root = __import__("pathlib").Path(sys.argv[1])
record = jobs.load_job("job1", root)
jobs.apply_transition(record, "publishing", actor="system")
jobs.apply_transition(record, "manual-review", actor="system", note="uncertain")
jobs.save_job(record, root)
PYEOF
  run "$OMES_BIN" content reconcile --json
  [ "$status" -eq 0 ]
  [[ "$output" == *"job1"* ]]
  [[ "$output" == *"manual-review"* ]]
}

@test "resume moves a job stuck in publishing to manual-review without a worker" {
  "$OMES_BIN" content approve job1 --actor alice >/dev/null
  PYTHONPATH="${OMES_TEST_ROOT}/lib/omes/py" python3 - "$CONTENT_ROOT" <<'PYEOF'
import sys
from content import jobs
root = __import__("pathlib").Path(sys.argv[1])
record = jobs.load_job("job1", root)
jobs.apply_transition(record, "publishing", actor="system")
jobs.save_job(record, root)
PYEOF
  run "$OMES_BIN" content resume --json
  [ "$status" -eq 0 ]
  [[ "$output" == *"job1"* ]]
  run grep -o '"state": "manual-review"' "${CONTENT_ROOT}/state/jobs/job1.json"
  [ "$status" -eq 0 ]
}
