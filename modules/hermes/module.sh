#!/usr/bin/env bash
# shellcheck shell=bash
# modules/hermes/module.sh - Hermes Agent CLI, installed per-user.
#
# The MODULE_* metadata variables below are read by lib/omes/module.sh
# after sourcing this file; ShellCheck cannot see that cross-file usage
# when analyzing this file on its own.
#
# Scope: MODULE_SCOPE=user. Hermes installs per-user (~/.hermes,
# ~/.local/bin/hermes) and must never be installed as root - see
# docs/hermes-integration.md and docs/security.md §1.

# shellcheck disable=SC2034
MODULE_NAME="hermes"
# shellcheck disable=SC2034
MODULE_DESCRIPTION="Hermes Agent CLI (per-user install via the upstream installer)"
# shellcheck disable=SC2034
MODULE_SCOPE="user"
# shellcheck disable=SC2034
MODULE_REQUIRES=()
# shellcheck disable=SC2034
MODULE_PROFILES=(server desktop hermes)

# shellcheck source=../../lib/omes/versions.sh
source "${OMES_ROOT}/lib/omes/versions.sh"
# shellcheck source=../../lib/omes/cmd/audit-provenance.sh
source "${OMES_ROOT}/lib/omes/cmd/audit-provenance.sh"

# Default upstream installer location (verified 2026-09-18). Overridable via
# OMES_HERMES_INSTALLER_URL purely for testability; there is no documented
# operator reason to change it.
HERMES_DEFAULT_INSTALLER_URL="https://hermes-agent.nousresearch.com/install.sh"

HERMES_PATH_MARKER_BEGIN="# BEGIN OMES hermes PATH"
HERMES_PATH_MARKER_END="# END OMES hermes PATH"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# _hermes_home
# Prints the resolved HERMES_HOME: OMES_HERMES_HOME override, else
# ~/.hermes (upstream default). Profiles that want an isolated Hermes
# instance set OMES_HERMES_HOME before running `omes install`.
_hermes_home() {
  printf '%s\n' "${OMES_HERMES_HOME:-${HOME}/.hermes}"
}

# _hermes_installer_url
# Prints the installer URL (OMES_HERMES_INSTALLER_URL override, else the
# verified upstream default).
_hermes_installer_url() {
  printf '%s\n' "${OMES_HERMES_INSTALLER_URL:-$HERMES_DEFAULT_INSTALLER_URL}"
}

# _hermes_path_snippet_file
# Prints the path to the OMES-managed PATH snippet that adds
# ~/.local/bin (where the Hermes installer places its binary) to PATH for
# future shells.
_hermes_path_snippet_file() {
  printf '%s/omes/hermes-path.sh\n' "${XDG_DATA_HOME:-${HOME}/.local/share}"
}

# _hermes_ensure_runtime_path
# Makes ~/.local/bin visible on PATH for the REST OF THIS PROCESS (not just
# future shells), so a hermes binary installed earlier in this same
# module_apply/module_verify call is immediately runnable without requiring
# a new shell. Idempotent.
_hermes_ensure_runtime_path() {
  case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) ;;
    *)
      PATH="${HOME}/.local/bin:${PATH}"
      export PATH
      ;;
  esac
}

# _hermes_installed_version
# Prints `hermes --version` output when the hermes CLI is present and
# runnable; prints nothing (and returns non-zero) otherwise. Never treats a
# present-but-broken binary as "installed".
_hermes_installed_version() {
  _hermes_ensure_runtime_path
  command -v hermes >/dev/null 2>&1 || return 1
  hermes --version 2>/dev/null
}

