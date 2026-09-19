# omes-help: manage and run the Graphify CLI (update/uninstall/run/skill)
# shellcheck shell=bash
#
# lib/omes/cmd/graphify.sh - `omes graphify <subcommand>` extension command.
#
#   omes graphify update             [--yes] [--dry-run] [--json]
#   omes graphify uninstall          [--yes] [--dry-run] [--json]
#   omes graphify run <path>         [--mode code|semantic] [--backend <name>]
#                                     [--out <dir>] [--yes] [--dry-run] [--json]
#   omes graphify skill install      [--yes] [--dry-run] [--json]
#   omes graphify skill uninstall    [--yes] [--dry-run] [--json]
#   omes graphify mcp health         [--graph <path>] [--json]
#
# Full synopsis/exit-codes/JSON schema for every subcommand: docs/graphify.md
# §2 (update/uninstall, issue #50), §3 (run/skill, issue #51), and §4
# (mcp health, issue #52). `omes install`/`uninstall --module graphify-mcp`
# (modules/graphify-mcp/module.sh) install/remove the `graphifyy[mcp]`
# extra itself - `mcp health` here is a read-only status check only, and
# never touches hermes-gateway or any Hermes runtime state.
#
# `run` never touches graphify-out/ content beyond writing its own
# omes-provenance.json sidecar (docs/graphify.md §3.2); `update`/
# `uninstall` never touch graphify-out/ at all - only the `graphifyy`
# tool-env install (docs/graphify.md §1.4, §2).

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

# _graphify_hermes_home
# Prints the resolved HERMES_HOME (OMES_HERMES_HOME override, else the
# upstream default ~/.hermes) - the same resolution modules/hermes/module.sh
# uses, duplicated here in miniature because this extension command does
# not source/depend on the hermes module.
_graphify_hermes_home() {
  printf '%s\n' "${OMES_HERMES_HOME:-${HOME}/.hermes}"
}

# _graphify_run_emit_json <ok> <extra-kv...>
# Emits the {"command":"graphify","subcommand":"run",...} JSON envelope
# when OMES_JSON=1, with $ok as the "ok" field and any additional
# already-built json_kv pairs spliced in before "exit_code".
_graphify_run_json() {
  local ok="$1"
  local exit_code="$2"
  shift 2
  [[ "$OMES_JSON" == "1" ]] || return 0
  json_obj \
    "$(json_kv command graphify)" \
    "$(json_kv subcommand run)" \
    "$(json_kv ok "$ok" --raw)" \
    "$@" \
    "$(json_kv exit_code "$exit_code" --raw)"
  printf '\n'
}

