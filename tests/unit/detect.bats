#!/usr/bin/env bats
# tests/unit/detect.bats - lib/omes/detect.sh unit tests.
#
# Note: tests/fixtures/os-release is intentionally NOT used here (owned by
# another part of the test suite); os-release content is generated inline
# via heredocs into the per-test temp dir and selected with
# OMES_OS_RELEASE_FILE.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/detect.sh
  source "${OMES_TEST_ROOT}/lib/omes/detect.sh"
}

teardown() {
  omes_test_teardown
}

_write_os_release() {
  local path
  path="$(omes_fixture_path os-release)"
  cat > "$path"
  printf '%s' "$path"
}

@test "detect_os parses ubuntu 24.04 fields" {
  local path
  path="$(_write_os_release <<'EOF'
PRETTY_NAME="Ubuntu 24.04 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
UBUNTU_CODENAME=noble
EOF
)"
  OMES_OS_RELEASE_FILE="$path" detect_os
  [ "$OMES_OS_ID" = "ubuntu" ]
  [ "$OMES_OS_VERSION_ID" = "24.04" ]
  [ "$OMES_OS_CODENAME" = "noble" ]
  [ "$OMES_OS_LIKE" = "debian" ]
  [ "$OMES_OS_PRETTY" = "Ubuntu 24.04 LTS" ]
}

@test "detect_os falls back to VERSION_CODENAME when UBUNTU_CODENAME is absent" {
  local path
  path="$(_write_os_release <<'EOF'
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
NAME="Debian GNU/Linux"
VERSION_ID="12"
VERSION_CODENAME=bookworm
ID=debian
EOF
)"
  OMES_OS_RELEASE_FILE="$path" detect_os
  [ "$OMES_OS_CODENAME" = "bookworm" ]
}

@test "detect_os reports unknown defaults for an unreadable file" {
  run env OMES_OS_RELEASE_FILE="${OMES_TEST_TMPDIR}/does-not-exist" bash -c '
    source "'"${OMES_TEST_ROOT}"'/lib/omes/core.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/detect.sh"
    detect_os || true
    echo "$OMES_OS_ID"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "unknown" ]
}

@test "detect_arch maps x86_64 to amd64" {
  run bash -c '
    uname() { echo x86_64; }
    export -f uname
    source "'"${OMES_TEST_ROOT}"'/lib/omes/core.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/detect.sh"
    detect_arch
    echo "$OMES_ARCH"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "amd64" ]
}

@test "detect_arch maps aarch64 to arm64" {
  run bash -c '
    uname() { echo aarch64; }
    export -f uname
    source "'"${OMES_TEST_ROOT}"'/lib/omes/core.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/detect.sh"
    detect_arch
    echo "$OMES_ARCH"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "arm64" ]
}

@test "detect_arch reports unsupported for an unknown machine type" {
  run bash -c '
    uname() { echo riscv64; }
    export -f uname
    source "'"${OMES_TEST_ROOT}"'/lib/omes/core.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/detect.sh"
    detect_arch || true
    echo "$OMES_ARCH"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "unsupported" ]
}

@test "detect_tier: ubuntu 24.04 amd64 is tier1" {
  local path
  path="$(_write_os_release <<'EOF'
PRETTY_NAME="Ubuntu 24.04 LTS"
VERSION_ID="24.04"
ID=ubuntu
UBUNTU_CODENAME=noble
EOF
)"
  OMES_OS_RELEASE_FILE="$path" detect_os
  OMES_ARCH="amd64"
  run detect_tier
  [ "$status" -eq 0 ]
  [ "$output" = "tier1" ]
}

@test "detect_tier: ubuntu 22.04 amd64 is tier2" {
  local path
  path="$(_write_os_release <<'EOF'
PRETTY_NAME="Ubuntu 22.04.4 LTS"
VERSION_ID="22.04"
ID=ubuntu
UBUNTU_CODENAME=jammy
EOF
)"
  OMES_OS_RELEASE_FILE="$path" detect_os
  OMES_ARCH="amd64"
  run detect_tier
  [ "$status" -eq 0 ]
  [ "$output" = "tier2" ]
}