# _hermes_download_and_install <home>
# Downloads the Hermes installer to a temp file (never `curl | bash`),
# optionally verifies OMES_HERMES_INSTALLER_SHA256, then runs it with an
# explicit HERMES_HOME. Honors dry-run (no download, no execution).
_hermes_download_and_install() {
  local home="$1"
  local url
  url="$(_hermes_installer_url)"

  if omes_dry_run; then
    log_info "[dry-run] would download the Hermes installer from ${url} and run it with HERMES_HOME=${home}"
    return 0
  fi

  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/omes-hermes-install.XXXXXX")"

  log_info "hermes: downloading installer from ${url}"
  if ! curl -fsSL "$url" -o "$tmp"; then
    log_error "hermes: failed to download the Hermes installer from ${url}"
    rm -f "$tmp"
    return 1
  fi

  local actual="" checksum_status="unverified"
  if [[ -n "${OMES_HERMES_INSTALLER_SHA256:-}" ]]; then
    actual="$(sha256sum "$tmp" | awk '{print $1}')"
    if [[ "$actual" != "${OMES_HERMES_INSTALLER_SHA256}" ]]; then
      log_error "hermes: installer sha256 mismatch (expected ${OMES_HERMES_INSTALLER_SHA256}, got ${actual}); aborting, nothing executed"
      rm -f "$tmp"
      return 1
    fi
    log_info "hermes: installer sha256 verified against OMES_HERMES_INSTALLER_SHA256"
    checksum_status="verified"
  else
    log_warn "hermes: OMES_HERMES_INSTALLER_SHA256 is not set; running the Hermes installer without verifying its integrity (see docs/security.md §6)"
  fi

  log_info "hermes: running installer with HERMES_HOME=${home}"
  local rc=0
  HERMES_HOME="$home" omes_run bash "$tmp" || rc=$?
  rm -f "$tmp"

  if [[ "$rc" -ne 0 ]]; then
    log_error "hermes: installer exited with status ${rc}"
    return 1
  fi

  _hermes_record_provenance "$url" "${OMES_HERMES_INSTALLER_SHA256:-}" "$actual" "$checksum_status"

  return 0
}

# _hermes_record_provenance <installer-url> <expected-sha256> <actual-sha256> <checksum-status>
# Records a supply-chain provenance entry for the "hermes" component
# (issue #84) via lib/omes/cmd/audit-provenance.sh's
# `provenance_record_component` helper. Best-effort: never fails
# module_apply (see that helper's own contract).
_hermes_record_provenance() {
  local url="$1" expected="$2" actual="$3" status="$4"
  local resolved_version
  resolved_version="$(_hermes_installed_version || true)"

  local checksum_obj payload
  checksum_obj="$(json_obj \
    "$(json_kv algorithm sha256)" \
    "$(json_kv expected "$expected")" \
    "$(json_kv actual "$actual")" \
    "$(json_kv status "$status")")"
  payload="$(json_obj \
    "$(json_kv installer_source_url "$url")" \
    "$(json_kv resolved_version "$resolved_version")" \
    "$(json_kv install_time "$(date -u +%Y-%m-%dT%H:%M:%SZ)")" \
    "$(json_kv checksum "$checksum_obj" --raw)")"

  provenance_record_component "hermes" "${OMES_PROFILE:-}" "$payload"
}

# _hermes_record_pkgmgr_provenance <kind> <package> <version> <origin>
# Records a package-manager provenance entry for a uv-tool- or
# pipx-managed package discovered on this host (issue #84's
# uv/pipx-managed-installs gap). <origin> is the package index URL when
# determinable (neither `uv tool list` nor `pipx list` currently expose
# one; this is a documented current limitation - see docs/provenance.md),
# else empty, in which case checksum.status is "unverified" rather than
# a guessed verified state. Component name is "<kind>:<package>" so
# `omes audit provenance` lists each discovered tool separately from
# "hermes" itself. Best-effort via provenance_record_component's own
# never-fail contract.
_hermes_record_pkgmgr_provenance() {
  local kind="$1" package="$2" version="$3" origin="$4"

  local pkgmgr_obj checksum_obj payload
  pkgmgr_obj="$(json_obj \
    "$(json_kv name "$kind")" \
    "$(json_kv package "$package")" \
    "$(json_kv version "$version")" \
    "$(json_kv origin "$origin")")"
  checksum_obj="$(json_obj \
    "$(json_kv algorithm "")" \
    "$(json_kv expected "")" \
    "$(json_kv actual "")" \
    "$(json_kv status unverified)")"
  payload="$(json_obj \
    "$(json_kv installer_source_url "$origin")" \
    "$(json_kv resolved_version "$version")" \
    "$(json_kv install_time "$(date -u +%Y-%m-%dT%H:%M:%SZ)")" \
    "$(json_kv checksum "$checksum_obj" --raw)" \
    "$(json_kv package_manager "$pkgmgr_obj" --raw)")"

  provenance_record_component "${kind}:${package}" "${OMES_PROFILE:-}" "$payload"
}

