#!/usr/bin/env bash
# shellcheck shell=bash
# modules/graphify-mcp/module.sh - Graphify's MCP stdio server, installed
# via the optional `graphifyy[mcp]` PyPI extra.
#
# Verified empirically (2026-09-19, graphifyy 0.9.64, `pip install
# "graphifyy[mcp]"` inside python:3.12-slim): the extra adds a real,
# invocable console script, `graphify-mcp` (entry point
# `graphify.serve:_main`), documented by `graphify-mcp --help` as:
#
#   usage: python -m graphify.serve [-h] [--graph PATH]
#                                   [--transport {stdio,http}] [--host HOST]
#                                   [--port PORT] [--api-key API_KEY]
#                                   [--path PATH] [--json-response]
#                                   [--stateless]
#                                   [--session-timeout SESSION_TIMEOUT]
#                                   [graph_path]
#
# Command/working-directory/graph-path/lifecycle (issue #52 acceptance
# criterion 1): `graphify-mcp [graph_path]` (default
# `graphify-out/graph.json`, resolved relative to the process's current
# working directory - there is no separate "install directory" concept),
# default transport `stdio`. This is a local process boundary an MCP
# client (Hermes) spawns per-session and talks to over stdin/stdout - NOT
# a persistent daemon OMES starts, enables, or manages via systemd. OMES's
# job stops at making the `graphify-mcp` command available and verifiable
# (this module) plus a read-only health check (lib/omes/cmd/graphify.sh's
# `omes graphify mcp health`, issue #52 criterion 2) - it never spawns,
# proxies, or supervises a running MCP session itself, and it never
# enables `--transport http` (docs/graphify.md §4: local process boundary
# only, remote graph servers are out of MVP scope per the issue brief).
#
# Disabled by default (issue #52 criterion 3): like modules/graphify/
# module.sh, MODULE_PROFILES is empty - reachable only via
# `omes install --module graphify-mcp`, never auto-applied by any
# profile.
#
# Failure isolation (issue #52 criterion 4): this file never references
# hermes-gateway, systemctl, or any Hermes runtime state. A broken or
# absent graphify-mcp can only ever affect an MCP client's ability to
# reach Graphify's tools over MCP - normal Hermes operation (messaging,
# other skills, the hermes-gateway service) is structurally unaffected,
# since nothing here touches it.

# shellcheck disable=SC2034
MODULE_NAME="graphify-mcp"
# shellcheck disable=SC2034
MODULE_DESCRIPTION="Graphify MCP stdio server (graphifyy[mcp] extra) - opt-in, local process boundary"
# shellcheck disable=SC2034
MODULE_SCOPE="user"
# shellcheck disable=SC2034
MODULE_REQUIRES=(graphify)
# Deliberately not wired into any profile's default module list - MCP is
# opt-in until an operator explicitly enables it (docs/graphify.md §4).
# shellcheck disable=SC2034
MODULE_PROFILES=()

GRAPHIFY_MCP_EXTRA_SPEC="graphifyy[mcp]"
GRAPHIFY_PLAIN_PACKAGE="graphifyy"

# ---------------------------------------------------------------------------
# Internal helpers
#
# Duplicated (in miniature) from modules/graphify/module.sh rather than
# sourced from it: module_load() (lib/omes/module.sh) unsets the previous
# module's functions/metadata before sourcing the next one, so by the time
# this module's own module_check/apply/verify/rollback run (after
# MODULE_REQUIRES=(graphify) has already been applied), graphify's private
# _graphify_* helpers are no longer in scope - the same reason
# modules/hermes-gateway/module.sh does not reach into modules/hermes/
# module.sh's internals either.
# ---------------------------------------------------------------------------

_graphify_mcp_ensure_runtime_path() {
  case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) ;;
    *)
      PATH="${HOME}/.local/bin:${PATH}"
      export PATH
      ;;
  esac
}

# _graphify_mcp_installer_available
# Same uv-preferred/pipx-fallback detection as
# modules/graphify/module.sh's _graphify_installer_available, honoring the
# same OMES_GRAPHIFY_UV_CMD/OMES_GRAPHIFY_PIPX_CMD test-only overrides for
# consistency.
_graphify_mcp_installer_available() {
  _graphify_mcp_ensure_runtime_path
  if command -v "${OMES_GRAPHIFY_UV_CMD:-uv}" >/dev/null 2>&1; then
    printf 'uv\n'
    return 0
  fi
  if command -v "${OMES_GRAPHIFY_PIPX_CMD:-pipx}" >/dev/null 2>&1; then
    printf 'pipx\n'
    return 0
  fi
  return 1
}

