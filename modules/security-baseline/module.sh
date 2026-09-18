#!/usr/bin/env bash
# shellcheck shell=bash
# modules/security-baseline/module.sh - firewall, update policy, log
# hygiene, and NTP check for the Ubuntu Server headless profile.
#
# The MODULE_* metadata variables below are read by lib/omes/module.sh
# after sourcing this file; ShellCheck cannot see that cross-file usage
# when analyzing this file on its own.
#
# Scope: MODULE_SCOPE=root, MODULE_REQUIRES=(apt-base) - ufw is already
# installed by apt-base's package list, and pkg_map/pkg_* helpers require
# apt-base's preconditions (apt-get/dpkg-query present). See
# docs/security.md Section 3 (firewall/SSH) and Section 4 (update policy),
# and docs/threat-model.md T24-T26 (this module's acceptance criteria).
#
# Test isolation: every /etc-rooted path this module writes or reads is
# resolved through _security_etc_dir(), which honors OMES_ETC_DIR (tests
# only; production always uses the real /etc) - the same pattern
# lib/omes/pkg.sh uses for OMES_APT_SOURCES_DIR and modules/hermes-gateway-
# system uses for OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR, so tests never
# touch the real filesystem's /etc.

# shellcheck disable=SC2034
MODULE_NAME="security-baseline"
# shellcheck disable=SC2034
MODULE_DESCRIPTION="Firewall (ufw), unattended security upgrades, journald hygiene, NTP check"
# shellcheck disable=SC2034
MODULE_SCOPE="root"
# shellcheck disable=SC2034
MODULE_REQUIRES=(apt-base)
# shellcheck disable=SC2034
MODULE_PROFILES=(server)

# ufw itself is already installed by apt-base's package list (MODULE_
# REQUIRES ensures apt-base has already applied); it is listed again here
# defensively so this module remains self-contained if apt-base's list
# ever changes, and so module_verify has a single source of truth for
# "packages this module requires".
SECURITY_BASELINE_PACKAGES=(ufw unattended-upgrades)

# ---------------------------------------------------------------------------
# Path helpers (OMES_ETC_DIR override for tests)
# ---------------------------------------------------------------------------

# _security_etc_dir
# Prints the /etc root this module reads/writes under. Honors OMES_ETC_DIR
# (tests only; production always uses the real /etc).
_security_etc_dir() {
  printf '%s\n' "${OMES_ETC_DIR:-/etc}"
}

# _security_apt_conf_path
# Prints the path to the OMES-owned unattended-upgrades config fragment.
# Deliberately a SEPARATE file from the distro's own
# 50unattended-upgrades and 20auto-upgrades (never edited by OMES - see
# module_apply's comment on why "52" is enough on its own).
_security_apt_conf_path() {
  printf '%s/apt/apt.conf.d/52omes-unattended-upgrades\n' "$(_security_etc_dir)"
}

# _security_journald_conf_path
# Prints the path to the OMES-owned journald drop-in.
_security_journald_conf_path() {
  printf '%s/systemd/journald.conf.d/omes.conf\n' "$(_security_etc_dir)"
}

# _security_sshd_config_path
_security_sshd_config_path() {
  printf '%s/ssh/sshd_config\n' "$(_security_etc_dir)"
}

# ---------------------------------------------------------------------------
# Content builders (pure functions of env/config; used both to write and,
# for idempotency, to compare against what is already on disk)
# ---------------------------------------------------------------------------

# _security_unattended_conf_content
# Builds 52omes-unattended-upgrades. `${distro_id}`/`${distro_codename}`
# are literal apt-config variables (apt resolves them itself, the same way
# the distro's own 50unattended-upgrades does) - NOT bash variables, hence
# the single-quoted heredoc.
_security_unattended_conf_content() {
  cat <<'EOF'
// Managed by OMES (modules/security-baseline) - do not edit by hand.
//
// This file is intentionally separate from the distro-owned
// /etc/apt/apt.conf.d/50unattended-upgrades and
// /etc/apt/apt.conf.d/20auto-upgrades, which OMES never edits (see
// docs/ubuntu-server.md). apt.conf.d files are cumulative and later
// files' settings win, so enabling the periodic timers here (instead of
// in 20auto-upgrades) achieves the same effect without touching a
// distro-owned file.
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";

// Security pocket only - see docs/security.md Section 4 ("OMES does not
// enable unattended upgrades for the full archive by default").
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
};

