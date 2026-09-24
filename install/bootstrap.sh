#!/usr/bin/env bash
# shellcheck shell=bash
# install/bootstrap.sh - safe entry point for OMES.
#
# Contract:
# 1. Distinguishes explicit release channels: stable (default), rc, edge, dev.
# 2. Stable defaults to an immutable pinned release tag, not mutable main.
# 3. Validates repository origin and refuses to clobber dirty/unexpected checkouts.
# 4. Never suppresses git fetch/checkout failures (no `|| true`).
# 5. Verifies resolved commit SHA before symlinking or executing bin/omes.
# 6. Never mutates the system beyond installing `git` via apt when missing (with
#    explicit warning) and managing OMES_INSTALL_DIR/OMES_BIN_DIR.
# 7. Hands off to `bin/omes check` read-only.

set -Eeuo pipefail
IFS=$'\n\t'

OMES_DEFAULT_STABLE_TAG="v0.4.0"
OMES_DEFAULT_REPO_URL="https://github.com/ahliweb/omes.git"

# Configuration defaults
OMES_CHANNEL="${OMES_CHANNEL:-stable}"
OMES_REF="${OMES_REF:-}"
OMES_REPO_URL="${OMES_REPO_URL:-$OMES_DEFAULT_REPO_URL}"
OMES_INSTALL_DIR="${OMES_INSTALL_DIR:-$HOME/.local/share/omes}"
OMES_BIN_DIR="${OMES_BIN_DIR:-$HOME/.local/bin}"
OMES_ALLOW_UNVERIFIED_ORIGIN="${OMES_ALLOW_UNVERIFIED_ORIGIN:-0}"
OMES_JSON="${OMES_JSON:-0}"
OMES_DRY_RUN="${OMES_DRY_RUN:-0}"
OMES_SKIP_CHECK="${OMES_SKIP_CHECK:-0}"
OMES_OS_RELEASE_FILE="${OMES_OS_RELEASE_FILE:-/etc/os-release}"

log() {
  if [[ "${OMES_JSON:-0}" -eq 1 ]]; then
    printf '[omes-bootstrap] %s\n' "$*" >&2
  else
    printf '[omes-bootstrap] %s\n' "$*"
  fi
}
warn() { printf '[omes-bootstrap] WARN %s\n' "$*" >&2; }
err() { printf '[omes-bootstrap] ERROR %s\n' "$*" >&2; }
die() {
  local code="$1"
  shift
  err "$*"
  exit "$code"
}

_run_git() {
  if [[ "${OMES_JSON:-0}" -eq 1 ]]; then
    git "$@" >&2
  else
    git "$@"
  fi
}

usage() {
  cat <<'EOF'
Usage: install/bootstrap.sh [OPTIONS]

Safe entry point for OMES installation.

Options:
  --channel <stable|rc|edge|dev>
                        Release channel to install (default: stable).
                          stable: verified immutable release tag (default: latest stable).
                          rc:     explicit release candidate tag (--ref required).
                          edge:   latest git main resolved to single commit.
                          dev:    local development checkout; preserves working tree.
  --ref <ref>           Explicit git ref/tag/branch/commit to install.
  --repo-url <url>      Git repository URL (default: https://github.com/ahliweb/omes.git).
  --install-dir <path>  Target checkout directory (default: ~/.local/share/omes).
  --bin-dir <path>      Directory for bin/omes symlink (default: ~/.local/bin).
  --allow-unverified-origin
                        Allow existing checkout origin to differ from --repo-url.
  --dry-run             Resolve ref and simulate steps without modifying filesystem.
  --skip-check          Skip running 'bin/omes check' after bootstrap.
  --json                Emit machine-readable JSON provenance to stdout.
  -h, --help            Show this help text.
EOF
}

# ---------------------------------------------------------------------------
# CLI arguments parsing
# ---------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    --channel)
      [[ $# -ge 2 ]] || die 2 "--channel requires an argument"
      OMES_CHANNEL="$2"
      shift 2
      ;;
    --ref)
      [[ $# -ge 2 ]] || die 2 "--ref requires an argument"
      OMES_REF="$2"
      shift 2
      ;;
    --repo-url)
      [[ $# -ge 2 ]] || die 2 "--repo-url requires an argument"
      OMES_REPO_URL="$2"
      shift 2
      ;;
    --install-dir)
      [[ $# -ge 2 ]] || die 2 "--install-dir requires an argument"
      OMES_INSTALL_DIR="$2"
      shift 2
      ;;
    --bin-dir)
      [[ $# -ge 2 ]] || die 2 "--bin-dir requires an argument"
      OMES_BIN_DIR="$2"
      shift 2
      ;;
    --allow-unverified-origin)
      OMES_ALLOW_UNVERIFIED_ORIGIN=1
      shift
      ;;
    --dry-run)
      OMES_DRY_RUN=1
      shift
      ;;
    --skip-check)
      OMES_SKIP_CHECK=1
      shift
      ;;
    --json)
      OMES_JSON=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die 2 "unknown option: $1 (see --help)"
      ;;
  esac
done

# ---------------------------------------------------------------------------
# bash version check
# ---------------------------------------------------------------------------

if [[ -z "${BASH_VERSINFO:-}" ]] || [[ "${BASH_VERSINFO[0]}" -lt 4 ]]; then
  die 1 "bash >= 4 is required (found: ${BASH_VERSION:-unknown})"
fi

# ---------------------------------------------------------------------------
# Channel validation
# ---------------------------------------------------------------------------

case "$OMES_CHANNEL" in
  stable | rc | edge | dev) ;;
  *) die 2 "invalid channel: '${OMES_CHANNEL}' (expected: stable, rc, edge, or dev)" ;;
esac

# ---------------------------------------------------------------------------
# Early OS detection (no mutation)
# ---------------------------------------------------------------------------

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
      26.04 | 26.04.*) tier="tier1" ;;
      24.04 | 24.04.*) tier="tier1" ;;
      22.04 | 22.04.*) tier="tier2" ;;
    esac
    ;;
  linuxmint)
    case "$os_version_id" in
      22 | 22.*) tier="tier1" ;;
    esac
    ;;
