#!/usr/bin/env bats
# tests/unit/provenance.bats - lib/omes/cmd/audit-provenance.sh unit tests
# (issue #84): `provenance_record_component` bash helper, and
# modules/hermes/module.sh's provenance recording at install.
#
# Deep audit evaluation logic (checksum mismatch, mutable URL, missing
# metadata, executable review) is covered by
# tests/py/provenance/test_audit.py and test_record.py; these tests
# assert the bash wiring: the helper writes the expected file at the
# expected mode, honors dry-run, and modules/hermes/module.sh calls it
# on a successful install.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  unset XDG_DATA_HOME OMES_HERMES_VERSION OMES_HERMES_INSTALLER_SHA256 \
    SHIM_HERMES_VERSION SHIM_HERMES_DOCTOR_EXIT SHIM_HERMES_DOCTOR_OUTPUT || true
  export OMES_HERMES_HOME="${HOME}/.hermes"

  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/log.sh
  source "${OMES_TEST_ROOT}/lib/omes/log.sh"
  # shellcheck source=../../lib/omes/json.sh
  source "${OMES_TEST_ROOT}/lib/omes/json.sh"
  # shellcheck source=../../lib/omes/detect.sh
  source "${OMES_TEST_ROOT}/lib/omes/detect.sh"
  # shellcheck source=../../lib/omes/state.sh
  source "${OMES_TEST_ROOT}/lib/omes/state.sh"
  # shellcheck source=../../lib/omes/runtime.sh
  source "${OMES_TEST_ROOT}/lib/omes/runtime.sh"
  # shellcheck source=../../lib/omes/cmd/audit-provenance.sh
  source "${OMES_TEST_ROOT}/lib/omes/cmd/audit-provenance.sh"
}

teardown() {
  omes_test_teardown
}

@test "provenance_record_component writes <state-dir>/provenance/<name>.json at mode 0600" {
  run provenance_record_component testcomp myprofile '{"installer_source_url":"https://example.com/x.sh","resolved_version":"1.0.0","checksum":{"algorithm":"sha256","status":"unverified"}}'
  [ "$status" -eq 0 ]

  local target="${OMES_STATE_DIR}/provenance/testcomp.json"
  [ -f "$target" ]
  local mode
  mode="$(stat -c '%a' "$target")"
  [ "$mode" = "600" ]

  OMES_TEST_JSON="$(cat "$target")" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["component"] == "testcomp"
assert d["profile"] == "myprofile"
assert d["checksum"]["status"] == "unverified"
'
  [ "$status" -eq 0 ]
}

@test "provenance_record_component honors dry-run: nothing written" {
  export OMES_DRY_RUN=1
  run provenance_record_component testcomp myprofile '{"installer_source_url":"https://example.com/x.sh"}'
  [ "$status" -eq 0 ]
  [ ! -e "${OMES_STATE_DIR}/provenance/testcomp.json" ]
}

@test "provenance_record_component with an empty payload still succeeds (never fails the caller)" {
  run provenance_record_component testcomp myprofile '{}'
  [ "$status" -eq 0 ]
  [ -f "${OMES_STATE_DIR}/provenance/testcomp.json" ]
}

@test "hermes module_apply records provenance for the hermes component on install" {
  # shellcheck source=../../lib/omes/backup.sh
  source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
  # shellcheck source=../../lib/omes/module.sh
  source "${OMES_TEST_ROOT}/lib/omes/module.sh"
  module_load hermes
  declare -ga OMES_MANAGED_PATHS=()

  # SHIM_HERMES_VERSION deliberately left unset: module_apply must think
  # hermes is NOT yet installed so it actually goes through
  # _hermes_download_and_install (where provenance is recorded), rather
  # than taking the "already installed, skip download" shortcut.

  run module_apply
  [ "$status" -eq 0 ]

  local target="${OMES_STATE_DIR}/provenance/hermes.json"
  [ -f "$target" ]

  OMES_TEST_JSON="$(cat "$target")" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["component"] == "hermes"
assert "installer_source_url" in d
assert d["checksum"]["status"] in ("verified", "unverified")
'
  [ "$status" -eq 0 ]
}

