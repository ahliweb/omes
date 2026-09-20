#!/usr/bin/env bats
# tests/integration/extension_commands.bats - lib/omes/cmd/<name>.sh dispatch.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  # Work on a throwaway copy of bin/+lib/ so the test can add an extension
  # without touching the real tree.
  WORK="${OMES_TEST_TMPDIR}/ext-tree"
  mkdir -p "$WORK"
  cp -a "${OMES_TEST_ROOT}/bin" "${OMES_TEST_ROOT}/lib" "${OMES_TEST_ROOT}/profiles" "${OMES_TEST_ROOT}/modules" "${OMES_TEST_ROOT}/VERSION" "$WORK/"
  mkdir -p "${WORK}/lib/omes/cmd"
  cat >"${WORK}/lib/omes/cmd/probe.sh" <<'EOF2'
# omes-help: prints its arguments (test extension)
cmd_probe() {
  local IFS=' '
  printf 'ARGS=%s JSON=%s DRY=%s\n' "$*" "$OMES_JSON" "$OMES_DRY_RUN"
}
EOF2
  OMES_BIN="${WORK}/bin/omes"
}

teardown() {
  omes_test_teardown
}

@test "an extension command receives its own arguments and the parsed global flags" {
  run "$OMES_BIN" probe --json status my-agent --limit 3
  [ "$status" -eq 0 ]
  [ "$output" = "ARGS=status my-agent --limit 3 JSON=1 DRY=0" ]
}

@test "global flags before the first positional are parsed; later ones are passed through" {
  run "$OMES_BIN" probe --dry-run apply x --json
  [ "$status" -eq 0 ]
  [ "$output" = "ARGS=apply x --json JSON=0 DRY=1" ]
}

@test "omes help lists extension commands with their omes-help summary" {
  run "$OMES_BIN" help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Extension commands:"* ]]
  [[ "$output" == *"probe       prints its arguments (test extension)"* ]]
}

@test "an unknown command is still a usage error (exit 2)" {
  run "$OMES_BIN" nosuchcommand
  [ "$status" -eq 2 ]
}

@test "an extension file without cmd_<name> is a clear error" {
  printf '# omes-help: broken\n' >"${WORK}/lib/omes/cmd/broken.sh"
  run "$OMES_BIN" broken
  [ "$status" -eq 1 ]
  [[ "$output" == *"does not define cmd_broken"* ]]
}
