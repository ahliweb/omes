#!/usr/bin/env bash
# shellcheck shell=bash
# lib/omes/detect.sh - read-only OS/arch/privilege/network/session detection.
#
# Nothing in this file mutates the host. It is meant to be sourced after
# lib/omes/core.sh.

if [[ -n "${OMES_DETECT_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317  # unreachable only when sourced; the `exit`
  # branch is the (correct) fallback for accidental direct execution.
  return 0 2>/dev/null || exit 0
fi
OMES_DETECT_SH_LOADED=1

# detect_os
# Reads ${OMES_OS_RELEASE_FILE:-/etc/os-release} and sets:
#   OMES_OS_ID, OMES_OS_VERSION_ID, OMES_OS_CODENAME (UBUNTU_CODENAME,
#   falling back to VERSION_CODENAME), OMES_OS_LIKE, OMES_OS_PRETTY.
# Returns 1 (but still sets safe defaults) when the file is unreadable.
detect_os() {
  local file="${OMES_OS_RELEASE_FILE:-/etc/os-release}"
  local id="" version_id="" ubuntu_codename="" version_codename="" id_like="" pretty_name=""

  if [[ -r "$file" ]]; then
    local captured
    captured="$(
      ID=""
      VERSION_ID=""
      UBUNTU_CODENAME=""
      VERSION_CODENAME=""
      ID_LIKE=""
      PRETTY_NAME=""
      # shellcheck disable=SC1090
      source "$file" 2>/dev/null || true
      printf 'id=%q\n' "$ID"
      printf 'version_id=%q\n' "$VERSION_ID"
      printf 'ubuntu_codename=%q\n' "$UBUNTU_CODENAME"
      printf 'version_codename=%q\n' "$VERSION_CODENAME"
      printf 'id_like=%q\n' "$ID_LIKE"
      printf 'pretty_name=%q\n' "$PRETTY_NAME"
    )"
    eval "$captured"
  fi

  OMES_OS_ID="${id:-unknown}"
  OMES_OS_VERSION_ID="${version_id:-}"
  OMES_OS_CODENAME="${ubuntu_codename:-${version_codename:-}}"
  OMES_OS_LIKE="${id_like:-}"
  OMES_OS_PRETTY="${pretty_name:-${id:-unknown}}"
  export OMES_OS_ID OMES_OS_VERSION_ID OMES_OS_CODENAME OMES_OS_LIKE OMES_OS_PRETTY

  [[ -r "$file" ]]
}

# detect_arch
# Sets OMES_ARCH to amd64/arm64/unsupported based on `uname -m`.
detect_arch() {
  local machine
  machine="$(uname -m)"
  case "$machine" in
    x86_64)
      OMES_ARCH="amd64"
      ;;
    aarch64 | arm64)
      OMES_ARCH="arm64"
      ;;
    *)
      OMES_ARCH="unsupported"
      ;;
  esac
  export OMES_ARCH
  [[ "$OMES_ARCH" != "unsupported" ]]
}

# detect_tier
# Sets and prints OMES_OS_TIER to one of tier1/tier2/tier3/unsupported:
#   - Ubuntu 26.04 / 24.04    -> tier1
#   - Ubuntu 22.04            -> tier2
#   - Linux Mint 22 / 22.*    -> tier1
#   - arm64 on a supported OS -> tier3 (overrides the amd64 tier above)
#   - everything else         -> unsupported
detect_tier() {
  if [[ -z "${OMES_OS_ID:-}" ]]; then
    detect_os || true
  fi
  if [[ -z "${OMES_ARCH:-}" ]]; then
    detect_arch || true
  fi

  local tier="unsupported"
  local supported=0

  case "${OMES_OS_ID:-}" in
    ubuntu)
      case "${OMES_OS_VERSION_ID:-}" in
        26.04 | 26.04.*)
          tier="tier1"
          supported=1
          ;;
        24.04 | 24.04.*)
          tier="tier1"
          supported=1
          ;;
        22.04 | 22.04.*)
          tier="tier2"
          supported=1
          ;;
      esac
      ;;
    linuxmint)
      case "${OMES_OS_VERSION_ID:-}" in
        22 | 22.*)
          tier="tier1"
          supported=1
          ;;
      esac
      ;;
  esac

  if [[ "$supported" -eq 1 ]] && [[ "${OMES_ARCH:-}" == "arm64" ]]; then
    tier="tier3"
  fi

  if [[ "$supported" -eq 0 ]]; then
    tier="unsupported"
  fi

  OMES_OS_TIER="$tier"
  export OMES_OS_TIER
  printf '%s\n' "$tier"
  [[ "$tier" != "unsupported" ]]
}