# _hermes_record_uv_provenance
# Parses `uv tool list` (one top-level "<name> <version>" line per
# installed tool, followed by indented entrypoint lines OMES ignores) and
# records a provenance entry per tool. No-op when uv is not on PATH.
# Read-only besides the provenance write itself.
_hermes_record_uv_provenance() {
  command -v uv >/dev/null 2>&1 || return 0

  local out
  out="$(uv tool list 2>/dev/null || true)"
  [[ -z "$out" ]] && return 0

  local line name version
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]] ]] && continue
    [[ -z "$line" ]] && continue
    name="${line%% *}"
    version="${line#* }"
    version="${version%% *}"
    [[ -z "$name" ]] && continue
    _hermes_record_pkgmgr_provenance "uv" "$name" "$version" ""
  done <<<"$out"
}

# _hermes_record_pipx_provenance
# Parses `pipx list --json` (stdlib python3, per ADR-0012 - no jq
# dependency) and records a provenance entry per installed venv's main
# package. No-op when pipx is not on PATH or python3 is unavailable.
# Read-only besides the provenance write itself.
_hermes_record_pipx_provenance() {
  command -v pipx >/dev/null 2>&1 || return 0
  command -v python3 >/dev/null 2>&1 || return 0

  local out
  out="$(pipx list --json 2>/dev/null || true)"
  [[ -z "$out" ]] && return 0

  local parsed
  parsed="$(OMES_PIPX_JSON="$out" python3 -c '
import json
import os

try:
    d = json.loads(os.environ["OMES_PIPX_JSON"])
except ValueError:
    raise SystemExit(0)

for name, venv in (d.get("venvs") or {}).items():
    pkg = ((venv.get("metadata") or {}).get("main_package") or {})
    package = pkg.get("package") or name
    version = pkg.get("package_version") or ""
    print(f"{package}\t{version}")
' 2>/dev/null || true)"
  [[ -z "$parsed" ]] && return 0

  local package version
  while IFS=$'\t' read -r package version; do
    [[ -z "$package" ]] && continue
    _hermes_record_pkgmgr_provenance "pipx" "$package" "$version" ""
  done <<<"$parsed"
}

# _hermes_record_package_manager_provenance
# Records package-manager provenance (issue #84) for any uv-tool- or
# pipx-managed package already present on this host, e.g. graphify once
# #50 lands (docs/graphify.md - uv tool/pipx is graphify's own installer,
# never system pip). Called from module_apply, after the Hermes
# install/evidence steps, so this snapshot always reflects what is
# currently discoverable rather than only what THIS apply call installed.
# Honors dry-run (module_apply's own dry-run branches already return
# before install steps run; this is a read-only discovery either way).
_hermes_record_package_manager_provenance() {
  _hermes_record_uv_provenance
  _hermes_record_pipx_provenance
}

# _hermes_ensure_path_snippet
# Creates/refreshes the OMES-managed PATH snippet and sources it from
# ~/.bashrc and ~/.profile if not already wired in (idempotent: a rc file
# already containing the marker block is left untouched). Both rc files are
# registered via omes_manage_path before being modified, so they are backed
# up and restorable.
_hermes_ensure_path_snippet() {
  local snippet
  snippet="$(_hermes_path_snippet_file)"

  if omes_dry_run; then
    log_info "[dry-run] would ensure PATH snippet at ${snippet} and source it from ~/.bashrc and ~/.profile"
    return 0
  fi

  omes_manage_path "$snippet"
  mkdir -p "$(dirname "$snippet")"
  cat >"$snippet" <<'SNIPPET'
# Managed by OMES (modules/hermes) - do not edit by hand.
# Ensures ~/.local/bin (where the Hermes installer places its binary) is on PATH.
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
SNIPPET
  chmod 644 "$snippet"

  local rc
  for rc in "${HOME}/.bashrc" "${HOME}/.profile"; do
    if [[ -f "$rc" ]] && grep -qF "$HERMES_PATH_MARKER_BEGIN" "$rc" 2>/dev/null; then
      log_info "hermes: PATH snippet already wired into ${rc}"
      continue
    fi
    omes_manage_path "$rc"
    {
      printf '\n%s\n' "$HERMES_PATH_MARKER_BEGIN"
      printf '. "%s"\n' "$snippet"
      printf '%s\n' "$HERMES_PATH_MARKER_END"
    } >>"$rc"
    log_info "hermes: wired PATH snippet into ${rc}"
  done
}