@test "hermes module_apply records checksum status verified when OMES_HERMES_INSTALLER_SHA256 matches" {
  # shellcheck source=../../lib/omes/backup.sh
  source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
  # shellcheck source=../../lib/omes/module.sh
  source "${OMES_TEST_ROOT}/lib/omes/module.sh"
  module_load hermes
  declare -ga OMES_MANAGED_PATHS=()

  # SHIM_HERMES_VERSION deliberately left unset here too - see the
  # comment in the previous test.

  # tests/shims/curl writes this fixed default installer body to -o
  # when SHIM_CURL_OUTPUT_FILE is not set; pin OMES_HERMES_INSTALLER_SHA256
  # to its known sha256 so the module's own comparison succeeds. Written
  # to a real file (never a command-substitution variable, which would
  # silently strip the trailing newline curl's shim actually writes) so
  # the hash is computed over the exact same bytes.
  local body_file="${OMES_TEST_TMPDIR}/expected-installer-body"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$body_file"
  export OMES_HERMES_INSTALLER_SHA256
  OMES_HERMES_INSTALLER_SHA256="$(sha256sum "$body_file" | awk '{print $1}')"

  run module_apply
  [ "$status" -eq 0 ]

  OMES_TEST_JSON="$(cat "${OMES_STATE_DIR}/provenance/hermes.json")" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["checksum"]["status"] == "verified", d
'
  [ "$status" -eq 0 ]
}

@test "hermes module_apply records uv-tool-managed package provenance (issue #84)" {
  # shellcheck source=../../lib/omes/backup.sh
  source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
  # shellcheck source=../../lib/omes/module.sh
  source "${OMES_TEST_ROOT}/lib/omes/module.sh"
  export SHIM_HERMES_VERSION="1.0.0"
  module_load hermes
  declare -ga OMES_MANAGED_PATHS=()

  run module_apply
  [ "$status" -eq 0 ]

  local target="${OMES_STATE_DIR}/provenance/uv:graphifyy.json"
  [ -f "$target" ]

  OMES_TEST_JSON="$(cat "$target")" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["component"] == "uv:graphifyy"
assert d["resolved_version"] == "v0.3.0"
assert d["checksum"]["status"] == "unverified"
assert d["package_manager"]["name"] == "uv"
assert d["package_manager"]["package"] == "graphifyy"
'
  [ "$status" -eq 0 ]
}

@test "hermes module_apply records pipx-managed package provenance (issue #84)" {
  # shellcheck source=../../lib/omes/backup.sh
  source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
  # shellcheck source=../../lib/omes/module.sh
  source "${OMES_TEST_ROOT}/lib/omes/module.sh"
  export SHIM_HERMES_VERSION="1.0.0"
  export SHIM_PIPX_LIST_JSON='{"venvs":{"somepkg":{"metadata":{"main_package":{"package":"somepkg","package_version":"2.1.0"}}}}}'
  module_load hermes
  declare -ga OMES_MANAGED_PATHS=()

  run module_apply
  [ "$status" -eq 0 ]

  local target="${OMES_STATE_DIR}/provenance/pipx:somepkg.json"
  [ -f "$target" ]

  OMES_TEST_JSON="$(cat "$target")" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["component"] == "pipx:somepkg"
assert d["resolved_version"] == "2.1.0"
assert d["checksum"]["status"] == "unverified"
assert d["package_manager"]["name"] == "pipx"
'
  [ "$status" -eq 0 ]
}

@test "grep-guard: modules/hermes never pipes curl output into a shell (no curl | bash / curl | sh)" {
  run grep -RnE 'curl[^|]*\|[[:space:]]*(sh|bash)([[:space:]]|$)' "${OMES_TEST_ROOT}/modules"
  [ "$status" -ne 0 ]
  [ -z "$output" ]
}