esac

if [[ "$tier" == "unsupported" ]]; then
  die 3 "unsupported platform: ${os_id} ${os_version_id} (supported: Ubuntu Server 26.04/24.04/22.04 LTS, Linux Mint 22.x)"
fi

log "detected supported platform: ${os_id} ${os_version_id} (tier=${tier})"

# ---------------------------------------------------------------------------
# Ensure git is present
# ---------------------------------------------------------------------------

if ! command -v git >/dev/null 2>&1; then
  if [[ "$OMES_DRY_RUN" -eq 1 ]]; then
    log "[dry-run] git is missing; would install git via apt-get"
  else
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
fi

# ---------------------------------------------------------------------------
# Channel ref rules and selection
# ---------------------------------------------------------------------------

requested_ref="$OMES_REF"

case "$OMES_CHANNEL" in
  stable)
    if [[ -z "$requested_ref" ]]; then
      requested_ref="$OMES_DEFAULT_STABLE_TAG"
    elif [[ "$requested_ref" == *"-rc"* ]]; then
      die 2 "pre-release candidate ref '${requested_ref}' requested under stable channel; use --channel rc"
    fi
    log "channel 'stable' selected: target release ref '${requested_ref}'"
    ;;
  rc)
    if [[ -z "$requested_ref" ]]; then
      die 2 "channel 'rc' requires an explicit pre-release candidate ref (e.g. --ref v0.4.0-rc1)"
    elif [[ "$requested_ref" != *"-rc"* ]]; then
      die 2 "channel 'rc' ref '${requested_ref}' does not appear to be a pre-release candidate (expected '-rc' in tag name)"
    fi
    warn "channel 'rc' selected: target pre-release ref '${requested_ref}'"
    ;;
  edge)
    if [[ -z "$requested_ref" ]]; then
      requested_ref="main"
    fi
    warn "channel 'edge' selected: rolling development ref '${requested_ref}' will be resolved to single commit"
    ;;
  dev)
    if [[ -z "$requested_ref" ]]; then
      requested_ref="HEAD"
    fi
    log "channel 'dev' selected: using local working tree"
    ;;
esac

# ---------------------------------------------------------------------------
# URL normalization & origin validation
# ---------------------------------------------------------------------------

