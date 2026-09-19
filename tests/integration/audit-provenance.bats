#!/usr/bin/env bats
# tests/integration/audit-provenance.bats - `omes audit provenance`
# wiring (issue #84). Deep evaluation logic (checksum mismatch, mutable
# URL, missing metadata, executable review) is covered by
# tests/py/provenance/test_audit.py's direct calls into audit.py; these
# integration tests assert the bash <-> python wiring through
# `omes install --profile hermes` (which records a real provenance
# entry) and `omes audit provenance` reading it back.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  export OMES_BIN="${OMES_TEST_ROOT}/bin/omes"
  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  unset OMES_HERMES_INSTALLER_SHA256 SHIM_HERMES_VERSION OMES_FAKE_ROOT || true

  # `omes install` runs platform detection before any module check; the
  # bats container itself may not be a supported OS (e.g. the Alpine
  # bats/bats image), so pin a supported fixture like
  # tests/integration/hermes.bats does.
  OMES_OS_RELEASE_FILE="$(omes_fixture_path os-release)"
  cat >"$OMES_OS_RELEASE_FILE" <<'EOF'
PRETTY_NAME="Ubuntu 24.04 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
UBUNTU_CODENAME=noble
EOF
  export OMES_OS_RELEASE_FILE
}

teardown() {
  omes_test_teardown
}

@test "omes audit provenance with no records yet is clean (exit 0)" {
  omes_run_stdout_only "$OMES_BIN" audit provenance --json
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["ok"] is True
assert d["findings"] == []
'
  [ "$status" -eq 0 ]
}

@test "omes install --module hermes --dry-run records no provenance (nothing installed)" {
  export SHIM_HERMES_VERSION="1.2.3"
  run "$OMES_BIN" install --module hermes --dry-run --yes
  [ "$status" -eq 0 ]
  [ ! -e "${OMES_STATE_DIR}/provenance/hermes.json" ]

  omes_run_stdout_only "$OMES_BIN" audit provenance --json
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["components"] == []
'
  [ "$status" -eq 0 ]
}

@test "omes audit provenance flags a checksum mismatch fixture as FAIL (exit 7), fail closed" {
  mkdir -p "${OMES_STATE_DIR}/provenance"
  cat > "${OMES_STATE_DIR}/provenance/broken.json" <<'JSON'
{"component":"broken","installer_source_url":"https://example.com/x.sh","resolved_version":"1.0","install_time":"2026-01-01T00:00:00Z","checksum":{"algorithm":"sha256","expected":"aaa","actual":"bbb","status":"unknown"}}
JSON
  chmod 600 "${OMES_STATE_DIR}/provenance/broken.json"

  omes_run_stdout_only "$OMES_BIN" audit provenance --json
  [ "$status" -eq 7 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["ok"] is False
kinds = [f["kind"] for f in d["findings"]]
assert "checksum_mismatch" in kinds, kinds
severities = {f["kind"]: f["severity"] for f in d["findings"]}
assert severities["checksum_mismatch"] == "FAIL"
'
  [ "$status" -eq 0 ]
}

@test "omes audit provenance warns on a mutable installer URL" {
  mkdir -p "${OMES_STATE_DIR}/provenance"
  cat > "${OMES_STATE_DIR}/provenance/mutable.json" <<'JSON'
{"component":"mutable","installer_source_url":"https://example.com/main/install.sh","resolved_version":"1.0","install_time":"2026-01-01T00:00:00Z","checksum":{"status":"locally-built"}}
JSON
  chmod 600 "${OMES_STATE_DIR}/provenance/mutable.json"

  omes_run_stdout_only "$OMES_BIN" audit provenance --json
  [ "$status" -eq 7 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
kinds = [f["kind"] for f in d["findings"]]
assert "mutable_url" in kinds, kinds
'
  [ "$status" -eq 0 ]
}

@test "omes audit provenance warns on missing metadata" {
  mkdir -p "${OMES_STATE_DIR}/provenance"
  printf '{"component":"incomplete"}\n' > "${OMES_STATE_DIR}/provenance/incomplete.json"
  chmod 600 "${OMES_STATE_DIR}/provenance/incomplete.json"

  omes_run_stdout_only "$OMES_BIN" audit provenance --json
  [ "$status" -eq 7 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
kinds = [f["kind"] for f in d["findings"]]
assert "missing_metadata" in kinds, kinds
'
  [ "$status" -eq 0 ]
}

@test "omes audit provenance --profile filters by recorded profile" {
  mkdir -p "${OMES_STATE_DIR}/provenance"
  cat > "${OMES_STATE_DIR}/provenance/scoped.json" <<'JSON'
{"component":"scoped","profile":"hermes","installer_source_url":"https://example.com/x.sh","resolved_version":"1.0","install_time":"2026-01-01T00:00:00Z","checksum":{"status":"locally-built"}}
JSON
  chmod 600 "${OMES_STATE_DIR}/provenance/scoped.json"

  omes_run_stdout_only "$OMES_BIN" audit provenance --profile no-such-profile --json
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["components"] == []
'
  [ "$status" -eq 0 ]

  omes_run_stdout_only "$OMES_BIN" audit provenance --profile hermes --json
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
names = [c["component"] for c in d["components"]]
assert "scoped" in names, names
'
  [ "$status" -eq 0 ]
}

@test "omes audit provenance never executes a discovered executable (canary absent)" {
  local canary="${OMES_TEST_TMPDIR}/canary-should-not-exist"
  rm -f "$canary"
  mkdir -p "${HOME}/.hermes/skills/evil"
  cat > "${HOME}/.hermes/skills/evil/run.sh" <<EOF
#!/usr/bin/env bash
touch "${canary}"
EOF
  chmod 755 "${HOME}/.hermes/skills/evil/run.sh"

  omes_run_stdout_only "$OMES_BIN" audit provenance --json
  [ "$status" -eq 0 ] || [ "$status" -eq 7 ]
  [ ! -e "$canary" ]

  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
paths = [r["path"] for r in d["review"]]
assert any("evil" in p for p in paths), paths
'
  [ "$status" -eq 0 ]
}
