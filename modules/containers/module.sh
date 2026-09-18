#!/usr/bin/env bash
# shellcheck shell=bash
# modules/containers/module.sh - Docker Engine, OPTIONAL, from Docker's
# official apt repo. Implements docs/adr/0007-docker-access-policy.md.
#
# The MODULE_* metadata variables below are read by lib/omes/module.sh
# after sourcing this file; ShellCheck cannot see that cross-file usage
# when analyzing this file on its own.
#
# Scope: MODULE_SCOPE=root. NOT applied by the default server profile -
# profiles/server.profile lists it commented out; an operator opts in with
# `omes install --module containers` (or by uncommenting the line). This
# is intentional: Docker group membership is root-equivalent (ADR-0007,
# docs/threat-model.md T13), so nothing about container support should be
# a silent default.
#
# Test isolation: every /etc-rooted path this module writes is resolved
# through _containers_etc_dir(), honoring OMES_ETC_DIR (tests only;
# production always uses the real /etc) - the same override
# modules/security-baseline uses, and the same pattern lib/omes/pkg.sh
# uses for OMES_APT_SOURCES_DIR.

# shellcheck disable=SC2034
MODULE_NAME="containers"
# shellcheck disable=SC2034
MODULE_DESCRIPTION="Docker Engine (optional) via Docker's official apt repo"
# shellcheck disable=SC2034
MODULE_SCOPE="root"
# shellcheck disable=SC2034
MODULE_REQUIRES=(apt-base)
# shellcheck disable=SC2034
MODULE_PROFILES=(server)

CONTAINERS_DOCKER_GPG_URL="https://download.docker.com/linux/ubuntu/gpg"
CONTAINERS_DOCKER_REPO_URI="https://download.docker.com/linux/ubuntu"
CONTAINERS_PACKAGES=(docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin)

# ---------------------------------------------------------------------------
# Path helpers (OMES_ETC_DIR override for tests)
# ---------------------------------------------------------------------------

_containers_etc_dir() {
  printf '%s\n' "${OMES_ETC_DIR:-/etc}"
}

# _containers_keyring_path
# Prints the path Docker's signing key is downloaded to: mode 0644,
# ASCII-armored/PEM, referenced by repo_add's Signed-By (docs/packages.md
# "Signed-By keyring required, mode 0644").
_containers_keyring_path() {
  printf '%s/apt/keyrings/docker.asc\n' "$(_containers_etc_dir)"
}

# _containers_supported_codenames
# Ubuntu codenames Docker's official apt repo currently publishes a suite
# for. Kept as a small, explicit list (rather than an apt-cache probe
# against a not-yet-added repo, which would always report "no candidate")
# per docs/packages.md's repository policy.
_containers_supported_codenames() {
  printf 'noble\njammy\nfocal\nbionic\n'
}

_containers_codename_supported() {
  _containers_supported_codenames | grep -qxF "$1"
}

# ---------------------------------------------------------------------------
# Rootless prerequisites (docs/adr/0007-docker-access-policy.md)
# ---------------------------------------------------------------------------

# _containers_rootless_missing_prereqs
# Prints, one per line, the rootless Docker prerequisites that are NOT
# currently met: the `uidmap`/`dbus-user-session` packages, a resolvable
# invoking user (SUDO_USER), subuid/subgid entries for that user, and a
# systemd user session for that user. Empty output means every
# prerequisite is met. Read-only; safe from module_check.
_containers_rootless_missing_prereqs() {
  local -a missing=()

  pkg_is_installed uidmap || missing+=("package 'uidmap' not installed")
  pkg_is_installed dbus-user-session || missing+=("package 'dbus-user-session' not installed")

  local target="${SUDO_USER:-}"
  if [[ -z "$target" ]]; then
    missing+=("SUDO_USER is empty (run via 'sudo -u <user>' style invocation, not directly as root)")
  else
    if ! getent subuid "$target" >/dev/null 2>&1; then
      missing+=("no /etc/subuid entry for '${target}'")
    fi
    if ! getent subgid "$target" >/dev/null 2>&1; then
      missing+=("no /etc/subgid entry for '${target}'")
    fi
    if ! loginctl show-user "$target" >/dev/null 2>&1; then
      missing+=("no active systemd user session for '${target}' (they must have logged in at least once)")
    fi
  fi

  if [[ "${#missing[@]}" -gt 0 ]]; then
    printf '%s\n' "${missing[@]}"
  fi
}