# detect_virt
# Sets/prints OMES_VIRT: wsl, a systemd-detect-virt value, or "unknown".
detect_virt() {
  if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
    OMES_VIRT="wsl"
  elif command -v systemd-detect-virt >/dev/null 2>&1; then
    OMES_VIRT="$(systemd-detect-virt 2>/dev/null || true)"
    OMES_VIRT="${OMES_VIRT:-none}"
  else
    OMES_VIRT="unknown"
  fi
  export OMES_VIRT
  printf '%s\n' "$OMES_VIRT"
}

# detect_session
# Sets/prints OMES_SESSION to "desktop" or "server", based on
# `systemctl get-default` (graphical.target) or XDG_SESSION_TYPE.
detect_session() {
  local session="server"

  if command -v systemctl >/dev/null 2>&1; then
    local default_target
    default_target="$(systemctl get-default 2>/dev/null || true)"
    if [[ "$default_target" == "graphical.target" ]]; then
      session="desktop"
    fi
  fi

  if [[ "$session" == "server" ]] && [[ -n "${XDG_SESSION_TYPE:-}" ]]; then
    session="desktop"
  fi

  OMES_SESSION="$session"
  export OMES_SESSION
  printf '%s\n' "$session"
}

# detect_privilege
# Sets/prints OMES_PRIVILEGE to "root", "sudo" (passwordless sudo
# available), or "user".
detect_privilege() {
  if omes_is_root; then
    OMES_PRIVILEGE="root"
  elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    OMES_PRIVILEGE="sudo"
  else
    OMES_PRIVILEGE="user"
  fi
  export OMES_PRIVILEGE
  printf '%s\n' "$OMES_PRIVILEGE"
}

# detect_network
# Sets/prints OMES_NETWORK to "online" or "offline". Returns 0 when online,
# 1 when offline. Honors OMES_ASSUME_ONLINE=1 / OMES_ASSUME_OFFLINE=1 so
# tests never depend on real connectivity.
detect_network() {
  if [[ "${OMES_ASSUME_ONLINE:-0}" == "1" ]]; then
    OMES_NETWORK="online"
    export OMES_NETWORK
    return 0
  fi
  if [[ "${OMES_ASSUME_OFFLINE:-0}" == "1" ]]; then
    OMES_NETWORK="offline"
    export OMES_NETWORK
    return 1
  fi

  local host
  for host in deb.debian.org archive.ubuntu.com; do
    if command -v getent >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
      if timeout 5 getent hosts "$host" >/dev/null 2>&1; then
        OMES_NETWORK="online"
        export OMES_NETWORK
        return 0
      fi
    elif command -v curl >/dev/null 2>&1; then
      if curl --max-time 5 --head --silent --fail "https://${host}" >/dev/null 2>&1; then
        OMES_NETWORK="online"
        export OMES_NETWORK
        return 0
      fi
    fi
  done

  OMES_NETWORK="offline"
  export OMES_NETWORK
  return 1
}

# detect_disk_free_mb <path>
# Prints free space on <path> in megabytes (0 on error).
detect_disk_free_mb() {
  local path="${1:-/}"
  local free
  free="$(df -Pm "$path" 2>/dev/null | awk 'NR==2 {print $4}' || true)"
  printf '%s\n' "${free:-0}"
}

# detect_mem_mb
# Prints total system memory in megabytes (0 on error).
detect_mem_mb() {
  local mem
  mem="$(awk '/^MemTotal:/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || true)"
  printf '%s\n' "${mem:-0}"
}

# detect_gpu
# Prints a short GPU description via lspci, or "unknown" when lspci is
# unavailable or found nothing.
detect_gpu() {
  local gpu=""
  if command -v lspci >/dev/null 2>&1; then
    gpu="$(lspci 2>/dev/null | grep -Ei 'vga|3d|display' | head -n1 | sed -E 's/^[0-9a-f:.]+ //' || true)"
  fi
  printf '%s\n' "${gpu:-unknown}"
}

# detect_display_manager
# Prints the configured display manager name, or "none".
detect_display_manager() {
  local dm="none"

  if [[ -r /etc/X11/default-display-manager ]]; then
    local raw
    raw="$(tr -d '[:space:]' </etc/X11/default-display-manager)"
    if [[ -n "$raw" ]]; then
      dm="$(basename "$raw")"
    fi
  elif command -v systemctl >/dev/null 2>&1; then
    local svc
    for svc in gdm gdm3 lightdm sddm; do
      if systemctl is-enabled "$svc" >/dev/null 2>&1; then
        dm="$svc"
        break
      fi
    done
  fi

  printf '%s\n' "$dm"
}