@test "detect_tier: linuxmint 22.1 amd64 is tier1" {
  local path
  path="$(_write_os_release <<'EOF'
PRETTY_NAME="Linux Mint 22.1"
VERSION_ID="22.1"
ID=linuxmint
ID_LIKE="ubuntu debian"
UBUNTU_CODENAME=noble
EOF
)"
  OMES_OS_RELEASE_FILE="$path" detect_os
  OMES_ARCH="amd64"
  run detect_tier
  [ "$status" -eq 0 ]
  [ "$output" = "tier1" ]
}

@test "detect_tier: linuxmint 22 (no point release) is tier1" {
  local path
  path="$(_write_os_release <<'EOF'
PRETTY_NAME="Linux Mint 22"
VERSION_ID="22"
ID=linuxmint
ID_LIKE="ubuntu debian"
UBUNTU_CODENAME=noble
EOF
)"
  OMES_OS_RELEASE_FILE="$path" detect_os
  OMES_ARCH="amd64"
  run detect_tier
  [ "$status" -eq 0 ]
  [ "$output" = "tier1" ]
}

@test "detect_tier: linuxmint 21.3 is unsupported" {
  local path
  path="$(_write_os_release <<'EOF'
PRETTY_NAME="Linux Mint 21.3"
VERSION_ID="21.3"
ID=linuxmint
ID_LIKE="ubuntu debian"
UBUNTU_CODENAME=jammy
EOF
)"
  OMES_OS_RELEASE_FILE="$path" detect_os
  OMES_ARCH="amd64"
  run detect_tier
  [ "$status" -eq 1 ]
  [ "$output" = "unsupported" ]
}

@test "detect_tier: debian 12 is unsupported" {
  local path
  path="$(_write_os_release <<'EOF'
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
VERSION_ID="12"
ID=debian
EOF
)"
  OMES_OS_RELEASE_FILE="$path" detect_os
  OMES_ARCH="amd64"
  run detect_tier
  [ "$status" -eq 1 ]
  [ "$output" = "unsupported" ]
}

@test "detect_tier: fedora is unsupported" {
  local path
  path="$(_write_os_release <<'EOF'
PRETTY_NAME="Fedora Linux 40"
VERSION_ID="40"
ID=fedora
EOF
)"
  OMES_OS_RELEASE_FILE="$path" detect_os
  OMES_ARCH="amd64"
  run detect_tier
  [ "$status" -eq 1 ]
  [ "$output" = "unsupported" ]
}

@test "detect_tier: arm64 on a supported OS is tier3" {
  local path
  path="$(_write_os_release <<'EOF'
PRETTY_NAME="Ubuntu 24.04 LTS"
VERSION_ID="24.04"
ID=ubuntu
UBUNTU_CODENAME=noble
EOF
)"
  OMES_OS_RELEASE_FILE="$path" detect_os
  OMES_ARCH="arm64"
  run detect_tier
  [ "$status" -eq 0 ]
  [ "$output" = "tier3" ]
}

@test "detect_tier: arm64 on an unsupported OS stays unsupported" {
  local path
  path="$(_write_os_release <<'EOF'
PRETTY_NAME="Fedora Linux 40"
VERSION_ID="40"
ID=fedora
EOF
)"
  OMES_OS_RELEASE_FILE="$path" detect_os
  OMES_ARCH="arm64"
  run detect_tier
  [ "$status" -eq 1 ]
  [ "$output" = "unsupported" ]
}

@test "detect_network honors OMES_ASSUME_ONLINE" {
  local out
  out=$(OMES_ASSUME_ONLINE=1 bash -c '
    source "'"${OMES_TEST_ROOT}"'/lib/omes/core.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/detect.sh"
    detect_network
    echo "$OMES_NETWORK"
  ')
  [ "$out" = "online" ]
}

@test "detect_network honors OMES_ASSUME_OFFLINE" {
  local out
  # test_helper.bash exports OMES_ASSUME_ONLINE=1 by default; explicitly
  # clear it here since detect_network checks ONLINE before OFFLINE.
  out=$(OMES_ASSUME_ONLINE=0 OMES_ASSUME_OFFLINE=1 bash -c '
    source "'"${OMES_TEST_ROOT}"'/lib/omes/core.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/detect.sh"
    detect_network || true
    echo "$OMES_NETWORK"
  ')
  [ "$out" = "offline" ]
}
