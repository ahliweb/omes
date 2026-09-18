#!/usr/bin/env bats
# tests/integration/desktop.bats - `omes check`/`omes install --profile
# desktop` integration tests, executing bin/omes end to end against
# tests/shims/.
#
# Root-scope behavior (apt-base, hyprland-session) uses the OMES_TEST=1 /
# OMES_FAKE_ROOT=1 test hook, never real privilege escalation.
# OMES_SESSION_DIR / OMES_BIN_DIR keep the session file and wrapper under
# an isolated tmpdir - this suite never writes under the real /usr.
# OMES_CINNAMON_SESSION_FILE always points at an isolated fake Cinnamon
# session file; every mutating test asserts it is left untouched.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"

  OMES_OS_RELEASE_FILE="$(omes_fixture_path os-release)"
  cat > "$OMES_OS_RELEASE_FILE" <<'EOF'
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

  # Linux Mint's default display manager (docs/compatibility-matrix.md
  # Section 5.2); detect_display_manager falls back to the systemctl shim
  # since /etc/X11/default-display-manager does not exist in the bats
  # container.
  export SHIM_ENABLED_SERVICES="lightdm"

  export OMES_CINNAMON_SESSION_FILE="${OMES_TEST_TMPDIR}/cinnamon.desktop"
  printf '[Desktop Entry]\nName=Cinnamon\n' > "$OMES_CINNAMON_SESSION_FILE"

  # hermes is commented out of profiles/desktop.profile by default, so it
  # never runs in this suite; SHIM_HERMES_VERSION is set defensively only
  # in case a future edit re-enables it.
  export SHIM_HERMES_VERSION="1.2.3"
}

teardown() {
  omes_test_teardown
}

_cinnamon_untouched() {
  [ -f "$OMES_CINNAMON_SESSION_FILE" ]
  run cat "$OMES_CINNAMON_SESSION_FILE"
  [[ "$output" == *"Name=Cinnamon"* ]]
}

# --- omes check --profile desktop --------------------------------------------

@test "check --profile desktop on Linux Mint 22 with cinnamon present exits 0" {
  run "$OMES_BIN" check --profile desktop
  [ "$status" -eq 0 ]
  [[ "$output" == *"desktop-preflight"* ]]
}

@test "check --profile desktop fails (exit 4) when cinnamon.desktop is missing" {
  export OMES_CINNAMON_SESSION_FILE="${OMES_TEST_TMPDIR}/nope/cinnamon.desktop"
  run "$OMES_BIN" check --profile desktop
  [ "$status" -eq 4 ]
  [[ "$output" == *"Cinnamon session file not found"* ]]
}

@test "check --profile desktop fails (exit 4) when hyprland is unavailable, no apt-get call" {
  export SHIM_APT_CACHE_UNKNOWN_PKGS="hyprland"
  run "$OMES_BIN" check --profile desktop
  [ "$status" -eq 4 ]
  [[ "$output" == *"hyprland"* ]]
  run grep -c '^apt-get install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "check --profile desktop performs a GPU/display preflight (report mentions GPU vendor)" {
  run "$OMES_BIN" check --profile desktop
  [ "$status" -eq 0 ]
  [[ "$output" == *"GPU vendor"* ]]
}

# --- omes install --profile desktop (root scope: apt-base, hyprland-session) -

@test "install --profile desktop as (simulated) root installs the toolset and session entry, never touching cinnamon" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile desktop --yes
  [ "$status" -eq 0 ]

  [ -f "${OMES_SESSION_DIR}/omes-hyprland.desktop" ]
  [ -x "${OMES_BIN_DIR}/omes-hyprland-session" ]

  run grep -q '^module.hyprland-session.status=applied$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]

  _cinnamon_untouched
}

@test "install --profile desktop as (simulated) root is idempotent" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile desktop --yes
  [ "$status" -eq 0 ]
  : > "$SHIM_LOG"

  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile desktop --yes
  [ "$status" -eq 0 ]
  run grep -c '^apt-get install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]

  _cinnamon_untouched
}

@test "install --profile desktop --dry-run as (simulated) root writes nothing" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile desktop --dry-run --yes
  [ "$status" -eq 0 ]
  [ ! -e "${OMES_SESSION_DIR}/omes-hyprland.desktop" ]
  [ ! -e "${OMES_BIN_DIR}/omes-hyprland-session" ]
  run grep -c '^apt-get' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  _cinnamon_untouched
}

@test "install --profile desktop as (simulated) root fails (exit 4) when cinnamon.desktop is missing" {
  export OMES_CINNAMON_SESSION_FILE="${OMES_TEST_TMPDIR}/nope/cinnamon.desktop"
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile desktop --yes
  [ "$status" -eq 4 ]
  [ ! -e "${OMES_SESSION_DIR}/omes-hyprland.desktop" ]
}

# --- omes install --profile desktop (user scope: desktop-preflight, desktop-config) -

@test "install --profile desktop as non-root applies desktop-preflight and desktop-config, and tells the operator to sudo for the rest" {
  run "$OMES_BIN" install --profile desktop --yes
  if [ "$status" -ne 0 ] || [ ! -f "${HOME}/.config/hypr/hyprland.conf" ]; then
    {
      echo "DEBUG install status=${status}"
      echo "DEBUG install output:"
      echo "$output"
      echo "DEBUG find \$HOME:"
      find "$HOME" 2>&1
      echo "DEBUG id: $(id)"
      echo "DEBUG PATH=$PATH"
      echo "DEBUG which omes-config-hypr-src: $(ls -la "${OMES_TEST_ROOT}/config/hypr" 2>&1)"
    } >&3
  fi
  [ "$status" -eq 0 ]
  [[ "$output" == *"Run with sudo: sudo omes install --profile desktop"* ]]

  [ -f "${HOME}/.config/hypr/hyprland.conf" ]
  [ -f "${HOME}/.config/omes/shell.sh" ]

  run grep -q '^module.desktop-config.status=applied$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]

  _cinnamon_untouched
}

@test "install --profile desktop as non-root never invokes apt-get (root-scope modules are skipped, not run)" {
  run "$OMES_BIN" install --profile desktop --yes
  [ "$status" -eq 0 ]
  run grep -c '^apt-get' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "re-running install --profile desktop as non-root is idempotent" {
  run "$OMES_BIN" install --profile desktop --yes
  [ "$status" -eq 0 ]
  run "$OMES_BIN" install --profile desktop --yes
  [ "$status" -eq 0 ]
  run grep -c 'BEGIN OMES desktop shell config' "${HOME}/.bashrc"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

# --- full end-to-end: root run then user run, exactly the documented order --

@test "the documented two-step install (sudo, then as user) leaves cinnamon untouched throughout" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile desktop --yes
  [ "$status" -eq 0 ]
  _cinnamon_untouched

  run "$OMES_BIN" install --profile desktop --yes
  [ "$status" -eq 0 ]
  _cinnamon_untouched

  [ -f "${OMES_SESSION_DIR}/omes-hyprland.desktop" ]
  [ -f "${HOME}/.config/hypr/hyprland.conf" ]
}