_normalize_url() {
  local url="$1"
  url="${url%/}"
  url="${url%.git}"
  if [[ "$url" =~ ^git@([^:]+):(.+)$ ]]; then
    url="https://${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
  elif [[ "$url" =~ ^ssh://git@([^/]+)/(.+)$ ]]; then
    url="https://${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
  fi
  printf '%s' "$url"
}

is_dirty=false

if [[ -d "${OMES_INSTALL_DIR}/.git" ]]; then
  # 1. Origin verification
  existing_origin="$(git -C "$OMES_INSTALL_DIR" config --get remote.origin.url 2>/dev/null || true)"
  if [[ -n "$existing_origin" ]]; then
    norm_existing="$(_normalize_url "$existing_origin")"
    norm_expected="$(_normalize_url "$OMES_REPO_URL")"
    if [[ "$norm_existing" != "$norm_expected" ]]; then
      if [[ "$OMES_ALLOW_UNVERIFIED_ORIGIN" -ne 1 ]]; then
        die 4 "existing checkout origin '${existing_origin}' does not match expected '${OMES_REPO_URL}'; pass --allow-unverified-origin to override, or remove/migrate ${OMES_INSTALL_DIR}"
      else
        warn "existing checkout origin '${existing_origin}' differs from expected '${OMES_REPO_URL}' (allowed by override)"
      fi
    fi
  elif [[ "$OMES_ALLOW_UNVERIFIED_ORIGIN" -ne 1 ]]; then
    die 4 "existing checkout at ${OMES_INSTALL_DIR} has no configured remote.origin.url; aborting (use --allow-unverified-origin to override)"
  fi

  # 2. Dirty working tree detection (ignoring our metadata file)
  status_porcelain="$(git -C "$OMES_INSTALL_DIR" status --porcelain -- ':!.omes-channel.json' 2>/dev/null || true)"
  if [[ -n "$status_porcelain" ]]; then
    if [[ "$OMES_CHANNEL" == "dev" ]]; then
      is_dirty=true
      warn "existing checkout at ${OMES_INSTALL_DIR} has uncommitted modifications; preserving dev checkout without updating"
    else
      die 5 "existing checkout at ${OMES_INSTALL_DIR} has uncommitted modifications or untracked files; refusing to overwrite. Use --channel dev to preserve local development tree or clean the working tree."
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Resolve and checkout
# ---------------------------------------------------------------------------

resolved_sha=""
resolved_ref=""

if [[ "$OMES_DRY_RUN" -eq 1 ]]; then
  log "[dry-run] channel: ${OMES_CHANNEL}"
  log "[dry-run] requested ref: ${requested_ref}"
  log "[dry-run] repo URL: ${OMES_REPO_URL}"
  log "[dry-run] install directory: ${OMES_INSTALL_DIR}"
  log "[dry-run] bin directory: ${OMES_BIN_DIR}"
  resolved_sha="0000000000000000000000000000000000000000"
  resolved_ref="${requested_ref}"
else
  mkdir -p "$(dirname "$OMES_INSTALL_DIR")"

  if [[ "$OMES_CHANNEL" == "dev" ]]; then
    if [[ ! -d "${OMES_INSTALL_DIR}/.git" ]]; then
      die 6 "channel 'dev' requires an existing git checkout at ${OMES_INSTALL_DIR}"
    fi
    resolved_sha="$(git -C "$OMES_INSTALL_DIR" rev-parse HEAD)"
    resolved_ref="HEAD"
  elif [[ -d "${OMES_INSTALL_DIR}/.git" ]]; then
    log "updating existing checkout at ${OMES_INSTALL_DIR} (channel: ${OMES_CHANNEL}, ref: ${requested_ref})"

    if [[ "$OMES_CHANNEL" == "edge" ]]; then
      # Edge: fetch target branch and resolve exact SHA
      _run_git -C "$OMES_INSTALL_DIR" fetch origin "${requested_ref}" || die 6 "failed to fetch '${requested_ref}' from origin"
      resolved_sha="$(git -C "$OMES_INSTALL_DIR" rev-parse "FETCH_HEAD^{commit}")"
      resolved_ref="refs/heads/${requested_ref}"
      _run_git -C "$OMES_INSTALL_DIR" checkout -q --detach "$resolved_sha" || die 7 "failed to checkout commit ${resolved_sha}"
    else
      # Stable or RC: fetch tags and resolve tag commit
      _run_git -C "$OMES_INSTALL_DIR" fetch origin --tags --prune || die 6 "failed to fetch tags from origin"
      resolved_sha="$(git -C "$OMES_INSTALL_DIR" rev-parse "${requested_ref}^{commit}" 2>/dev/null)" || die 6 "failed to resolve tag '${requested_ref}'"
      resolved_ref="refs/tags/${requested_ref}"
      _run_git -C "$OMES_INSTALL_DIR" checkout -q --detach "$resolved_sha" || die 7 "failed to checkout ref '${requested_ref}'"
    fi
  else
    log "cloning ${OMES_REPO_URL} into ${OMES_INSTALL_DIR}"
    _run_git clone -q "$OMES_REPO_URL" "$OMES_INSTALL_DIR" || die 6 "failed to clone ${OMES_REPO_URL}"

    if [[ "$OMES_CHANNEL" == "edge" ]]; then
      resolved_sha="$(git -C "$OMES_INSTALL_DIR" rev-parse "origin/${requested_ref}^{commit}" 2>/dev/null || git -C "$OMES_INSTALL_DIR" rev-parse "${requested_ref}^{commit}")" || die 6 "failed to resolve ref '${requested_ref}'"
      resolved_ref="refs/heads/${requested_ref}"
    else
      resolved_sha="$(git -C "$OMES_INSTALL_DIR" rev-parse "${requested_ref}^{commit}" 2>/dev/null)" || die 6 "failed to resolve ref '${requested_ref}'"
      resolved_ref="refs/tags/${requested_ref}"
    fi
    _run_git -C "$OMES_INSTALL_DIR" checkout -q --detach "$resolved_sha" || die 7 "failed to checkout ${resolved_sha}"
  fi

  # Post-checkout SHA verification
  actual_sha="$(git -C "$OMES_INSTALL_DIR" rev-parse HEAD 2>/dev/null || true)"
  if [[ "$actual_sha" != "$resolved_sha" ]]; then
    die 8 "resolved commit SHA mismatch: expected ${resolved_sha}, checked out ${actual_sha}"
  fi
fi

# ---------------------------------------------------------------------------
# Symlink bin/omes onto PATH & record provenance
# ---------------------------------------------------------------------------

timestamp_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u)"

if [[ "$OMES_DRY_RUN" -ne 1 ]]; then
  # Record channel provenance metadata
  cat >"${OMES_INSTALL_DIR}/.omes-channel.json" <<EOF
{
  "channel": "${OMES_CHANNEL}",
  "requested_ref": "${requested_ref}",
  "resolved_ref": "${resolved_ref}",
  "resolved_sha": "${resolved_sha}",
  "repo_url": "${OMES_REPO_URL}",
  "install_dir": "${OMES_INSTALL_DIR}",
  "bin_path": "${OMES_BIN_DIR}/omes",
  "timestamp_utc": "${timestamp_utc}",
  "is_dirty": ${is_dirty}
}
EOF

  mkdir -p "$OMES_BIN_DIR"
  ln -sf "${OMES_INSTALL_DIR}/bin/omes" "${OMES_BIN_DIR}/omes"
  log "symlinked ${OMES_BIN_DIR}/omes -> ${OMES_INSTALL_DIR}/bin/omes"

  case ":${PATH}:" in
    *":${OMES_BIN_DIR}:"*) ;;
    *) warn "${OMES_BIN_DIR} is not on PATH; add it to your shell profile or invoke ${OMES_BIN_DIR}/omes directly" ;;
  esac