# ---------------------------------------------------------------------------
# Access policy (docs/adr/0007-docker-access-policy.md)
# ---------------------------------------------------------------------------

# _containers_setup_rootless
# Installs docker-ce-rootless-extras and prints the exact user-level setup
# command when OMES_DOCKER_ROOTLESS=1 (this module's env-var stand-in for
# the brief's future `--rootless` CLI flag) and prerequisites hold. Never
# runs dockerd-rootless-setuptool.sh itself, and never as root.
_containers_setup_rootless() {
  local -a missing=()
  mapfile -t missing < <(_containers_rootless_missing_prereqs)

  if [[ "${#missing[@]}" -gt 0 ]]; then
    log_error "containers: OMES_DOCKER_ROOTLESS=1 requested, but rootless prerequisites are not met: ${missing[*]}"
    return 0
  fi

  pkg_install docker-ce-rootless-extras || return $?

  log_info "containers: rootless prerequisites satisfied for '${SUDO_USER}'"
  log_info "containers: to finish rootless setup, run AS THAT USER (never as root, never via sudo): dockerd-rootless-setuptool.sh install"
  state_set "module.containers.rootless_prereqs_user" "${SUDO_USER}"
  return 0
}

# _containers_setup_group
# Adds the invoking user (SUDO_USER, never root) to the docker group -
# ONLY when OMES_ALLOW_DOCKER_GROUP=1 (set by bin/omes from
# --allow-docker-group) AND confirmation (omes_confirm: auto-yes under
# --yes/OMES_NONINTERACTIVE=1, else an interactive prompt, else refused).
# Refuses outright when SUDO_USER is empty. Logs the root-equivalence
# WARN and records the change in state so module_rollback can undo it.
_containers_setup_group() {
  local user="${SUDO_USER:-}"

  if [[ -z "$user" ]]; then
    log_error "containers: --allow-docker-group requires a real invoking user (SUDO_USER is empty); refusing to add root to the docker group"
    return 0
  fi

  if ! omes_confirm "Add '${user}' to the docker group? This is ROOT-EQUIVALENT access: no sudo is needed to control the Docker daemon, and a docker-group member can bind-mount the host root filesystem and escape any container boundary."; then
    log_error "containers: --allow-docker-group was given but confirmation was declined or unavailable (no tty and not --yes); no group change made"
    return 0
  fi

  if ! omes_run usermod -aG docker "$user"; then
    log_error "containers: failed to add '${user}' to the docker group"
    return 0
  fi

  log_warn "containers: added '${user}' to the docker group - this is ROOT-EQUIVALENT access (effective on their NEXT login/session, e.g. after re-login or 'newgrp docker')"
  state_set "module.containers.docker_group_user" "$user"
  return 0
}

# _containers_apply_access_policy
# Default: no group change, print the 'sudo docker' usage note. Then,
# independently, rootless when opted in, and group membership only when
# explicitly opted in and confirmed.
_containers_apply_access_policy() {
  if omes_dry_run; then
    log_info "[dry-run] access policy: default 'sudo docker' (no group change) unless OMES_DOCKER_ROOTLESS=1 and/or --allow-docker-group is given"
    return 0
  fi

  if [[ "${OMES_DOCKER_ROOTLESS:-0}" == "1" ]]; then
    _containers_setup_rootless
  fi

  if [[ "${OMES_ALLOW_DOCKER_GROUP:-0}" == "1" ]]; then
    _containers_setup_group
  else
    log_info "containers: default Docker access is 'sudo docker' (no group change); e.g. run 'sudo docker ps'"
  fi

  return 0
}

# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------

module_check() {
  if ! command -v apt-get >/dev/null 2>&1; then
    log_error "containers: apt-get not found (requires a Debian/Ubuntu-based system)"
    return 1
  fi
  if ! command -v curl >/dev/null 2>&1; then
    log_error "containers: curl not found (required to download Docker's signing key)"
    return 1
  fi

  local codename="${OMES_OS_CODENAME:-}"
  if [[ -z "$codename" ]]; then
    log_error "containers: OS codename could not be determined"
    return 1
  fi
  if ! _containers_codename_supported "$codename"; then
    log_error "containers: Docker's official apt repo does not publish a suite for codename '${codename}' (supported: $(_containers_supported_codenames | tr '\n' ' '))"
    return 1
  fi

  if [[ "${OMES_OS_ID:-}" == "linuxmint" ]]; then
    log_warn "containers: Linux Mint detected; Docker officially supports Ubuntu only - OMES will use \$UBUNTU_CODENAME (${codename}) for the apt suite. No support parity is claimed (see docs/adr/0007-docker-access-policy.md)."
  fi

  # Informational apt-cache probe: reports whatever the currently
  # configured cache already knows about docker-ce (e.g. a prior apply
  # already added the repo). Never a hard requirement pre-apply - the
  # repo itself is only added during module_apply.
  local candidate
  candidate="$(pkg_candidate_version docker-ce)"
  log_info "containers: docker-ce apt-cache candidate: ${candidate:-none yet (repo not added)}"

  if ! pkg_is_installed docker-ce && ! detect_network; then
    log_error "containers: network is required to add Docker's apt repository and install packages"
    return 1
  fi

  if [[ "${OMES_DOCKER_ROOTLESS:-0}" == "1" ]]; then
    local -a missing=()
    mapfile -t missing < <(_containers_rootless_missing_prereqs)
    if [[ "${#missing[@]}" -gt 0 ]]; then
      log_warn "containers: OMES_DOCKER_ROOTLESS=1 is set, but rootless prerequisites are not currently met: ${missing[*]} (Docker itself will still install; this only affects the rootless extras step)"
    fi
  fi

  return 0
}

module_apply() {
  local codename="${OMES_OS_CODENAME:-}"
  local keyring keyring_dir
  keyring="$(_containers_keyring_path)"
  keyring_dir="$(dirname "$keyring")"

  # A single, whole-function dry-run early return: repo_add's own
  # validation (repo_validate) requires the Signed-By keyring to already
  # exist on disk, which dry-run never creates - so repo_add cannot be
  # called at all in dry-run (not even in its own dry-run-aware branch),
  # only described.
  if omes_dry_run; then
    log_info "[dry-run] would download Docker's signing key to ${keyring}"
    log_info "[dry-run] would add the docker apt repo for suite '${codename}'"
    log_info "[dry-run] would install: ${CONTAINERS_PACKAGES[*]}"
    log_info "[dry-run] would run: systemctl enable --now docker"
    log_info "[dry-run] access policy: default 'sudo docker' (no group change) unless OMES_DOCKER_ROOTLESS=1 and/or --allow-docker-group is given"
    return 0
  fi

  mkdir -p "$keyring_dir"
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/omes-docker-key.XXXXXX")"

  if ! curl -fsSL "$CONTAINERS_DOCKER_GPG_URL" -o "$tmp"; then
    log_error "containers: failed to download Docker's signing key from ${CONTAINERS_DOCKER_GPG_URL}"
    rm -f "$tmp"
    return 1
  fi

  if ! grep -q 'BEGIN PGP PUBLIC KEY BLOCK' "$tmp"; then
    log_error "containers: downloaded key does not look like an ASCII-armored/PEM PGP public key; aborting, nothing installed"
    rm -f "$tmp"
    return 1
  fi

  chmod 644 "$tmp"
  omes_manage_path "$keyring"
  mv -f "$tmp" "$keyring"
  log_info "containers: wrote Docker signing key to ${keyring}"

  if [[ "${OMES_OS_ID:-}" == "linuxmint" ]]; then
    log_warn "containers: Linux Mint detected; using \$UBUNTU_CODENAME (${codename}) for the apt suite - no support parity claimed (see docs/adr/0007-docker-access-policy.md)"
  fi

  if ! repo_add docker "$CONTAINERS_DOCKER_REPO_URI" "$codename" "$keyring" --ubuntu-only; then
    log_error "containers: failed to add Docker's apt repository"
    return 1
  fi

  pkg_apt_update || return $?
  pkg_install "${CONTAINERS_PACKAGES[@]}" || return $?

  if ! omes_run systemctl enable --now docker; then
    log_error "containers: failed to enable/start docker.service"
    return 1
  fi

  _containers_apply_access_policy

  return 0
}