EOF
  if [[ "${OMES_UNATTENDED_AUTO_REBOOT:-0}" == "1" ]]; then
    printf 'Unattended-Upgrade::Automatic-Reboot "true";\n'
    printf 'Unattended-Upgrade::Automatic-Reboot-Time "%s";\n' "${OMES_UNATTENDED_REBOOT_TIME:-02:00}"
  else
    printf 'Unattended-Upgrade::Automatic-Reboot "false";\n'
  fi
}

# _security_journald_conf_content
_security_journald_conf_content() {
  printf '# Managed by OMES (modules/security-baseline) - do not edit by hand.\n'
  printf '[Journal]\n'
  printf 'SystemMaxUse=%s\n' "${OMES_JOURNALD_MAX_USE:-500M}"
  printf 'Compress=yes\n'
}

# ---------------------------------------------------------------------------
# SSH lockout guard (docs/security.md Section 3, docs/threat-model.md T26)
# ---------------------------------------------------------------------------

# _security_sshd_port
# Prints the sshd port: `sshd -T`'s reported port when sshd is reachable,
# else parsed from sshd_config's `Port` directive, else 22.
_security_sshd_port() {
  local port=""
  if command -v sshd >/dev/null 2>&1; then
    port="$(sshd -T 2>/dev/null | awk 'tolower($1)=="port"{print $2; exit}')"
  fi
  if [[ -z "$port" ]]; then
    local cfg
    cfg="$(_security_sshd_config_path)"
    if [[ -r "$cfg" ]]; then
      port="$(awk 'tolower($1)=="port"{print $2; exit}' "$cfg")"
    fi
  fi
  printf '%s\n' "${port:-22}"
}

# _security_ssh_reason
# Prints a short human-readable reason and returns 0 when SSH access must
# be kept/made open before ufw is enabled: OMES_ENABLE_SSH=1 (this
# module's stand-in for the brief's future `--enable-ssh` CLI flag - see
# module header/docs/ubuntu-server.md), an active SSH session
# ($SSH_CONNECTION/$SSH_TTY), `who am i` reporting a remote host, or the
# ssh/sshd service being active or enabled. Returns 1 with no output when
# none apply. Read-only; safe from module_check.
_security_ssh_reason() {
  if [[ "${OMES_ENABLE_SSH:-0}" == "1" ]]; then
    printf 'OMES_ENABLE_SSH=1 was requested\n'
    return 0
  fi

  if [[ -n "${SSH_CONNECTION:-}" ]] || [[ -n "${SSH_TTY:-}" ]]; then
    printf 'the current session is over SSH (SSH_CONNECTION/SSH_TTY set)\n'
    return 0
  fi

  if command -v who >/dev/null 2>&1; then
    local who_out
    who_out="$(who am i 2>/dev/null || true)"
    if [[ "$who_out" =~ \(([^)]+)\) ]]; then
      local host="${BASH_REMATCH[1]}"
      if [[ -n "$host" ]] && [[ "$host" != "-" ]] && [[ "$host" != :* ]] && [[ "$host" != "localhost" ]]; then
        printf "'who am i' reports a remote host (%s)\n" "$host"
        return 0
      fi
    fi
  fi

  if command -v systemctl >/dev/null 2>&1; then
    local svc
    for svc in ssh sshd; do
      if systemctl is-active "$svc" >/dev/null 2>&1; then
        printf 'the %s service is active\n' "$svc"
        return 0
      fi
      if systemctl is-enabled "$svc" >/dev/null 2>&1; then
        printf 'the %s service is enabled\n' "$svc"
        return 0
      fi
    done
  fi

  return 1
}

# _security_ssh_rule_present <status-verbose-output> <ssh-port>
# True when <status-verbose-output> (the text of `ufw status verbose`)
# already contains an ALLOW rule for OpenSSH or the given port.
_security_ssh_rule_present() {
  local status_out="$1" port="$2"
  printf '%s\n' "$status_out" | grep -qE "(OpenSSH|${port}/tcp).*ALLOW"
}

