#!/usr/bin/env bats
# tests/integration/dr-desktop-session.bats - DR scenario (b), issue #17:
# a broken desktop session recovers via `omes uninstall --module
# hyprland-session` (removes only the OMES session entry and wrapper;
# cinnamon.desktop is never touched) plus `omes restore` for the user's
# own hyprland.conf (managed by the separate, user-scope desktop-config
# module - see modules/hyprland-session/module.sh's header comment for
# why these are two modules that never run in the same `omes install`
# invocation).
#
# Skips outright (does not fake anything) if modules/hyprland-session or
# modules/desktop-config are absent - i.e. issue #8 has not merged onto
# this branch yet.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  if [[ ! -r "${OMES_TEST_ROOT}/modules/hyprland-session/module.sh" ]] \
    || [[ ! -r "${OMES_TEST_ROOT}/modules/desktop-config/module.sh" ]]; then
    skip "modules/hyprland-session and/or modules/desktop-config not present (issue #8 not merged on this branch) - see docs/disaster-recovery.md's scenario (b) note"
  fi

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  unset XDG_CONFIG_HOME || true

  OMES_OS_RELEASE_FILE="$(omes_fixture_path os-release)"
  cat >"$OMES_OS_RELEASE_FILE" <<'EOF'
PRETTY_NAME="Linux Mint 22"
NAME="Linux Mint"
VERSION_ID="22"
VERSION_CODENAME=wilma
ID=linuxmint
ID_LIKE="ubuntu debian"
UBUNTU_CODENAME=noble
EOF
  export OMES_OS_RELEASE_FILE

  export OMES_SESSION_DIR="${OMES_TEST_TMPDIR}/wayland-sessions"
  export OMES_BIN_DIR="${OMES_TEST_TMPDIR}/local-bin"
  mkdir -p "$OMES_SESSION_DIR" "$OMES_BIN_DIR"

  export OMES_DP_MEM_MB_OVERRIDE=16384
  export OMES_DP_DISK_FREE_MB_OVERRIDE=40960
  export SHIM_ENABLED_SERVICES="lightdm"

  export OMES_CINNAMON_SESSION_FILE="${OMES_TEST_TMPDIR}/cinnamon.desktop"
  printf '[Desktop Entry]\nName=Cinnamon\n' >"$OMES_CINNAMON_SESSION_FILE"

  export SHIM_HERMES_VERSION="1.2.3"

  # A pre-existing user hyprland.conf (custom keybinds) - desktop-config's
  # own template differs from this, so applying it with --yes overwrites
  # (backing up first) rather than no-op'ing on an identical file.
  mkdir -p "${HOME}/.config/hypr"
  printf '# my custom binds - do not lose me\n' >"${HOME}/.config/hypr/hyprland.conf"
}

teardown() {
  omes_test_teardown
}

_cinnamon_untouched() {
  run cat "$OMES_CINNAMON_SESSION_FILE"
  [[ "$output" == *"Name=Cinnamon"* ]]
}

@test "setup: hyprland-session (root) and desktop-config (user) both apply, overwriting the pre-existing hyprland.conf" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile desktop --yes
  [ "$status" -eq 0 ]
  [ -f "${OMES_SESSION_DIR}/omes-hyprland.desktop" ]
  [ -x "${OMES_BIN_DIR}/omes-hyprland-session" ]

  run "$OMES_BIN" install --profile desktop --yes
  [ "$status" -eq 0 ]

  run cat "${HOME}/.config/hypr/hyprland.conf"
  [[ "$output" != *"my custom binds"* ]]
  _cinnamon_untouched
}

@test "recovery: uninstall --module hyprland-session removes only the OMES session entry and wrapper, cinnamon untouched" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile desktop --yes
  [ "$status" -eq 0 ]

  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" uninstall --module hyprland-session --yes
  [ "$status" -eq 0 ]

  [ ! -e "${OMES_SESSION_DIR}/omes-hyprland.desktop" ]
  [ ! -e "${OMES_BIN_DIR}/omes-hyprland-session" ]
  _cinnamon_untouched

  run grep -q '^module.hyprland-session.status=removed$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]
}

@test "recovery: the user's original hyprland.conf is returned via omes restore --from <desktop-config's timestamp>" {
  run "$OMES_BIN" install --profile desktop --yes
  [ "$status" -eq 0 ]
  run cat "${HOME}/.config/hypr/hyprland.conf"
  [[ "$output" != *"my custom binds"* ]]

  omes_run_stdout_only "$OMES_BIN" restore --list --json
  [ "$status" -eq 0 ]
  local dc_ts
  dc_ts="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); ms=[b["timestamp"] for b in d["backups"] if b["module"]=="desktop-config"]; print(ms[0] if ms else "")' "$output")"
  [ -n "$dc_ts" ]

  run "$OMES_BIN" restore --from "$dc_ts" --yes
  [ "$status" -eq 0 ]

  run cat "${HOME}/.config/hypr/hyprland.conf"
  [ "$output" = "# my custom binds - do not lose me" ]
}
