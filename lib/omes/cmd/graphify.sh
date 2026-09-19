# omes-help: manage the Graphify CLI tool-env install (update/uninstall)
# shellcheck shell=bash
#
# lib/omes/cmd/graphify.sh - `omes graphify <subcommand>` extension command.
#
# Wraps modules/graphify/module.sh's install detection to expose two
# maintenance subcommands outside the normal install/uninstall lifecycle:
#
#   omes graphify update    [--yes] [--dry-run] [--json]
#   omes graphify uninstall [--yes] [--dry-run] [--json]
#
# `omes graphify run` is NOT implemented in this file - it belongs to the
# Hermes workflow wrapper tracked in issue #51 (docs/graphify.md §3).
# Invoking it here prints a clear "not implemented yet" message and exits
# with a usage error rather than doing nothing silently.
#
# Neither subcommand ever touches graphify-out/ directories, vault
# content, or any other data graphify itself produces - only the
# `graphifyy` tool-env install (docs/graphify.md §1.4, §2).

# _graphify_cmd_parse_flags <args...>
# Extracts --yes/--dry-run/--json from an extension command's own argument
# list (bin/omes only parses global flags up to the first token it does not
# recognize - see docs/cli.md §4.12 - so an extension command must parse
# any of its own flags that appear after its first subcommand token).
# Sets OMES_NONINTERACTIVE/OMES_DRY_RUN/OMES_JSON directly in the current
# shell - deliberately NOT invoked via command/process substitution
# anywhere, since that would run it in a subshell and silently drop these
# assignments. Neither `update` nor `uninstall` takes any other argument,
# so unrecognized tokens are ignored rather than collected.
_graphify_cmd_parse_flags() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --yes)
        # shellcheck disable=SC2034  # read by omes_confirm/omes_noninteractive in core.sh
        OMES_NONINTERACTIVE=1
        ;;
      --dry-run)
        # shellcheck disable=SC2034  # read by omes_dry_run in core.sh
        OMES_DRY_RUN=1
        ;;
      --json)
        OMES_JSON=1
        ;;
      *) ;;
    esac
  done
}

# _graphify_cmd_installer_or_die
# Prints "uv"/"pipx" (uv preferred) or emits a JSON/human error and exits
# 1 when neither is on PATH. Assumes modules/graphify/module.sh has
# already been loaded (module_load graphify).
_graphify_cmd_installer_or_die() {
  local installer
  if installer="$(_graphify_installer_available)"; then
    printf '%s\n' "$installer"
    return 0
  fi

  log_error "graphify: neither uv nor pipx found on PATH; nothing to do"
  if [[ "$OMES_JSON" == "1" ]]; then
    json_obj \
      "$(json_kv command graphify)" \
      "$(json_kv subcommand "$1")" \
      "$(json_kv ok false --raw)" \
      "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
    printf '\n'
  fi
  exit "$OMES_EX_ERROR"
}

_graphify_cmd_update() {
  _graphify_cmd_parse_flags "$@"

  module_load graphify
  local installer
  installer="$(_graphify_cmd_installer_or_die update)"

  if omes_dry_run; then
    log_info "[dry-run] would run: ${installer} tool upgrade graphifyy (or pipx upgrade graphifyy)"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj \
        "$(json_kv command graphify)" \
        "$(json_kv subcommand update)" \
        "$(json_kv ok true --raw)" \
        "$(json_kv installer "$installer")" \
        "$(json_kv exit_code 0 --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_OK"
  fi

  if ! omes_confirm "Upgrade ${GRAPHIFY_PACKAGE} via ${installer}?"; then
    log_error "aborted: confirmation required (re-run with --yes to proceed non-interactively)"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand update)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_ERROR"
  fi

  local -a cmd=()
  if [[ "$installer" == "uv" ]]; then
    cmd=(uv tool upgrade "$GRAPHIFY_PACKAGE")
  else
    cmd=(pipx upgrade "$GRAPHIFY_PACKAGE")
  fi

  if ! omes_run "${cmd[@]}"; then
    log_error "graphify: upgrade via ${installer} failed"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand update)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_ERROR"
  fi

  local resolved
  resolved="$(_graphify_installed_version || true)"
  if [[ -n "$resolved" ]]; then
    state_set "module.graphify.version_installed" "$resolved"
  fi

  if [[ "$OMES_JSON" == "1" ]]; then
    json_obj \
      "$(json_kv command graphify)" \
      "$(json_kv subcommand update)" \
      "$(json_kv ok true --raw)" \
      "$(json_kv installer "$installer")" \
      "$(json_kv version "${resolved}")" \
      "$(json_kv exit_code 0 --raw)"
    printf '\n'
  else
    log_info "graphify: updated via ${installer} (now: ${resolved:-unknown})"
  fi
  exit "$OMES_EX_OK"
}

_graphify_cmd_uninstall() {
  _graphify_cmd_parse_flags "$@"

  module_load graphify
  local installer
  installer="$(_graphify_cmd_installer_or_die uninstall)"

  if omes_dry_run; then
    log_info "[dry-run] would run: ${installer} uninstall graphifyy (never touches graphify-out/ or other data)"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj \
        "$(json_kv command graphify)" \
        "$(json_kv subcommand uninstall)" \
        "$(json_kv ok true --raw)" \
        "$(json_kv installer "$installer")" \
        "$(json_kv exit_code 0 --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_OK"
  fi

  if ! omes_confirm "Uninstall ${GRAPHIFY_PACKAGE} via ${installer}? (graphify-out/ and other data are never touched)"; then
    log_error "aborted: confirmation required (re-run with --yes to proceed non-interactively)"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand uninstall)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_ERROR"
  fi

  local -a cmd=()
  if [[ "$installer" == "uv" ]]; then
    cmd=(uv tool uninstall "$GRAPHIFY_PACKAGE")
  else
    cmd=(pipx uninstall "$GRAPHIFY_PACKAGE")
  fi

  if ! omes_run "${cmd[@]}"; then
    log_error "graphify: uninstall via ${installer} failed"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand uninstall)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_ERROR"
  fi

  state_unset "module.graphify.version_installed"
  log_warn "graphify: removed ${GRAPHIFY_PACKAGE} via ${installer} tool uninstall; graphify-out/ and other data are untouched"

  if [[ "$OMES_JSON" == "1" ]]; then
    json_obj \
      "$(json_kv command graphify)" \
      "$(json_kv subcommand uninstall)" \
      "$(json_kv ok true --raw)" \
      "$(json_kv installer "$installer")" \
      "$(json_kv exit_code 0 --raw)"
    printf '\n'
  fi
  exit "$OMES_EX_OK"
}

cmd_graphify() {
  local sub="${1:-}"
  if [[ -n "$sub" ]]; then
    shift
  fi

  case "$sub" in
    update)
      _graphify_cmd_update "$@"
      ;;
    uninstall)
      _graphify_cmd_uninstall "$@"
      ;;
    run)
      log_error "graphify: 'omes graphify run' is not implemented yet (tracked in #51)"
      exit "$OMES_EX_USAGE"
      ;;
    *)
      log_error "graphify: usage: omes graphify {update|uninstall} [--yes] [--dry-run] [--json]"
      exit "$OMES_EX_USAGE"
      ;;
  esac
}