# ---------------------------------------------------------------------------
# NTP check (informational only - never installs chrony unless opted in)
# ---------------------------------------------------------------------------

# _security_ntp_synced
# True when timedatectl reports the system clock as NTP-synchronized.
_security_ntp_synced() {
  command -v timedatectl >/dev/null 2>&1 || return 1
  local out
  out="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
  [[ "$out" == "yes" ]]
}

# ---------------------------------------------------------------------------
# Idempotent file writer
# ---------------------------------------------------------------------------

# _security_write_if_changed <path> <content>
# Writes <content> to <path> (mode 0644, managed via omes_manage_path)
# only when it differs from what is already there. Returns 0 when it
# changed (or, in dry-run, would change), 1 when already up to date.
# Honors dry-run: compares first, only skips the actual write.
_security_write_if_changed() {
  local path="$1" content="$2"
  local current=""
  [[ -f "$path" ]] && current="$(cat "$path" 2>/dev/null || true)"

  if [[ "$current" == "$content" ]]; then
    log_info "security-baseline: ${path} already up to date"
    return 1
  fi

  if omes_dry_run; then
    log_info "[dry-run] would write ${path}"
    return 0
  fi

  mkdir -p "$(dirname "$path")"
  omes_manage_path "$path"
  printf '%s\n' "$content" > "$path"
  chmod 644 "$path"
  log_info "security-baseline: wrote ${path}"
  return 0
}

# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------

module_check() {
  if ! command -v ufw >/dev/null 2>&1; then
    log_error "security-baseline: ufw not found (expected from apt-base; MODULE_REQUIRES=(apt-base))"
    return 1
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    log_error "security-baseline: systemctl not found"
    return 1
  fi

  local -a pkgs=("${SECURITY_BASELINE_PACKAGES[@]}")
  if [[ "${OMES_INSTALL_CHRONY:-0}" == "1" ]]; then
    pkgs+=(chrony)
  fi

  local -a missing=()
  local pkg
  while IFS= read -r pkg; do
    [[ -n "$pkg" ]] && missing+=("$pkg")
  done < <(pkg_missing "${pkgs[@]}")

  if [[ "${#missing[@]}" -gt 0 ]]; then
    local rc bad=0
    for pkg in "${missing[@]}"; do
      pkg_exists_in_repos "$pkg"
      rc=$?
      [[ "$rc" -ne 0 ]] && bad=1
    done
    if [[ "$bad" -eq 1 ]]; then
      return 1
    fi
    log_info "security-baseline: missing packages: ${missing[*]}"
  else
    log_info "security-baseline: all packages already installed (offline ok)"
  fi

  local status_out
  status_out="$(ufw status verbose 2>/dev/null || true)"
  if [[ "$status_out" == *"Status: active"* ]]; then
    log_info "security-baseline: ufw is already active"
  else
    log_info "security-baseline: ufw would be enabled"
  fi
  if [[ "$status_out" != *"Default: deny (incoming)"* ]]; then
    log_info "security-baseline: default incoming policy would change to deny"
  fi
  if [[ "$status_out" != *"allow (outgoing)"* ]]; then
    log_info "security-baseline: default outgoing policy would change to allow"
  fi

  local ssh_reason ssh_port
  if ssh_reason="$(_security_ssh_reason)"; then
    ssh_port="$(_security_sshd_port)"
    if _security_ssh_rule_present "$status_out" "$ssh_port"; then
      log_info "security-baseline: SSH (port ${ssh_port}) is already allowed (${ssh_reason})"
    else
      log_info "security-baseline: SSH (port ${ssh_port}) would be allowed before ufw is enabled (${ssh_reason})"
    fi
  fi

  if ! _security_ntp_synced; then
    log_warn "security-baseline: timedatectl reports the clock is not NTP-synchronized"
  fi

  local apt_conf journald_conf
  apt_conf="$(_security_apt_conf_path)"
  journald_conf="$(_security_journald_conf_path)"
  if [[ -f "$apt_conf" ]] && [[ "$(cat "$apt_conf" 2>/dev/null || true)" == "$(_security_unattended_conf_content)" ]]; then
    log_info "security-baseline: ${apt_conf} already up to date"
  else
    log_info "security-baseline: would write ${apt_conf}"
  fi
  if [[ -f "$journald_conf" ]] && [[ "$(cat "$journald_conf" 2>/dev/null || true)" == "$(_security_journald_conf_content)" ]]; then
    log_info "security-baseline: ${journald_conf} already up to date"
  else
    log_info "security-baseline: would write ${journald_conf}"
  fi

  return 0
}

