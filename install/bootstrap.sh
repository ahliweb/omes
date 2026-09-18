#!/usr/bin/env bash
# shellcheck shell=bash
# install/bootstrap.sh - safe `curl | bash` entry point for OMES.
#
# Contract: this script never mutates the system beyond (a) installing
# `git` via apt when missing, with an explicit sudo prompt printed before
# running, and (b) cloning/updating the OMES repository and symlinking
# ~/.local/bin/omes. It does not install any OMES module itself; it hands
# off to `omes check` and prints the operator's next command. It never
# pipes a network response directly into a shell beyond this file itself
# (no nested `curl ... | bash`).

set -Eeuo pipefail
IFS=$'\n\t'

log() { printf '[omes-bootstrap] %s\n' "$*"; }
warn() { printf '[omes-bootstrap] WARN %s\n' "$*" >&2; }
err() { printf '[omes-bootstrap] ERROR %s\n' "$*" >&2; }
die() {
  local code="$1"
  shift
  err "$*"
  exit "$code"
}

# ---------------------------------------------------------------------------
# bash version check
# ---------------------------------------------------------------------------

if [[ -z "${BASH_VERSINFO:-}" ]] || [[ "${BASH_VERSINFO[0]}" -lt 4 ]]; then
  die 1 "bash >= 4 is required (found: ${BASH_VERSION:-unknown})"
fi

# ---------------------------------------------------------------------------
# Early OS detection (no mutation) - self-contained, does not depend on a
# cloned checkout since none exists yet at this point.
# ---------------------------------------------------------------------------

OMES_OS_RELEASE_FILE="${OMES_OS_RELEASE_FILE:-/etc/os-release}"
os_id="unknown"
os_version_id=""

if [[ -r "$OMES_OS_RELEASE_FILE" ]]; then
  ID=""
  VERSION_ID=""
  # shellcheck disable=SC1090
  source "$OMES_OS_RELEASE_FILE"
  os_id="${ID:-unknown}"
  os_version_id="${VERSION_ID:-}"
fi

tier="unsupported"
case "$os_id" in
  ubuntu)
    case "$os_version_id" in
      24.04) tier="tier1" ;;
      22.04) tier="tier2" ;;
    esac
    ;;
  linuxmint)
    case "$os_version_id" in
      22 | 22.*) tier="tier1" ;;
    esac
    ;;
esac

if [[ "$tier" == "unsupported" ]]; then
  die 3 "unsupported platform: ${os_id} ${os_version_id} (supported: Ubuntu Server 24.04/22.04 LTS, Linux Mint 22.x)"
fi

log "detected supported platform: ${os_id} ${os_version_id} (tier=${tier})"

# ---------------------------------------------------------------------------
# Ensure git is present (only privileged step besides the clone itself)
# ---------------------------------------------------------------------------

if ! command -v git >/dev/null 2>&1; then
  if ! command -v apt-get >/dev/null 2>&1; then
    die 1 "git is not installed and apt-get is not available to install it"
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    die 1 "git is not installed and sudo is not available; install git manually and re-run"
  fi

  log "git is not installed; about to run the following privileged commands:"
  log "  sudo apt-get update"
  log "  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git"
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git
fi

# ---------------------------------------------------------------------------
# Clone (or update) the pinned ref
# ---------------------------------------------------------------------------

OMES_REPO_URL="${OMES_REPO_URL:-https://github.com/ahliweb/omes.git}"
OMES_REF="${OMES_REF:-main}"
OMES_INSTALL_DIR="${OMES_INSTALL_DIR:-$HOME/.local/share/omes}"
OMES_BIN_DIR="${OMES_BIN_DIR:-$HOME/.local/bin}"

mkdir -p "$(dirname "$OMES_INSTALL_DIR")"

if [[ -d "${OMES_INSTALL_DIR}/.git" ]]; then
  log "updating existing checkout at ${OMES_INSTALL_DIR} (ref: ${OMES_REF})"
  git -C "$OMES_INSTALL_DIR" fetch origin
  git -C "$OMES_INSTALL_DIR" checkout "$OMES_REF"
  git -C "$OMES_INSTALL_DIR" pull --ff-only origin "$OMES_REF" 2>/dev/null || true
else
  log "cloning ${OMES_REPO_URL} (ref: ${OMES_REF}) into ${OMES_INSTALL_DIR}"
  git clone "$OMES_REPO_URL" "$OMES_INSTALL_DIR"
  git -C "$OMES_INSTALL_DIR" checkout "$OMES_REF"
fi

# ---------------------------------------------------------------------------
# Symlink bin/omes onto PATH
# ---------------------------------------------------------------------------

mkdir -p "$OMES_BIN_DIR"
ln -sf "${OMES_INSTALL_DIR}/bin/omes" "${OMES_BIN_DIR}/omes"
log "symlinked ${OMES_BIN_DIR}/omes -> ${OMES_INSTALL_DIR}/bin/omes"

case ":${PATH}:" in
  *":${OMES_BIN_DIR}:"*) ;;
  *) warn "${OMES_BIN_DIR} is not on PATH; add it to your shell profile or invoke ${OMES_BIN_DIR}/omes directly" ;;
esac

# ---------------------------------------------------------------------------
# Hand off to `omes check` and print the next command
# ---------------------------------------------------------------------------

log "running: omes check"
"${OMES_BIN_DIR}/omes" check || true

log "next steps:"
log "  ${OMES_BIN_DIR}/omes check --profile server"
log "  sudo ${OMES_BIN_DIR}/omes install --profile server --dry-run --yes"
log "  sudo ${OMES_BIN_DIR}/omes install --profile server"