fi

# ---------------------------------------------------------------------------
# JSON output if requested
# ---------------------------------------------------------------------------

if [[ "$OMES_JSON" -eq 1 ]]; then
  cat <<EOF
{
  "channel": "${OMES_CHANNEL}",
  "requested_ref": "${requested_ref}",
  "resolved_ref": "${resolved_ref}",
  "resolved_sha": "${resolved_sha}",
  "repo_url": "${OMES_REPO_URL}",
  "install_dir": "${OMES_INSTALL_DIR}",
  "bin_path": "${OMES_BIN_DIR}/omes",
  "timestamp_utc": "${timestamp_utc}",
  "is_dirty": ${is_dirty},
  "dry_run": $([[ "$OMES_DRY_RUN" -eq 1 ]] && echo "true" || echo "false")
}
EOF
fi

# ---------------------------------------------------------------------------
# Hand off to `omes check`
# ---------------------------------------------------------------------------

if [[ "$OMES_DRY_RUN" -ne 1 && "$OMES_SKIP_CHECK" -ne 1 ]]; then
  log "running: omes check"
  if [[ "$OMES_JSON" -eq 1 ]]; then
    "${OMES_BIN_DIR}/omes" check >&2 || true
  else
    "${OMES_BIN_DIR}/omes" check || true
  fi

  log "next steps:"
  log "  ${OMES_BIN_DIR}/omes check --profile server"
  log "  sudo ${OMES_BIN_DIR}/omes install --profile server --dry-run --yes"
  log "  sudo ${OMES_BIN_DIR}/omes install --profile server"
fi