module_apply() {
  local -a pkgs=("${SECURITY_BASELINE_PACKAGES[@]}")
  if [[ "${OMES_INSTALL_CHRONY:-0}" == "1" ]]; then
    pkgs+=(chrony)
  fi

  local -a missing=()
  local pkg
  while IFS= read -r pkg; do
    [[ -n "$pkg" ]] && missing+=("$pkg")
  done < <(pkg_missing "${pkgs[@]}")

  if [[ "${#missing[@]}" -gt 0 ]]; then
    pkg_apt_update || return $?
    pkg_install "${missing[@]}" || return $?
  else
    log_info "security-baseline: nothing to install, all packages already present"
  fi

  # --- Firewall ------------------------------------------------------------
  #
  # Order is deliberate and load-bearing (docs/security.md Section 3,
  # docs/threat-model.md T26): the SSH allow rule is added FIRST (whether
  # ufw is active or not yet), default policies are set next, and
  # `ufw --force enable` runs LAST - so an already-active re-run that for
  # any reason needs to tighten its default policy never does so before
  # SSH is confirmed allowed, and a first run never enables the firewall
  # before the SSH rule exists.
  local status_out
  status_out="$(ufw status verbose 2>/dev/null || true)"
  local was_active=0
  [[ "$status_out" == *"Status: active"* ]] && was_active=1

  local ssh_reason ssh_port
  if ssh_reason="$(_security_ssh_reason)"; then
    ssh_port="$(_security_sshd_port)"
    if ! _security_ssh_rule_present "$status_out" "$ssh_port"; then
      if [[ "$ssh_port" == "22" ]]; then
        omes_run ufw allow OpenSSH
      else
        omes_run ufw allow "${ssh_port}/tcp"
      fi
    fi
    # Logged every apply (not just when the rule is newly added) so an
    # operator always sees why SSH stayed open - see docs/security.md
    # Section 3 ("OMES prints a warning naming the detected session").
    log_warn "security-baseline: SSH access kept open on port ${ssh_port}/tcp (${ssh_reason})"
  fi

  if [[ "$status_out" != *"Default: deny (incoming)"* ]]; then
    omes_run ufw default deny incoming
  fi
  if [[ "$status_out" != *"allow (outgoing)"* ]]; then
    omes_run ufw default allow outgoing
  fi

  if [[ "$was_active" -eq 0 ]]; then
    omes_run ufw --force enable
    if ! omes_dry_run; then
      state_set "module.security-baseline.ufw_enabled_by_omes" "1"
    fi
  fi

  # --- Update policy ---------------------------------------------------------
  local apt_conf journald_conf
  apt_conf="$(_security_apt_conf_path)"
  journald_conf="$(_security_journald_conf_path)"

  _security_write_if_changed "$apt_conf" "$(_security_unattended_conf_content)" || true

  local journald_changed=1
  if ! _security_write_if_changed "$journald_conf" "$(_security_journald_conf_content)"; then
    journald_changed=0
  fi
  if [[ "$journald_changed" -eq 1 ]]; then
    omes_run systemctl restart systemd-journald
  fi

  # --- NTP (informational only; never installs chrony unless opted in) -----
  if ! _security_ntp_synced; then
    log_warn "security-baseline: timedatectl reports the clock is not NTP-synchronized (pass OMES_INSTALL_CHRONY=1 to install chrony, or check network time settings)"
  fi

  return 0
}