# _hermes_remove_marker_block <rc-file>
# Best-effort removal of the OMES-managed marker block from <rc-file>
# (used by module_rollback). No-op if the file or the marker is absent.
_hermes_remove_marker_block() {
  local rc="$1"
  [[ -f "$rc" ]] || return 0
  grep -qF "$HERMES_PATH_MARKER_BEGIN" "$rc" 2>/dev/null || return 0

  local tmp
  tmp="$(mktemp "${rc}.omes-rollback.XXXXXX")"
  awk -v b="$HERMES_PATH_MARKER_BEGIN" -v e="$HERMES_PATH_MARKER_END" '
    $0==b {skip=1; next}
    $0==e {skip=0; next}
    skip {next}
    {print}
  ' "$rc" >"$tmp"
  mv -f "$tmp" "$rc"
}

# _hermes_ensure_env_file <home>
# Creates $home/.env (mode 0600) if absent. Never overwrites an existing
# .env (that would destroy operator-managed secrets).
#
# Deliberately NOT registered via omes_manage_path: .env holds operator
# secrets (Telegram bot token, provider API keys - see docs/security.md
# §5 and docs/hermes-integration.md §5). omes_manage_path causes
# lib/omes/backup.sh to copy the registered path into
# <state-dir>/backups/ on every apply, and to restore/delete it on
# module_rollback - all of which would put a live secret into a backup
# directory and make rollback able to destroy an operator's credentials.
# OMES may create this file and fix its mode; it never backs it up,
# restores it, or deletes it.
_hermes_ensure_env_file() {
  local home="$1"
  local env_file="${home}/.env"

  if omes_dry_run; then
    log_info "[dry-run] would ensure ${env_file} exists with mode 0600 (never backed up)"
    return 0
  fi

  if [[ ! -e "$env_file" ]]; then
    mkdir -p "$home"
    : >"$env_file"
    log_info "hermes: created ${env_file} (not a managed/backed-up path - see docs/hermes-integration.md §5)"
  fi
  chmod 600 "$env_file"
}

# _hermes_record_evidence
# Snapshots lib/omes/versions.sh's compatibility evidence report
# (issue #83) into state keys `evidence.<component>.version` /
# `evidence.<component>.observed_at`, for the small set of components
# that have a single scalar value (composite objects like
# `provider_config` and nested `os`/`omes` are intentionally skipped
# here - they stay available via `omes health versions --json`, not
# duplicated into flat state keys). Best-effort: a collection failure
# here never fails module_apply or module_doctor.
_hermes_record_evidence() {
  local json
  json="$(versions_collect_json 2>/dev/null)" || return 0
  [[ -n "$json" ]] || return 0

  local pairs
  pairs="$(OMES_HERMES_EVIDENCE_JSON="$json" python3 -c '
import json
import os

d = json.loads(os.environ["OMES_HERMES_EVIDENCE_JSON"])
components = d.get("components", {})
for name in ("hermes", "gateway_mode", "python3", "node", "browser", "ffmpeg", "docker", "ollama"):
    fact = components.get(name)
    if not isinstance(fact, dict) or "value" not in fact:
        continue
    value = fact.get("value") or ""
    observed_at = fact.get("observed_at") or ""
    print(f"{name}\t{value}\t{observed_at}")
' 2>/dev/null || true)"
  [[ -n "$pairs" ]] || return 0

  local line name value observed_at
  while IFS=$'\t' read -r name value observed_at; do
    [[ -n "$name" ]] || continue
    state_set "evidence.${name}.version" "$value"
    state_set "evidence.${name}.observed_at" "$observed_at"
  done <<<"$pairs"
}

# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------

module_check() {
  if omes_is_root; then
    log_error "hermes: must not run as root - Hermes installs per-user; re-run as the target user without sudo (see docs/hermes-integration.md)"
    return 1
  fi

  if ! command -v curl >/dev/null 2>&1; then
    log_error "hermes: curl not found (required to download the Hermes installer)"
    return 1
  fi

  if [[ ! -w "$HOME" ]]; then
    log_error "hermes: \$HOME (${HOME}) is not writable"
    return 1
  fi

  local installed_version
  if installed_version="$(_hermes_installed_version)"; then
    log_info "hermes: already installed (${installed_version})"
    return 0
  fi

  log_info "hermes: not yet installed"
  if ! detect_network; then
    log_error "hermes: network required to download the Hermes installer"
    return 1
  fi
  return 0
}

module_apply() {
  local home
  home="$(_hermes_home)"
  export HERMES_HOME="$home"

  local current_version=""
  current_version="$(_hermes_installed_version || true)"

  local need_install=1
  if [[ -n "$current_version" ]]; then
    if [[ -z "${OMES_HERMES_VERSION:-}" ]] || [[ "$current_version" == *"${OMES_HERMES_VERSION}"* ]]; then
      need_install=0
    fi
  fi

  if [[ "$need_install" -eq 1 ]]; then
    if ! _hermes_download_and_install "$home"; then
      return 1
    fi
  else
    log_info "hermes: already installed (${current_version}); skipping installer download"
  fi

  _hermes_ensure_path_snippet
  _hermes_ensure_env_file "$home"

  if ! omes_dry_run; then
    state_set "module.hermes.home" "$home"
    _hermes_record_evidence
    _hermes_record_package_manager_provenance
  fi

  return 0
}

module_verify() {
  if omes_dry_run; then
    log_info "[dry-run] would verify: hermes --version and hermes doctor"
    return 0
  fi

  _hermes_ensure_runtime_path

  local version_out
  if ! version_out="$(hermes --version 2>&1)"; then
    log_error "hermes: 'hermes --version' failed: ${version_out}"
    return 1
  fi
  log_info "hermes: version: ${version_out}"

  local doctor_out
  if ! doctor_out="$(hermes doctor 2>&1)"; then
    log_error "hermes: 'hermes doctor' reported a failure:"
    log_error "${doctor_out}"
    return 1
  fi

  if grep -qi 'warn' <<<"$doctor_out"; then
    log_warn "hermes: 'hermes doctor' reported warnings:"
    log_warn "${doctor_out}"
  else
    log_info "hermes: 'hermes doctor' passed with no warnings"
  fi

  return 0
}

module_rollback() {
  local home
  home="$(_hermes_home)"
  local snippet
  snippet="$(_hermes_path_snippet_file)"

  log_warn "hermes: rollback removes only OMES-managed PATH wiring; it never deletes ${home} or any Hermes data"

  if omes_dry_run; then
    log_info "[dry-run] would remove ${snippet} and the OMES marker block from ~/.bashrc and ~/.profile"
    return 0
  fi

  if [[ -f "$snippet" ]]; then
    rm -f "$snippet"
    log_info "hermes: removed ${snippet}"
  fi

  local rc
  for rc in "${HOME}/.bashrc" "${HOME}/.profile"; do
    _hermes_remove_marker_block "$rc"
  done

  state_unset "module.hermes.home"

  log_warn "hermes: to fully remove Hermes yourself, run: rm -rf '${home}' \"\$HOME/.local/bin/hermes\""
  return 0
}