# _graphify_mcp_available
# True when the `graphify-mcp` console script is present and its --help
# exits 0 (never actually starts the stdio server, which would block).
_graphify_mcp_available() {
  _graphify_mcp_ensure_runtime_path
  command -v graphify-mcp >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------

module_check() {
  if omes_is_root; then
    log_error "graphify-mcp: must not run as root - installs into the same per-user tool environment as graphify; re-run as the target user without sudo"
    return 1
  fi

  _graphify_mcp_ensure_runtime_path
  if ! command -v graphify >/dev/null 2>&1; then
    log_error "graphify-mcp: the graphify CLI is not installed - run 'omes install --module graphify' first"
    return 1
  fi

  local installer
  if ! installer="$(_graphify_mcp_installer_available)"; then
    log_error "graphify-mcp: neither uv nor pipx found on PATH - install one of them first (see docs/graphify.md §2), the same requirement as the graphify module itself"
    return 1
  fi
  log_info "graphify-mcp: using ${installer} for install/uninstall"

  if _graphify_mcp_available; then
    log_info "graphify-mcp: already installed"
    return 0
  fi

  log_info "graphify-mcp: not yet installed"
  if ! detect_network; then
    log_error "graphify-mcp: network required to install the graphifyy[mcp] extra"
    return 1
  fi
  return 0
}

module_apply() {
  local installer
  if ! installer="$(_graphify_mcp_installer_available)"; then
    log_error "graphify-mcp: neither uv nor pipx found on PATH"
    return 1
  fi

  if _graphify_mcp_available; then
    log_info "graphify-mcp: already installed; skipping install"
  else
    local pkgspec="$GRAPHIFY_MCP_EXTRA_SPEC"
    if [[ -n "${OMES_GRAPHIFY_VERSION:-}" ]]; then
      pkgspec="graphifyy[mcp]==${OMES_GRAPHIFY_VERSION}"
    fi

    local -a cmd=()
    if [[ "$installer" == "uv" ]]; then
      cmd=(uv tool install "$pkgspec")
    else
      cmd=(pipx install "$pkgspec")
    fi

    if ! omes_run "${cmd[@]}"; then
      log_error "graphify-mcp: install failed via ${installer} (${pkgspec})"
      return 1
    fi
  fi

  return 0
}

module_verify() {
  if omes_dry_run; then
    log_info "[dry-run] would verify: graphify-mcp --help exits 0"
    return 0
  fi

  _graphify_mcp_ensure_runtime_path

  if ! command -v graphify-mcp >/dev/null 2>&1; then
    log_error "graphify-mcp: 'graphify-mcp' is not on PATH after apply"
    return 1
  fi

  if ! graphify-mcp --help >/dev/null 2>&1; then
    log_error "graphify-mcp: 'graphify-mcp --help' failed"
    return 1
  fi

  log_info "graphify-mcp: 'graphify-mcp --help' succeeded"
  return 0
}

module_rollback() {
  local installer
  if ! installer="$(_graphify_mcp_installer_available)"; then
    log_warn "graphify-mcp: rollback found neither uv nor pipx on PATH; nothing to reinstall"
    return 0
  fi

  log_warn "graphify-mcp: rollback reinstalls ${GRAPHIFY_PLAIN_PACKAGE} WITHOUT the [mcp] extra (dropping the graphify-mcp entry point); it never touches graphify-out/ directories, other graphify-produced data, or hermes-gateway"

  if omes_dry_run; then
    log_info "[dry-run] would run: ${installer} tool install --reinstall ${GRAPHIFY_PLAIN_PACKAGE} (or the pipx --force equivalent)"
    return 0
  fi

  local -a cmd=()
  if [[ "$installer" == "uv" ]]; then
    cmd=(uv tool install --reinstall "$GRAPHIFY_PLAIN_PACKAGE")
  else
    cmd=(pipx install --force "$GRAPHIFY_PLAIN_PACKAGE")
  fi

  if ! omes_run "${cmd[@]}"; then
    log_error "graphify-mcp: reinstall via ${installer} failed"
    return 1
  fi

  log_warn "graphify-mcp: reinstalled ${GRAPHIFY_PLAIN_PACKAGE} without the [mcp] extra via ${installer}"
  return 0
}