_graphify_cmd_run() {
  local path="" mode="code" backend="" out=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --mode)
        [[ $# -ge 2 ]] || {
          log_error "graphify: --mode requires an argument"; exit "$OMES_EX_USAGE"
        }
        mode="$2"
        shift 2
        ;;
      --backend)
        [[ $# -ge 2 ]] || {
          log_error "graphify: --backend requires an argument"; exit "$OMES_EX_USAGE"
        }
        backend="$2"
        shift 2
        ;;
      --out)
        [[ $# -ge 2 ]] || {
          log_error "graphify: --out requires an argument"; exit "$OMES_EX_USAGE"
        }
        out="$2"
        shift 2
        ;;
      --yes)
        # shellcheck disable=SC2034  # read by omes_confirm/omes_noninteractive in core.sh
        OMES_NONINTERACTIVE=1
        shift
        ;;
      --dry-run)
        # shellcheck disable=SC2034  # read by omes_dry_run in core.sh
        OMES_DRY_RUN=1
        shift
        ;;
      --json)
        OMES_JSON=1
        shift
        ;;
      -*)
        log_error "graphify: unknown flag: $1"
        exit "$OMES_EX_USAGE"
        ;;
      *)
        if [[ -n "$path" ]]; then
          log_error "graphify: unexpected extra argument: $1"
          exit "$OMES_EX_USAGE"
        fi
        path="$1"
        shift
        ;;
    esac
  done

  if [[ -z "$path" ]]; then
    log_error "graphify: usage: omes graphify run <path> [--mode code|semantic] [--backend <name>] [--out <dir>] [--yes] [--dry-run] [--json]"
    exit "$OMES_EX_USAGE"
  fi

  case "$mode" in
    code | semantic) ;;
    *)
      log_error "graphify: invalid --mode '${mode}' (must be 'code' or 'semantic')"
      exit "$OMES_EX_USAGE"
      ;;
  esac

  if [[ ! -e "$path" ]]; then
    log_error "graphify: path does not exist: ${path}"
    exit "$OMES_EX_USAGE"
  fi

  local resolved
  if ! resolved="$(realpath "$path" 2>/dev/null)"; then
    log_error "graphify: failed to resolve path: ${path}"
    exit "$OMES_EX_USAGE"
  fi

  # Path validation: refuse a resolved path that is itself named
  # graphify-out/ or lives inside one - re-extracting graphify's own
  # output directory is never useful (docs/graphify.md §3.1).
  if [[ "$(basename "$resolved")" == "graphify-out" ]] || [[ "/${resolved}/" == */graphify-out/* ]]; then
    log_error "graphify: refusing to run on a path inside a graphify-out/ directory: ${resolved}"
    exit "$OMES_EX_USAGE"
  fi

  module_load graphify
  if ! command -v graphify >/dev/null 2>&1; then
    log_error "graphify: the graphify CLI is not installed - run 'omes install --module graphify' first"
    exit "$OMES_EX_USAGE"
  fi

  local out_dir
  if [[ -n "$out" ]]; then
    out_dir="$out"
  elif [[ -d "$resolved" ]]; then
    out_dir="${resolved}/graphify-out"
  else
    out_dir="$(dirname "$resolved")/graphify-out"
  fi

  # Mode gating: semantic requires OMES_GRAPHIFY_PROVIDER_ENV to name a
  # non-empty credential variable. Only the NAME is ever read/logged/
  # recorded - the credential's own value is checked for non-emptiness via
  # indirect expansion and never appears anywhere else (docs/graphify.md §3.1).
  local provider_env_var=""
  if [[ "$mode" == "semantic" ]]; then
    if [[ -z "${OMES_GRAPHIFY_PROVIDER_ENV:-}" ]]; then
      log_error "graphify: --mode semantic requires OMES_GRAPHIFY_PROVIDER_ENV=<VAR_NAME> naming a provider credential env var (e.g. ANTHROPIC_API_KEY); code-only mode remains the default without it"
      _graphify_run_json false "$OMES_EX_USAGE"
      exit "$OMES_EX_USAGE"
    fi
    provider_env_var="$OMES_GRAPHIFY_PROVIDER_ENV"
    if [[ -z "${!provider_env_var:-}" ]]; then
      log_error "graphify: OMES_GRAPHIFY_PROVIDER_ENV names '${provider_env_var}' but that variable is not set (or is empty) in this environment"
      _graphify_run_json false "$OMES_EX_USAGE"
      exit "$OMES_EX_USAGE"
    fi
  fi

  log_info "graphify: path: ${resolved}"
  log_info "graphify: mode: ${mode}"
  log_info "graphify: output directory: ${out_dir}"
  if [[ "$mode" == "code" ]]; then
    log_info "graphify: code-only mode produces EXTRACTED edges only (no semantic/INFERRED edges - see docs/graphify.md §1.6)"
  else
    log_info "graphify: semantic mode may add INFERRED edges (LLM-derived) alongside EXTRACTED (AST-derived) edges - see docs/graphify.md §1.6"
  fi

  local -a cmd=()
  if [[ "$mode" == "code" ]]; then
    cmd=(graphify extract "$resolved" --code-only)
  else
    cmd=(graphify extract "$resolved")
    if [[ -n "$backend" ]]; then
      cmd+=(--backend "$backend")
    fi
  fi
  if [[ -n "$out" ]]; then
    cmd+=(--out "$out")
  fi

  if omes_dry_run; then
    log_info "[dry-run] would run: ${cmd[*]}"
    _graphify_run_json true 0 \
      "$(json_kv mode "$mode")" \
      "$(json_kv path "$resolved")" \
      "$(json_kv out_dir "$out_dir")"
    exit "$OMES_EX_OK"
  fi

  if [[ "$mode" == "semantic" ]]; then
    if ! omes_confirm "Run semantic extraction on ${resolved} via provider env '${provider_env_var}'? This calls an external LLM API."; then
      log_error "aborted: confirmation required (re-run with --yes to proceed non-interactively)"
      _graphify_run_json false "$OMES_EX_ERROR"
      exit "$OMES_EX_ERROR"
    fi
  fi

  if ! omes_run "${cmd[@]}"; then
    log_error "graphify: extraction failed"
    _graphify_run_json false "$OMES_EX_ERROR"
    exit "$OMES_EX_ERROR"
  fi

  # Provenance sidecar (docs/graphify.md §3.2) - OMES's own audit record,
  # layered on top of graphify's own EXTRACTED/INFERRED edge tagging, never
  # replacing it. provider_env_var records only the NAME of the credential
  # variable, never its value.
  mkdir -p "$out_dir"
  local provenance_file="${out_dir}/omes-provenance.json"
  local generated_at graphify_version omes_version invoked_by
  generated_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  graphify_version="$(_graphify_installed_version 2>/dev/null || true)"
  graphify_version="${graphify_version#graphify }"
  omes_version="$(cat "${OMES_ROOT}/VERSION" 2>/dev/null || true)"
  invoked_by="$(id -un 2>/dev/null || printf 'unknown')"

  local backend_kv provider_kv
  if [[ -n "$backend" ]]; then
    backend_kv="$(json_kv backend "$backend")"
  else
    backend_kv="$(json_kv backend null --raw)"
  fi
  if [[ -n "$provider_env_var" ]]; then
    provider_kv="$(json_kv provider_env_var "$provider_env_var")"
  else
    provider_kv="$(json_kv provider_env_var null --raw)"
  fi

  {
    json_obj \
      "$(json_kv generated_at "$generated_at")" \
      "$(json_kv mode "$mode")" \
      "$(json_kv path "$resolved")" \
      "$(json_kv out_dir "$out_dir")" \
      "$backend_kv" \
      "$provider_kv" \
      "$(json_kv graphify_version "$graphify_version")" \
      "$(json_kv omes_version "$omes_version")" \
      "$(json_kv invoked_by "$invoked_by")"
    printf '\n'
  } >"$provenance_file"

  log_info "graphify: wrote provenance sidecar: ${provenance_file}"

  _graphify_run_json true 0 \
    "$(json_kv mode "$mode")" \
    "$(json_kv path "$resolved")" \
    "$(json_kv out_dir "$out_dir")" \
    "$(json_kv provenance_file "$provenance_file")"
  exit "$OMES_EX_OK"
}

_graphify_cmd_skill_install() {
  _graphify_cmd_parse_flags "$@"

  local hermes_home target_dir
  hermes_home="$(_graphify_hermes_home)"
  target_dir="${hermes_home}/skills/graphify"
  local source_dir="${OMES_ROOT}/modules/graphify/skill"

  if omes_dry_run; then
    log_info "[dry-run] would install ${source_dir}/{SKILL.md,run.sh} into ${target_dir}"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj \
        "$(json_kv command graphify)" \
        "$(json_kv subcommand skill)" \
        "$(json_kv action install)" \
        "$(json_kv ok true --raw)" \
        "$(json_kv target_dir "$target_dir")" \
        "$(json_kv exit_code 0 --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_OK"
  fi

  if ! omes_confirm "Install the Graphify Hermes skill into ${target_dir}?"; then
    log_error "aborted: confirmation required (re-run with --yes to proceed non-interactively)"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand skill)" "$(json_kv action install)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_ERROR"
  fi

  mkdir -p "$target_dir"
  backup_begin "graphify-skill" "pre-apply" >/dev/null
  omes_manage_path "${target_dir}/SKILL.md"
  omes_manage_path "${target_dir}/run.sh"
  cp "${source_dir}/SKILL.md" "${target_dir}/SKILL.md"
  cp "${source_dir}/run.sh" "${target_dir}/run.sh"
  chmod +x "${target_dir}/run.sh"
  backup_finish >/dev/null

  log_info "graphify: installed Hermes skill into ${target_dir}"
  if ! command -v omes >/dev/null 2>&1; then
    log_warn "graphify: 'omes' is not currently on PATH - ${target_dir}/run.sh will not find it when Hermes invokes this skill until it is"
  fi

  if [[ "$OMES_JSON" == "1" ]]; then
    json_obj \
      "$(json_kv command graphify)" \
      "$(json_kv subcommand skill)" \
      "$(json_kv action install)" \
      "$(json_kv ok true --raw)" \
      "$(json_kv target_dir "$target_dir")" \
      "$(json_kv exit_code 0 --raw)"
    printf '\n'
  fi
  exit "$OMES_EX_OK"
}

_graphify_cmd_skill_uninstall() {
  _graphify_cmd_parse_flags "$@"

  local hermes_home target_dir
  hermes_home="$(_graphify_hermes_home)"
  target_dir="${hermes_home}/skills/graphify"

  if omes_dry_run; then
    log_info "[dry-run] would remove ${target_dir}/SKILL.md and ${target_dir}/run.sh"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj \
        "$(json_kv command graphify)" \
        "$(json_kv subcommand skill)" \
        "$(json_kv action uninstall)" \
        "$(json_kv ok true --raw)" \
        "$(json_kv target_dir "$target_dir")" \
        "$(json_kv exit_code 0 --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_OK"
  fi

  if ! omes_confirm "Remove the Graphify Hermes skill from ${target_dir}?"; then
    log_error "aborted: confirmation required (re-run with --yes to proceed non-interactively)"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand skill)" "$(json_kv action uninstall)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_ERROR"
  fi

  rm -f "${target_dir}/SKILL.md" "${target_dir}/run.sh"
  log_warn "graphify: removed the Hermes skill's SKILL.md/run.sh from ${target_dir}; no other file under \$HERMES_HOME/skills/ was touched"

  if [[ "$OMES_JSON" == "1" ]]; then
    json_obj \
      "$(json_kv command graphify)" \
      "$(json_kv subcommand skill)" \
      "$(json_kv action uninstall)" \
      "$(json_kv ok true --raw)" \
      "$(json_kv target_dir "$target_dir")" \
      "$(json_kv exit_code 0 --raw)"
    printf '\n'
  fi
  exit "$OMES_EX_OK"
}

_graphify_cmd_skill() {
  local action="${1:-}"
  if [[ -n "$action" ]]; then
    shift
  fi

  case "$action" in
    install)
      _graphify_cmd_skill_install "$@"
      ;;
    uninstall)
      _graphify_cmd_skill_uninstall "$@"
      ;;
    *)
      log_error "graphify: usage: omes graphify skill {install|uninstall} [--yes] [--dry-run] [--json]"
      exit "$OMES_EX_USAGE"
      ;;
  esac
}

# _graphify_cmd_mcp_health
# Read-only health check for the optional graphify-mcp entry point
# (modules/graphify-mcp/module.sh, issue #52). Never installs, uninstalls,
# or spawns a running MCP session; never touches hermes-gateway or any
# Hermes runtime state - a broken or absent graphify-mcp can only ever
# affect an MCP client's ability to reach Graphify's tools, never normal
# Hermes operation (docs/graphify.md §4).
_graphify_cmd_mcp_health() {
  local graph=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --graph)
        [[ $# -ge 2 ]] || {
          log_error "graphify: --graph requires an argument"; exit "$OMES_EX_USAGE"
        }
        graph="$2"
        shift 2
        ;;
      --json)
        OMES_JSON=1
        shift
        ;;
      *)
        log_error "graphify: unknown argument to 'mcp health': $1"
        exit "$OMES_EX_USAGE"
        ;;
    esac
  done

  local status="ok"
  local detail=""
  local resolved_graph=""

  if ! command -v graphify-mcp >/dev/null 2>&1; then
    status="not_applicable"
    detail="graphify-mcp is not installed - run 'omes install --module graphify-mcp' to enable it; 'graphify query'/'omes graphify run' remain available regardless"
  elif ! graphify-mcp --help >/dev/null 2>&1; then
    status="unhealthy"
    detail="'graphify-mcp --help' failed"
  else
    resolved_graph="${graph:-graphify-out/graph.json}"
    if [[ ! -f "$resolved_graph" ]]; then
      status="unhealthy"
      detail="graph file not found: ${resolved_graph} (run 'omes graphify run <path>' first, or pass --graph)"
    fi
  fi

  case "$status" in
    ok)
      log_info "graphify: mcp health: ok (graphify-mcp is installed and responsive; graph: ${resolved_graph})"
      ;;
    not_applicable)
      log_info "graphify: mcp health: not_applicable - ${detail}"
      ;;
    unhealthy)
      log_error "graphify: mcp health: unhealthy - ${detail}"
      ;;
  esac

  if [[ "$OMES_JSON" == "1" ]]; then
    local ok_bool="true"
    [[ "$status" == "unhealthy" ]] && ok_bool="false"
    json_obj \
      "$(json_kv command graphify)" \
      "$(json_kv subcommand mcp)" \
      "$(json_kv action health)" \
      "$(json_kv ok "$ok_bool" --raw)" \
      "$(json_kv status "$status")" \
      "$(json_kv detail "$detail")" \
      "$(json_kv graph "$resolved_graph")" \
      "$(json_kv exit_code 0 --raw)"
    printf '\n'
  fi

  # not_applicable is an expected default state (MCP is opt-in and
  # disabled by default, issue #52 criterion 3), not a failure - only a
  # genuinely broken install (installed but non-functional, or an
  # explicitly requested graph missing) exits non-zero.
  if [[ "$status" == "unhealthy" ]]; then
    exit "$OMES_EX_ERROR"
  fi
  exit "$OMES_EX_OK"
}

_graphify_cmd_mcp() {
  local action="${1:-}"
  if [[ -n "$action" ]]; then
    shift
  fi

  case "$action" in
    health)
      _graphify_cmd_mcp_health "$@"
      ;;
    *)
      log_error "graphify: usage: omes graphify mcp health [--graph <path>] [--json]"
      exit "$OMES_EX_USAGE"
      ;;
  esac
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
      _graphify_cmd_run "$@"
      ;;
    skill)
      _graphify_cmd_skill "$@"
      ;;
    mcp)
      _graphify_cmd_mcp "$@"
      ;;
    *)
      log_error "graphify: usage: omes graphify {update|uninstall|run|skill|mcp} [args...]"
      exit "$OMES_EX_USAGE"
      ;;
  esac
}