module_verify() {
  if omes_dry_run; then
    log_info "[dry-run] would verify: 'docker version' reachable, docker.service enabled"
    return 0
  fi

  local out
  if ! out="$(docker version 2>&1)"; then
    log_error "containers: 'docker version' failed: ${out}"
    return 1
  fi

  if ! systemctl is-enabled docker >/dev/null 2>&1; then
    log_error "containers: docker.service is not enabled"
    return 1
  fi

  log_info "containers: verified docker is installed, reachable, and enabled"
  return 0
}

module_rollback() {
  log_warn "containers: rollback leaves Docker packages installed; remove manually with: apt-get remove ${CONTAINERS_PACKAGES[*]} docker-ce-rootless-extras"

  local group_user
  group_user="$(state_get "module.containers.docker_group_user" 2>/dev/null || true)"

  if omes_dry_run; then
    if [[ -n "$group_user" ]]; then
      log_info "[dry-run] would stop/disable docker.service, remove the docker apt repo + signing key, and remove '${group_user}' from the docker group"
    else
      log_info "[dry-run] would stop/disable docker.service and remove the docker apt repo + signing key"
    fi
    return 0
  fi

  omes_run systemctl disable --now docker >/dev/null 2>&1 || true

  repo_remove docker

  local keyring
  keyring="$(_containers_keyring_path)"
  if [[ -f "$keyring" ]]; then
    rm -f "$keyring"
    log_info "containers: removed ${keyring}"
  fi

  if [[ -n "$group_user" ]]; then
    if omes_run gpasswd -d "$group_user" docker; then
      log_warn "containers: removed '${group_user}' from the docker group"
    else
      log_warn "containers: failed to remove '${group_user}' from the docker group; remove manually with: gpasswd -d ${group_user} docker"
    fi
    state_unset "module.containers.docker_group_user"
  fi

  state_unset "module.containers.rootless_prereqs_user"

  return 0
}

# module_doctor
# Optional diagnostics (not part of the required module contract; not yet
# wired into `omes doctor`, tracked in #14). Read-only.
module_doctor() {
  local ver
  if ver="$(docker version 2>&1)"; then
    log_info "containers: docker: reachable"
  else
    log_warn "containers: docker: NOT reachable (${ver})"
  fi

  if systemctl is-enabled docker >/dev/null 2>&1; then
    log_info "containers: docker.service: enabled"
  else
    log_warn "containers: docker.service: not enabled"
  fi

  local group_user
  group_user="$(state_get "module.containers.docker_group_user" 2>/dev/null || true)"
  if [[ -n "$group_user" ]]; then
    log_warn "containers: access policy: '${group_user}' is in the docker group (ROOT-EQUIVALENT)"
  else
    log_info "containers: access policy: sudo docker (no group membership granted by OMES)"
  fi

  return 0
}