module_verify() {
  if omes_dry_run; then
    log_info "[dry-run] would verify: ufw active + default deny incoming (+ SSH allowed if required), unattended-upgrades installed, config files present"
    return 0
  fi

  local status_out
  status_out="$(ufw status verbose 2>/dev/null || true)"
  if [[ "$status_out" != *"Status: active"* ]]; then
    log_error "security-baseline: ufw is not active"
    return 1
  fi
  if [[ "$status_out" != *"Default: deny (incoming)"* ]]; then
    log_error "security-baseline: ufw default incoming policy is not 'deny'"
    return 1
  fi

  local ssh_reason ssh_port
  if ssh_reason="$(_security_ssh_reason)"; then
    ssh_port="$(_security_sshd_port)"
    if ! _security_ssh_rule_present "$status_out" "$ssh_port"; then
      log_error "security-baseline: SSH should be allowed (${ssh_reason}) but no matching ufw ALLOW rule was found for port ${ssh_port}"
      return 1
    fi
  fi

  if ! pkg_is_installed unattended-upgrades; then
    log_error "security-baseline: unattended-upgrades is not installed"
    return 1
  fi

  local apt_conf journald_conf
  apt_conf="$(_security_apt_conf_path)"
  journald_conf="$(_security_journald_conf_path)"
  if [[ ! -f "$apt_conf" ]]; then
    log_error "security-baseline: missing ${apt_conf}"
    return 1
  fi
  if [[ ! -f "$journald_conf" ]]; then
    log_error "security-baseline: missing ${journald_conf}"
    return 1
  fi

  log_info "security-baseline: verified ufw active (default deny incoming), SSH policy, unattended-upgrades installed, config files present"
  return 0
}

module_rollback() {
  log_warn "security-baseline: rollback leaves ufw/unattended-upgrades packages installed; remove manually with: apt-get remove ${SECURITY_BASELINE_PACKAGES[*]}"

  local apt_conf journald_conf
  apt_conf="$(_security_apt_conf_path)"
  journald_conf="$(_security_journald_conf_path)"

  if omes_dry_run; then
    log_info "[dry-run] would remove ${apt_conf} and ${journald_conf}, and disable ufw only if OMES itself enabled it"
    return 0
  fi

  if [[ -f "$apt_conf" ]]; then
    rm -f "$apt_conf"
    log_info "security-baseline: removed ${apt_conf}"
  fi
  if [[ -f "$journald_conf" ]]; then
    rm -f "$journald_conf"
    log_info "security-baseline: removed ${journald_conf}"
    omes_run systemctl restart systemd-journald
  fi

  local enabled_by_omes
  enabled_by_omes="$(state_get "module.security-baseline.ufw_enabled_by_omes" 2>/dev/null || echo 0)"
  if [[ "$enabled_by_omes" == "1" ]]; then
    omes_run ufw disable
    state_unset "module.security-baseline.ufw_enabled_by_omes"
    log_warn "security-baseline: disabled ufw (OMES had enabled it; the SSH allow rule, if any, is left in place - never removed by rollback)"
  else
    log_info "security-baseline: leaving ufw as-is (it was already active before OMES ran, or OMES never enabled it)"
  fi

  return 0
}

# module_doctor
# Optional diagnostics (not part of the required module contract; not yet
# wired into `omes doctor`, which is a stub tracked in #14 - bin/omes is
# outside this module's file scope). Read-only. Reports firewall state,
# a simulated count of pending security updates, and NTP sync state.
module_doctor() {
  local fw_line
  fw_line="$(ufw status verbose 2>/dev/null | head -n1 || echo unknown)"
  log_info "security-baseline: firewall: ${fw_line}"

  local pending
  pending="$(apt-get -s upgrade 2>/dev/null | grep -c '^Inst ' || true)"
  log_info "security-baseline: pending security updates (simulated via apt-get -s upgrade): ${pending:-0}"

  if _security_ntp_synced; then
    log_info "security-baseline: NTP synchronized: yes"
  else
    log_warn "security-baseline: NTP synchronized: no"
  fi

  if command -v journalctl >/dev/null 2>&1; then
    local recent
    recent="$(journalctl -u apt-daily-upgrade.service -n 5 --no-pager 2>/dev/null || true)"
    if [[ -n "$recent" ]]; then
      log_info "security-baseline: recent apt-daily-upgrade.service log:"
      log_info "${recent}"
    fi
  fi

  return 0
}