# _hermes_ollama_configured
# True when Ollama looks like it is meant to be checked for this install:
# either explicitly opted in (OMES_OLLAMA_ENABLED=1), or the `ollama`
# binary is present AND a model has been configured
# (OMES_OLLAMA_MODEL) - matching the detection rule in issue #71/#79.
_hermes_ollama_configured() {
  if [[ "${OMES_OLLAMA_ENABLED:-0}" == "1" ]]; then
    return 0
  fi
  command -v ollama >/dev/null 2>&1 && [[ -n "${OMES_OLLAMA_MODEL:-}" ]]
}

# module_doctor
# Additive, optional `omes doctor` hook (see bin/omes's cmd_doctor):
# when Ollama looks configured for this install, runs the layered Ollama
# health check (lib/omes/py/health/ollama.py, issue #71) and reports a
# one-line summary. Advisory only - a non-zero return here surfaces as a
# WARN in `omes doctor`, it never fails `module_verify` itself, and it is
# a complete no-op (prints nothing, returns 0) when Ollama is not
# configured for this install at all.
_hermes_doctor_print_ollama() {
  if ! _hermes_ollama_configured; then
    printf 'ollama: not configured for this install (set OMES_OLLAMA_ENABLED=1, or install the ollama binary and set OMES_OLLAMA_MODEL)\n'
    return 0
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    printf 'ollama: python3 not found; cannot run the layered Ollama health check (see docs/ollama.md)\n'
    return 1
  fi

  local script="${OMES_ROOT}/lib/omes/py/health/ollama.py"
  if [[ ! -r "$script" ]]; then
    printf 'ollama: health checker not found at %s\n' "$script"
    return 1
  fi

  local out rc=0
  out="$(python3 "$script" 2>/dev/null)" || rc=$?
  if [[ -z "$out" ]]; then
    printf 'ollama: health checker produced no output (exit %s)\n' "$rc"
    return 1
  fi

  local summary
  summary="$(printf '%s' "$out" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    print("health checker returned invalid JSON")
    sys.exit(0)
print("ready=%s service=%s model=%s" % (d.get("ready"), d.get("service", {}).get("status"), d.get("model", {}).get("status")))
' 2>/dev/null || printf 'health checker output could not be summarized')"

  printf 'ollama: %s\n' "$summary"
  [[ "$rc" -eq 0 ]]
}

# module_doctor
# Runs every additive doctor section (Ollama, then versions/compatibility
# evidence) and combines their results: a WARN from any section makes
# `omes doctor` report this module as WARN, but never fails
# module_verify itself.
module_doctor() {
  local overall=0

  _hermes_doctor_print_ollama || overall=1
  _hermes_doctor_print_versions || overall=1

  return "$overall"
}

# =============================================================================
# BEGIN module_doctor versions section (issue #83)
# =============================================================================
#
# Appended to module_doctor's output: refreshes the evidence.* state
# snapshot and prints a one-line compatibility-evidence summary plus any
# known-unsupported-combination warnings. Advisory only, like the Ollama
# section above - never fails module_verify.
_hermes_doctor_print_versions() {
  _hermes_record_evidence

  local json rc=0
  json="$(versions_collect_json 2>/dev/null)" || rc=$?
  if [[ -z "$json" ]]; then
    printf 'versions: evidence collector produced no output (see docs/compatibility-evidence.md)\n'
    return 1
  fi

  OMES_HERMES_EVIDENCE_JSON="$json" python3 -c '
import json
import os

d = json.loads(os.environ["OMES_HERMES_EVIDENCE_JSON"])
c = d.get("components", {})
hermes_v = (c.get("hermes") or {}).get("value")
gw = (c.get("gateway_mode") or {}).get("value")
print("versions: hermes=%s gateway_mode=%s python3=%s node=%s ffmpeg=%s docker=%s" % (
    hermes_v, gw,
    (c.get("python3") or {}).get("value"),
    (c.get("node") or {}).get("value"),
    (c.get("ffmpeg") or {}).get("value"),
    (c.get("docker") or {}).get("value"),
))
for w in d.get("warnings", []):
    print("versions: WARN %s" % w)
' 2>/dev/null || printf 'versions: evidence report could not be summarized\n'

  return "$rc"
}
# =============================================================================
# END module_doctor versions section (issue #83)
# =============================================================================
