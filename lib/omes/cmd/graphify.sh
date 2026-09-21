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
#   omes graphify export <out-dir>   --vault <path> [--init-vault]
#                                     [--dry-run] [--yes] [--json]
#   omes graphify export rollback    [--timestamp <ts>] [--yes] [--json]
#   omes graphify sync <path>        [--vault <path>] [--min-interval <sec>]
#                                     [--allow-nested-vault] [--dry-run]
#                                     [--yes] [--json]
#   omes graphify status <path>      [--vault <path>] [--json]
#   omes graphify init-ignore <path> [--dry-run] [--yes] [--json]
#   omes graphify purge <path>       [--vault <path>] [--dry-run] [--yes]
#                                     [--json]
#
# Full synopsis/exit-codes/JSON schema for every subcommand: docs/graphify.md
# §2 (update/uninstall, issue #50), §3 (run/skill, issue #51), §4
# (mcp health, issue #52), §5 (export/export rollback, issue #53), §6
# (sync/status, issue #54), and docs/graphify-privacy.md (init-ignore/purge,
# issue #55). `omes install`/`uninstall --module graphify-mcp`
# (modules/graphify-mcp/module.sh) install/remove the `graphifyy[mcp]`
# extra itself - `mcp health` here is a read-only status check only, and
# never touches hermes-gateway or any Hermes runtime state.
#
# `run` never touches graphify-out/ content beyond writing its own
# omes-provenance.json sidecar (docs/graphify.md §3.2); `update`/
# `uninstall` never touch graphify-out/ at all - only the `graphifyy`
# tool-env install (docs/graphify.md §1.4, §2). `export` writes ONLY under
# <vault>/<subdir>/ (docs/graphify.md §5) - it never touches unrelated
# vault notes or the vault's .obsidian/ directory. `sync`/`status` never
# touch a vault at all - `--vault` there is accepted ONLY for loop
# avoidance (excluding it from the source-tree scan / refusing a nested
# vault), see docs/graphify.md §6.3.

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

# _graphify_version_lt <v1> <v2>
# Returns 0 if v1 < v2 under version sort.
_graphify_version_lt() {
  local v1="$1" v2="$2"
  if [[ "$v1" == "$v2" ]]; then
    return 1
  fi
  local lowest
  lowest="$(printf '%s\n%s\n' "$v1" "$v2" | sort -V | head -n 1)"
  [[ "$lowest" == "$v1" ]]
}

# _graphify_version_gt <v1> <v2>
# Returns 0 if v1 > v2 under version sort.
_graphify_version_gt() {
  local v1="$1" v2="$2"
  if [[ "$v1" == "$v2" ]]; then
    return 1
  fi
  local lowest
  lowest="$(printf '%s\n%s\n' "$v1" "$v2" | sort -V | head -n 1)"
  [[ "$lowest" == "$v2" ]]
}

# _graphify_version_policy_check
# Enforces version baseline and compatibility policies (ADR-0025, issue #180):
# - Pinning: must match OMES_GRAPHIFY_VERSION if set.
# - Minimum baseline: must be >= 0.9.64 (or OMES_GRAPHIFY_MIN_VERSION).
# - Candidate warning: warns if newer than released-supported baseline 0.9.64.
_graphify_version_policy_check() {
  local version_str
  version_str="$(_graphify_installed_version 2>/dev/null || true)"
  if [[ -z "$version_str" ]]; then
    log_error "graphify: the graphify CLI is not installed or not runnable"
    return 1
  fi
  local ver="${version_str#graphify }"
  ver="$(printf '%s' "$ver" | tr -d '[:space:]')"

  if [[ -n "${OMES_GRAPHIFY_VERSION:-}" ]] && [[ "$ver" != "${OMES_GRAPHIFY_VERSION}" ]]; then
    log_error "graphify: installed version '${ver}' does not match pinned OMES_GRAPHIFY_VERSION '${OMES_GRAPHIFY_VERSION}'"
    return 1
  fi

  local min_base="0.9.64"
  if [[ -n "${OMES_GRAPHIFY_MIN_VERSION:-}" ]]; then
    min_base="${OMES_GRAPHIFY_MIN_VERSION}"
  fi

  if _graphify_version_lt "$ver" "$min_base"; then
    log_error "graphify: installed version '${ver}' is older than minimum supported baseline '${min_base}' (ADR-0025)"
    return 1
  fi

  if _graphify_version_gt "$ver" "0.9.64"; then
    log_warn "graphify: installed version '${ver}' is newer than released-supported baseline (0.9.64); running with candidate upstream capability"
  fi
  return 0
}

# _graphify_supports_upstream_skill_install
# Returns 0 if installed graphify CLI supports `graphify install --platform hermes`
_graphify_supports_upstream_skill_install() {
  if [[ "${SHIM_GRAPHIFY_UPSTREAM_INSTALL:-0}" == "1" ]]; then
    return 0
  fi
  command -v graphify >/dev/null 2>&1 || return 1
  local help_out
  if help_out="$(graphify install --help 2>&1)" && [[ "$help_out" == *"--platform"* ]]; then
    return 0
  fi
  return 1
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
  if ! command -v graphify >/dev/null 2>&1 || ! _graphify_installed_version >/dev/null 2>&1; then
    log_error "graphify: the graphify CLI is not installed - run 'omes install --module graphify' first"
    exit "$OMES_EX_USAGE"
  fi

  if ! _graphify_version_policy_check; then
    _graphify_run_json false "$OMES_EX_ERROR"
    exit "$OMES_EX_ERROR"
  fi

  log_info "graphify: 'omes graphify run' is a compatibility wrapper for 'graphify extract' with OMES safety checks and provenance (ADR-0025)"

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

_graphify_cmd_extract() {
  _graphify_cmd_run "$@"
}

_graphify_cmd_query() {
  _graphify_cmd_parse_flags "$@"
  module_load graphify
  if ! command -v graphify >/dev/null 2>&1 || ! _graphify_installed_version >/dev/null 2>&1; then
    log_error "graphify: the graphify CLI is not installed - run 'omes install --module graphify' first"
    exit "$OMES_EX_USAGE"
  fi
  if ! _graphify_version_policy_check; then
    exit "$OMES_EX_ERROR"
  fi

  if omes_dry_run; then
    log_info "[dry-run] would run: graphify query $*"
    exit "$OMES_EX_OK"
  fi

  omes_run graphify query "$@"
}

_graphify_cmd_hook() {
  _graphify_cmd_parse_flags "$@"
  module_load graphify
  if ! command -v graphify >/dev/null 2>&1 || ! _graphify_installed_version >/dev/null 2>&1; then
    log_error "graphify: the graphify CLI is not installed - run 'omes install --module graphify' first"
    exit "$OMES_EX_USAGE"
  fi
  if ! _graphify_version_policy_check; then
    exit "$OMES_EX_ERROR"
  fi

  if omes_dry_run; then
    log_info "[dry-run] would run: graphify hook $*"
    exit "$OMES_EX_OK"
  fi

  omes_run graphify hook "$@"
}

_graphify_cmd_skill_install() {
  _graphify_cmd_parse_flags "$@"

  local hermes_home target_dir
  hermes_home="$(_graphify_hermes_home)"
  target_dir="${hermes_home}/skills/graphify"
  local source_dir="${OMES_ROOT}/modules/graphify/skill"

  module_load graphify
  if ! command -v graphify >/dev/null 2>&1 || ! _graphify_installed_version >/dev/null 2>&1; then
    log_error "graphify: the graphify CLI is not installed - run 'omes install --module graphify' first"
    exit "$OMES_EX_USAGE"
  fi
  if ! _graphify_version_policy_check; then
    exit "$OMES_EX_ERROR"
  fi

  if _graphify_supports_upstream_skill_install; then
    if omes_dry_run; then
      log_info "[dry-run] would run: graphify install --platform hermes (delegated upstream)"
      if [[ "$OMES_JSON" == "1" ]]; then
        json_obj \
          "$(json_kv command graphify)" \
          "$(json_kv subcommand skill)" \
          "$(json_kv action install)" \
          "$(json_kv ok true --raw)" \
          "$(json_kv mode upstream)" \
          "$(json_kv target_dir "$target_dir")" \
          "$(json_kv exit_code 0 --raw)"
        printf '\n'
      fi
      exit "$OMES_EX_OK"
    fi

    if ! omes_confirm "Install the Graphify Hermes skill via upstream 'graphify install --platform hermes' into ${target_dir}?"; then
      log_error "aborted: confirmation required (re-run with --yes to proceed non-interactively)"
      if [[ "$OMES_JSON" == "1" ]]; then
        json_obj "$(json_kv command graphify)" "$(json_kv subcommand skill)" "$(json_kv action install)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
        printf '\n'
      fi
      exit "$OMES_EX_ERROR"
    fi

    mkdir -p "$target_dir"
    backup_begin "graphify-skill" "pre-apply" >/dev/null
    if [[ -f "${target_dir}/SKILL.md" ]]; then
      omes_manage_path "${target_dir}/SKILL.md"
    fi
    if [[ -f "${target_dir}/run.sh" ]]; then
      omes_manage_path "${target_dir}/run.sh"
    fi

    if ! omes_run graphify install --platform hermes; then
      backup_abort >/dev/null || true
      log_error "graphify: upstream 'graphify install --platform hermes' failed"
      if [[ "$OMES_JSON" == "1" ]]; then
        json_obj "$(json_kv command graphify)" "$(json_kv subcommand skill)" "$(json_kv action install)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
        printf '\n'
      fi
      exit "$OMES_EX_ERROR"
    fi
    backup_finish >/dev/null

    log_info "graphify: installed Hermes skill via upstream 'graphify install --platform hermes' into ${target_dir}"

    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj \
        "$(json_kv command graphify)" \
        "$(json_kv subcommand skill)" \
        "$(json_kv action install)" \
        "$(json_kv ok true --raw)" \
        "$(json_kv mode upstream)" \
        "$(json_kv target_dir "$target_dir")" \
        "$(json_kv exit_code 0 --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_OK"
  fi

  # Fallback to bundled skill when upstream install --platform hermes is unavailable
  if omes_dry_run; then
    log_info "[dry-run] would install ${source_dir}/{SKILL.md,run.sh} into ${target_dir} (bundled fallback)"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj \
        "$(json_kv command graphify)" \
        "$(json_kv subcommand skill)" \
        "$(json_kv action install)" \
        "$(json_kv ok true --raw)" \
        "$(json_kv mode bundled_fallback)" \
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

  log_info "graphify: installed Hermes skill into ${target_dir} (bundled fallback; upstream 'graphify install --platform hermes' is candidate)"
  log_warn "graphify: bundled skill is deprecated; will be removed when minimum supported baseline advances to upstream release with native Hermes skill installer"

  if ! command -v omes >/dev/null 2>&1; then
    log_warn "graphify: 'omes' is not currently on PATH - ${target_dir}/run.sh will not find it when Hermes invokes this skill until it is"
  fi

  if [[ "$OMES_JSON" == "1" ]]; then
    json_obj \
      "$(json_kv command graphify)" \
      "$(json_kv subcommand skill)" \
      "$(json_kv action install)" \
      "$(json_kv ok true --raw)" \
      "$(json_kv mode bundled_fallback)" \
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

# _graphify_export_py <action> <arg...>
# Invokes lib/omes/py/graphify/cli.py (issue #53) with the given action
# ("plan"/"write"/"plan-upstream"/"write-upstream") and its own
# already-built --flag arguments, printing its single-line JSON object on
# stdout and returning its exit status unchanged.
_graphify_export_py() {
  local action="$1"
  shift
  local py_root="${OMES_ROOT}/lib/omes/py"
  (
    cd "$py_root" || exit 1
    python3 -m graphify.cli "$action" "$@"
  )
}

# _graphify_stage_upstream_export <graph-json> <staging-dir>
# Runs upstream `graphify export obsidian --graph <graph-json> --dir
# <staging-dir>` (verified 2026-09-19 against graphify 0.9.64 to produce
# real vault-ready Markdown with its own YAML front matter, plus a canvas
# file and its own generated-files manifest - docs/graphify.md §5.2).
# Writes ONLY into the throwaway <staging-dir>, never the vault, so this
# runs unconditionally (even under `omes graphify export --dry-run`) -
# there is nothing to preview here, only a real vault write later is
# gated by dry-run/confirmation. Returns 1 (with staging-dir left as-is
# for inspection in logs) when the command fails or leaves no manifest
# behind - the caller falls back to OMES's own renderer in that case.
_graphify_stage_upstream_export() {
  local graph_json="$1" staging_dir="$2"
  if ! graphify export obsidian --graph "$graph_json" --dir "$staging_dir" >/dev/null 2>&1; then
    return 1
  fi
  [[ -f "${staging_dir}/.graphify_obsidian_manifest.json" ]]
}

# _graphify_json_field <json> <field>
# Extracts a single string/number field from a small, trusted JSON object
# (the graphify.cli output above) without a jq dependency. Only used for
# already-validated, OMES-produced JSON, never operator input.
_graphify_json_field() {
  local json="$1" field="$2"
  python3 -c 'import json,sys; d=json.loads(sys.argv[1]); v=d.get(sys.argv[2]); print(v if v is not None else "")' "$json" "$field" 2>/dev/null
}

_graphify_cmd_export_rollback() {
  local ts=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --timestamp)
        [[ $# -ge 2 ]] || {
          log_error "graphify: --timestamp requires an argument"; exit "$OMES_EX_USAGE"
        }
        ts="$2"
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
      *)
        log_error "graphify: unknown argument to 'export rollback': $1"
        exit "$OMES_EX_USAGE"
        ;;
    esac
  done

  local base
  base="$(omes_state_dir)/backups"
  local target=""
  if [[ -n "$ts" ]]; then
    if [[ -d "${base}/${ts}" ]] && grep -qxF "module=graphify-export" "${base}/${ts}/META" 2>/dev/null; then
      target="$ts"
    fi
  else
    local name
    while IFS= read -r name; do
      if grep -qxF "module=graphify-export" "${base}/${name}/META" 2>/dev/null; then
        target="$name"
      fi
    done < <(backup_list)
  fi

  if [[ -z "$target" ]]; then
    log_error "graphify: export rollback: no graphify-export backup session found${ts:+ for timestamp ${ts}}"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand export)" "$(json_kv action rollback)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_BACKUP" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_BACKUP"
  fi

  if ! omes_dry_run && ! omes_confirm "Restore the graphify Obsidian export backup ${target}? This overwrites current notes under the exported vault subdirectory."; then
    log_error "aborted: confirmation required (re-run with --yes to proceed non-interactively)"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand export)" "$(json_kv action rollback)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_ERROR"
  fi

  if ! restore_backup "$target"; then
    log_error "graphify: export rollback failed for backup ${target}"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand export)" "$(json_kv action rollback)" "$(json_kv ok false --raw)" "$(json_kv backup "$target")" "$(json_kv exit_code "$OMES_EX_BACKUP" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_BACKUP"
  fi

  log_info "graphify: export rollback: restored backup ${target}"
  if [[ "$OMES_JSON" == "1" ]]; then
    json_obj "$(json_kv command graphify)" "$(json_kv subcommand export)" "$(json_kv action rollback)" "$(json_kv ok true --raw)" "$(json_kv backup "$target")" "$(json_kv exit_code 0 --raw)"
    printf '\n'
  fi
  exit "$OMES_EX_OK"
}

# _graphify_cmd_export <graphify-out-dir> --vault <path> [--init-vault]
#                       [--dry-run] [--yes] [--json]
# Renders vault-ready Markdown notes from <graphify-out-dir>/graph.json
# into <vault>/<subdir>/ ONLY (docs/graphify.md §5). Never touches any
# other note or the vault's .obsidian/ directory. See lib/omes/py/graphify/
# obsidian.py for the note-rendering logic and its own overwrite-refusal
# rule (a target file lacking the omes_generated marker is never
# overwritten - it is reported as a conflict instead).
_graphify_cmd_export() {
  if [[ "${1:-}" == "rollback" ]]; then
    shift
    _graphify_cmd_export_rollback "$@"
    # shellcheck disable=SC2317  # unreachable only because _graphify_cmd_export_rollback always exits
    return
  fi

  local out_dir="" vault="" init_vault=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --vault)
        [[ $# -ge 2 ]] || {
          log_error "graphify: --vault requires an argument"; exit "$OMES_EX_USAGE"
        }
        vault="$2"
        shift 2
        ;;
      --init-vault)
        init_vault=1
        shift
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
        if [[ -n "$out_dir" ]]; then
          log_error "graphify: unexpected extra argument: $1"
          exit "$OMES_EX_USAGE"
        fi
        out_dir="$1"
        shift
        ;;
    esac
  done

  if [[ -z "$out_dir" ]]; then
    log_error "graphify: usage: omes graphify export <graphify-out-dir> --vault <path> [--init-vault] [--dry-run] [--yes] [--json]"
    exit "$OMES_EX_USAGE"
  fi

  vault="${vault:-${OBSIDIAN_VAULT_PATH:-}}"
  if [[ -z "$vault" ]]; then
    log_error "graphify: --vault is required (or set OBSIDIAN_VAULT_PATH) - the vault path is never guessed"
    exit "$OMES_EX_USAGE"
  fi

  if [[ ! -e "$out_dir" ]]; then
    log_error "graphify: graphify-out directory does not exist: ${out_dir}"
    exit "$OMES_EX_USAGE"
  fi
  local resolved_out
  if ! resolved_out="$(realpath "$out_dir" 2>/dev/null)"; then
    log_error "graphify: failed to resolve path: ${out_dir}"
    exit "$OMES_EX_USAGE"
  fi

  local graph_json="${resolved_out}/graph.json"
  if [[ ! -f "$graph_json" ]]; then
    log_error "graphify: graph.json not found in ${resolved_out} (run 'omes graphify run <path>' first)"
    exit "$OMES_EX_USAGE"
  fi

  if [[ ! -e "$vault" ]] && [[ "$init_vault" -ne 1 ]]; then
    log_error "graphify: vault does not exist: ${vault} (pass --init-vault to create a new vault at this path)"
    exit "$OMES_EX_USAGE"
  fi

  local resolved_vault
  if [[ -e "$vault" ]]; then
    if ! resolved_vault="$(realpath "$vault" 2>/dev/null)"; then
      log_error "graphify: failed to resolve vault path: ${vault}"
      exit "$OMES_EX_USAGE"
    fi
    if [[ ! -d "${resolved_vault}/.obsidian" ]] && [[ "$init_vault" -ne 1 ]]; then
      log_error "graphify: ${resolved_vault} does not look like an Obsidian vault (no .obsidian/ directory) - pass --init-vault to initialize one explicitly"
      exit "$OMES_EX_USAGE"
    fi
  else
    resolved_vault="$vault"
  fi

  local project_name="${OMES_GRAPHIFY_PROJECT_NAME:-}"
  local source_root
  if [[ "$(basename "$resolved_out")" == "graphify-out" ]]; then
    source_root="$(dirname "$resolved_out")"
  else
    source_root="$resolved_out"
  fi
  if [[ -z "$project_name" ]]; then
    project_name="$(basename "$source_root")"
  fi

  local subdir="${OMES_GRAPHIFY_VAULT_SUBDIR:-graphify/${project_name}}"
  local target_dir="${resolved_vault}/${subdir}"

  local graphify_version="unknown" extraction_mode="unknown"
  local prov_file="${resolved_out}/omes-provenance.json"
  if [[ -f "$prov_file" ]]; then
    graphify_version="$(_graphify_json_field "$(cat "$prov_file")" graphify_version)"
    extraction_mode="$(_graphify_json_field "$(cat "$prov_file")" mode)"
    [[ -n "$graphify_version" ]] || graphify_version="unknown"
    [[ -n "$extraction_mode" ]] || extraction_mode="unknown"
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    log_error "graphify: python3 is required for 'omes graphify export' but was not found"
    exit "$OMES_EX_ERROR"
  fi

  # Render path selection (docs/graphify.md §5.1/§5.2): prefer upstream
  # `graphify export obsidian` (staged into a throwaway temp dir, never
  # written directly into the vault) when the graphify CLI is on PATH and
  # actually produces its own generated-files manifest; otherwise fall
  # back to OMES's own from-scratch renderer. The staging step itself
  # never touches the vault, so it runs unconditionally, even under
  # --dry-run - only the eventual vault write is dry-run/confirmation
  # gated below.
  local render_mode="fallback"
  local staging_dir=""
  local -a plan_args=() write_args=()
  if command -v graphify >/dev/null 2>&1; then
    staging_dir="$(mktemp -d "${TMPDIR:-/tmp}/omes-graphify-export.XXXXXX")"
    if _graphify_stage_upstream_export "$graph_json" "$staging_dir"; then
      render_mode="upstream"
    else
      log_warn "graphify: export: upstream 'graphify export obsidian' did not produce a usable export; falling back to OMES's own renderer"
      rm -rf "$staging_dir"
      staging_dir=""
    fi
  fi

  if [[ "$render_mode" == "upstream" ]]; then
    log_info "graphify: export: rendering via upstream 'graphify export obsidian' (docs/graphify.md §5.2)"
    plan_args=(--staging-dir "$staging_dir" --target-dir "$target_dir" --graph-json "$graph_json" --graphify-version "$graphify_version" --extraction-mode "$extraction_mode")
    write_args=("${plan_args[@]}")
  else
    log_info "graphify: export: rendering via OMES's own graph.json renderer (docs/graphify.md §5.1)"
    plan_args=(--graph-json "$graph_json" --target-dir "$target_dir" --project-name "$project_name" --source-root "$source_root" --graphify-version "$graphify_version" --extraction-mode "$extraction_mode")
    write_args=("${plan_args[@]}")
  fi
  local plan_action="plan" write_action="write"
  if [[ "$render_mode" == "upstream" ]]; then
    plan_action="plan-upstream"
    write_action="write-upstream"
  fi

  local plan_json
  if ! plan_json="$(_graphify_export_py "$plan_action" "${plan_args[@]}")"; then
    [[ -n "$staging_dir" ]] && rm -rf "$staging_dir"
    log_error "graphify: export: failed to plan the Obsidian export"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand export)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_ERROR"
  fi

  local note_count
  note_count="$(_graphify_json_field "$plan_json" note_count)"
  log_info "graphify: export: vault: ${resolved_vault}"
  log_info "graphify: export: target: ${target_dir} (${note_count:-0} file(s) planned, render: ${render_mode})"

  if omes_dry_run; then
    log_info "[dry-run] would write generated notes under ${target_dir} (plan: ${plan_json})"
    [[ -n "$staging_dir" ]] && rm -rf "$staging_dir"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj \
        "$(json_kv command graphify)" \
        "$(json_kv subcommand export)" \
        "$(json_kv ok true --raw)" \
        "$(json_kv vault "$resolved_vault")" \
        "$(json_kv target_dir "$target_dir")" \
        "$(json_kv render_mode "$render_mode")" \
        "$(json_kv plan "$plan_json" --raw)" \
        "$(json_kv exit_code 0 --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_OK"
  fi

  if ! omes_confirm "Write generated Obsidian notes under ${target_dir}?"; then
    [[ -n "$staging_dir" ]] && rm -rf "$staging_dir"
    log_error "aborted: confirmation required (re-run with --yes to proceed non-interactively)"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand export)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_ERROR"
  fi

  if [[ ! -d "$resolved_vault" ]]; then
    if [[ "$init_vault" -ne 1 ]]; then
      [[ -n "$staging_dir" ]] && rm -rf "$staging_dir"
      log_error "graphify: vault does not exist: ${resolved_vault}"
      exit "$OMES_EX_USAGE"
    fi
    log_info "graphify: --init-vault: creating a new vault at ${resolved_vault} (a minimal .obsidian/ marker directory is created; OMES never installs, starts, or manages Obsidian itself)"
    mkdir -p "${resolved_vault}/.obsidian"
  fi

  backup_begin "graphify-export" "pre-export: ${target_dir}" >/dev/null
  if [[ -e "$target_dir" ]]; then
    backup_path "$target_dir"
  fi

  local write_json
  if ! write_json="$(_graphify_export_py "$write_action" "${write_args[@]}")"; then
    backup_finish >/dev/null
    [[ -n "$staging_dir" ]] && rm -rf "$staging_dir"
    log_error "graphify: export: failed to write the Obsidian export"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand export)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_ERROR"
  fi
  local backup_dir
  backup_dir="$(backup_finish)"
  [[ -n "$staging_dir" ]] && rm -rf "$staging_dir"

  local conflicts
  conflicts="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(len(d.get("conflicts") or []))' "$write_json" 2>/dev/null || printf '0')"
  if [[ "$conflicts" != "0" ]]; then
    log_warn "graphify: export: ${conflicts} existing note(s) lacked the omes_generated marker (or, for non-Markdown files, were not previously OMES-owned) and were NOT overwritten (see JSON output for paths)"
  fi
  log_info "graphify: export: wrote notes under ${target_dir} (backup: ${backup_dir}, render: ${render_mode})"

  if [[ "$OMES_JSON" == "1" ]]; then
    json_obj \
      "$(json_kv command graphify)" \
      "$(json_kv subcommand export)" \
      "$(json_kv ok true --raw)" \
      "$(json_kv vault "$resolved_vault")" \
      "$(json_kv target_dir "$target_dir")" \
      "$(json_kv backup "$backup_dir")" \
      "$(json_kv render_mode "$render_mode")" \
      "$(json_kv result "$write_json" --raw)" \
      "$(json_kv exit_code 0 --raw)"
    printf '\n'
  fi
  exit "$OMES_EX_OK"
}

# _graphify_sync_resolve <path>
# Resolves <path> the same way `omes graphify run`/`export` do: must
# exist, canonicalized via realpath, refused if it is (or is nested
# inside) a graphify-out/ directory. Prints "<resolved>\n<out_dir>\n" on
# success, or "ERROR:<message>\n" on failure. NEVER calls `exit` itself -
# this is always invoked via process substitution (`< <(...)`), which
# runs in a subshell; an `exit` there only ends that subshell, and the
# reading `read` in the caller's shell would then fail with an unrelated,
# wrong exit code once the subshell's output stream closes early. The
# caller checks for the "ERROR:" prefix and exits directly, in its own
# shell, instead.
_graphify_sync_resolve() {
  local path="$1"

  if [[ ! -e "$path" ]]; then
    printf 'ERROR:graphify: path does not exist: %s\n\n' "$path"
    return 0
  fi
  local resolved
  if ! resolved="$(realpath "$path" 2>/dev/null)"; then
    printf 'ERROR:graphify: failed to resolve path: %s\n\n' "$path"
    return 0
  fi
  if [[ "$(basename "$resolved")" == "graphify-out" ]] || [[ "/${resolved}/" == */graphify-out/* ]]; then
    printf 'ERROR:graphify: refusing to operate on a path inside a graphify-out/ directory: %s\n\n' "$resolved"
    return 0
  fi

  local out_dir
  if [[ -d "$resolved" ]]; then
    out_dir="${resolved}/graphify-out"
  else
    out_dir="$(dirname "$resolved")/graphify-out"
  fi

  printf '%s\n%s\n' "$resolved" "$out_dir"
}

# _graphify_sync_check_resolve_error <first-line>
# Exits OMES_EX_USAGE with an actionable message when
# _graphify_sync_resolve's first printed line starts with "ERROR:"; a
# no-op otherwise. Always called directly (never via command/process
# substitution) so its `exit` actually ends the real process.
_graphify_sync_check_resolve_error() {
  case "$1" in
    ERROR:*)
      log_error "${1#ERROR:}"
      exit "$OMES_EX_USAGE"
      ;;
  esac
}

# _graphify_sync_resolve_vault <resolved-source-path> <allow-nested>
# Resolves --vault/OBSIDIAN_VAULT_PATH (optional for sync/status, used
# only for loop avoidance - docs/graphify.md §6.3). Prints the resolved
# vault path (or nothing if none given/it does not yet exist), or
# "REFUSED:<path>" when the vault resolves inside the source tree and
# <allow-nested> is not "1" - the caller decides how to report/exit on
# that prefix. This function ALWAYS returns 0 itself (never `exit`s and
# never returns non-zero) so that calling it via command substitution
# under `set -e` (bin/omes's strict mode) never trips errexit on its own
# account; only the caller's own explicit `exit` after inspecting the
# printed value ends the process.
_graphify_sync_resolve_vault() {
  local resolved_source="$1" allow_nested="$2"
  local vault="${OMES_GRAPHIFY_SYNC_VAULT_ARG:-${OBSIDIAN_VAULT_PATH:-}}"
  [[ -n "$vault" ]] || return 0
  [[ -e "$vault" ]] || return 0

  local resolved_vault
  resolved_vault="$(realpath "$vault" 2>/dev/null)" || return 0

  if [[ "/${resolved_vault}/" == "/${resolved_source}/"* ]] || [[ "$resolved_vault" == "$resolved_source" ]]; then
    if [[ "$allow_nested" != "1" ]]; then
      printf 'REFUSED:%s\n' "$resolved_vault"
      return 0
    fi
  fi

  printf '%s\n' "$resolved_vault"
}

# _graphify_sync_check_vault_refusal <vault-resolve-result> <resolved-source>
# Exits OMES_EX_USAGE with an actionable message when
# _graphify_sync_resolve_vault's output starts with "REFUSED:"; a no-op
# otherwise. Split out from _graphify_sync_resolve_vault itself so the
# `exit` happens directly in the caller's own shell (never inside a
# command-substitution subshell - see that function's own comment).
_graphify_sync_check_vault_refusal() {
  local result="$1" resolved_source="$2"
  case "$result" in
    REFUSED:*)
      log_error "graphify: the vault (${result#REFUSED:}) is nested inside the source path (${resolved_source}) - this would let a future export feed back into sync's own change detection; pass --allow-nested-vault to proceed anyway (the vault path is still always excluded from the scan)"
      exit "$OMES_EX_USAGE"
      ;;
  esac
}

# _graphify_sync_json_valid <path>
# True when <path> parses as JSON - used as a minimal post-run sanity
# check (docs/graphify.md §6.4's "recovery from interrupted runs") after
# `graphify extract`/`update` return 0, since a killed/interrupted
# process could still have left a truncated graph.json behind.
_graphify_sync_json_valid() {
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$1" >/dev/null 2>&1
}

_graphify_cmd_status() {
  local path="" vault=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --vault)
        [[ $# -ge 2 ]] || {
          log_error "graphify: --vault requires an argument"; exit "$OMES_EX_USAGE"
        }
        vault="$2"
        shift 2
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
    log_error "graphify: usage: omes graphify status <path> [--vault <path>] [--json]"
    exit "$OMES_EX_USAGE"
  fi

  local resolved out_dir
  {
    read -r resolved
    read -r out_dir
  } < <(_graphify_sync_resolve "$path")
  _graphify_sync_check_resolve_error "$resolved"

  local resolved_vault=""
  [[ -n "$vault" ]] && OMES_GRAPHIFY_SYNC_VAULT_ARG="$vault"
  resolved_vault="$(_graphify_sync_resolve_vault "$resolved" 1)"
  unset OMES_GRAPHIFY_SYNC_VAULT_ARG

  local manifest="${out_dir}/omes-sync.json"
  local -a exclude_args=(--exclude "$out_dir")
  [[ -n "$resolved_vault" ]] && exclude_args+=(--exclude "$resolved_vault")

  if ! command -v python3 >/dev/null 2>&1; then
    log_error "graphify: python3 is required for 'omes graphify status' but was not found"
    exit "$OMES_EX_ERROR"
  fi

  local status_json
  if ! status_json="$(_graphify_export_py status --path "$resolved" --manifest "$manifest" "${exclude_args[@]}" --max-file-mb "${OMES_GRAPHIFY_MAX_FILE_MB:-5}")"; then
    log_error "graphify: status: scan failed"
    exit "$OMES_EX_ERROR"
  fi

  local first_run changed status_word
  first_run="$(_graphify_json_field "$status_json" first_run)"
  changed="$(_graphify_json_field "$status_json" changed)"
  if [[ "$first_run" == "True" ]]; then
    status_word="no_manifest"
  elif [[ "$changed" == "True" ]]; then
    status_word="stale"
  else
    status_word="up_to_date"
  fi

  log_info "graphify: status: ${status_word} (${resolved})"

  if [[ "$OMES_JSON" == "1" ]]; then
    json_obj \
      "$(json_kv command graphify)" \
      "$(json_kv subcommand status)" \
      "$(json_kv ok true --raw)" \
      "$(json_kv path "$resolved")" \
      "$(json_kv status "$status_word")" \
      "$(json_kv detail "$status_json" --raw)" \
      "$(json_kv exit_code 0 --raw)"
    printf '\n'
  fi
  exit "$OMES_EX_OK"
}

_graphify_cmd_sync() {
  local path="" vault="" min_interval=0 allow_nested=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --vault)
        [[ $# -ge 2 ]] || {
          log_error "graphify: --vault requires an argument"; exit "$OMES_EX_USAGE"
        }
        vault="$2"
        shift 2
        ;;
      --min-interval)
        [[ $# -ge 2 ]] || {
          log_error "graphify: --min-interval requires an argument"; exit "$OMES_EX_USAGE"
        }
        min_interval="$2"
        shift 2
        ;;
      --allow-nested-vault)
        allow_nested=1
        shift
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
    log_error "graphify: usage: omes graphify sync <path> [--vault <path>] [--min-interval <sec>] [--allow-nested-vault] [--dry-run] [--yes] [--json]"
    exit "$OMES_EX_USAGE"
  fi
  if ! [[ "$min_interval" =~ ^[0-9]+$ ]]; then
    log_error "graphify: --min-interval must be a non-negative integer (seconds)"
    exit "$OMES_EX_USAGE"
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    log_error "graphify: python3 is required for 'omes graphify sync' but was not found"
    exit "$OMES_EX_ERROR"
  fi
  module_load graphify
  if ! command -v graphify >/dev/null 2>&1; then
    log_error "graphify: the graphify CLI is not installed - run 'omes install --module graphify' first"
    exit "$OMES_EX_USAGE"
  fi

  local resolved out_dir
  {
    read -r resolved
    read -r out_dir
  } < <(_graphify_sync_resolve "$path")
  _graphify_sync_check_resolve_error "$resolved"

  [[ -n "$vault" ]] && OMES_GRAPHIFY_SYNC_VAULT_ARG="$vault"
  local vault_result resolved_vault=""
  vault_result="$(_graphify_sync_resolve_vault "$resolved" "$allow_nested")"
  unset OMES_GRAPHIFY_SYNC_VAULT_ARG
  _graphify_sync_check_vault_refusal "$vault_result" "$resolved"
  resolved_vault="$vault_result"

  local manifest="${out_dir}/omes-sync.json"
  local -a exclude_args=(--exclude "$out_dir")
  [[ -n "$resolved_vault" ]] && exclude_args+=(--exclude "$resolved_vault")

  # Debounce (docs/graphify.md §6.2): checked BEFORE the (potentially
  # expensive) tree scan, using the manifest's own last_run_at field, so
  # a debounced call costs nothing beyond reading one small JSON file.
  if [[ "$min_interval" -gt 0 ]] && [[ -f "$manifest" ]]; then
    local last_run_at now_epoch last_epoch elapsed
    last_run_at="$(_graphify_json_field "$(cat "$manifest")" last_run_at)"
    if [[ -n "$last_run_at" ]]; then
      last_epoch="$(python3 -c 'import sys,datetime; print(int(datetime.datetime.strptime(sys.argv[1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()))' "$last_run_at" 2>/dev/null || printf '0')"
      now_epoch="$(date -u +%s)"
      elapsed=$((now_epoch - last_epoch))
      if [[ "$elapsed" -lt "$min_interval" ]]; then
        log_info "graphify: sync: debounced (${elapsed}s since last run, --min-interval ${min_interval}s)"
        if [[ "$OMES_JSON" == "1" ]]; then
          json_obj \
            "$(json_kv command graphify)" \
            "$(json_kv subcommand sync)" \
            "$(json_kv ok true --raw)" \
            "$(json_kv action skipped_debounced)" \
            "$(json_kv path "$resolved")" \
            "$(json_kv exit_code 0 --raw)"
          printf '\n'
        fi
        exit "$OMES_EX_OK"
      fi
    fi
  fi

  local status_json
  if ! status_json="$(_graphify_export_py status --path "$resolved" --manifest "$manifest" "${exclude_args[@]}" --max-file-mb "${OMES_GRAPHIFY_MAX_FILE_MB:-5}")"; then
    log_error "graphify: sync: scan failed"
    exit "$OMES_EX_ERROR"
  fi

  local first_run changed
  first_run="$(_graphify_json_field "$status_json" first_run)"
  changed="$(_graphify_json_field "$status_json" changed)"

  if [[ "$changed" != "True" ]]; then
    log_info "graphify: sync: up to date, nothing to do (${resolved})"
    if omes_dry_run; then
      : # nothing to preview either
    else
      # Still advance last_run_at so --min-interval throttles repeated
      # no-op calls, without re-invoking graphify at all.
      _graphify_export_py scan --path "$resolved" --manifest "$manifest" "${exclude_args[@]}" --max-file-mb "${OMES_GRAPHIFY_MAX_FILE_MB:-5}" --write >/dev/null || true
    fi
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj \
        "$(json_kv command graphify)" \
        "$(json_kv subcommand sync)" \
        "$(json_kv ok true --raw)" \
        "$(json_kv action none)" \
        "$(json_kv path "$resolved")" \
        "$(json_kv detail "$status_json" --raw)" \
        "$(json_kv exit_code 0 --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_OK"
  fi

  local action="update"
  [[ "$first_run" == "True" ]] && action="extract"

  if omes_dry_run; then
    log_info "[dry-run] would run: graphify ${action} ${resolved} (then update ${manifest})"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj \
        "$(json_kv command graphify)" \
        "$(json_kv subcommand sync)" \
        "$(json_kv ok true --raw)" \
        "$(json_kv action "$action")" \
        "$(json_kv path "$resolved")" \
        "$(json_kv detail "$status_json" --raw)" \
        "$(json_kv exit_code 0 --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_OK"
  fi

  if ! omes_confirm "Run graphify ${action} on ${resolved}? (code-only, no LLM/provider credential involved)"; then
    log_error "aborted: confirmation required (re-run with --yes to proceed non-interactively)"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand sync)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_ERROR"
  fi

  local run_failed=0
  if [[ "$action" == "extract" ]]; then
    # First run: no existing graphify-out/ to update in place, so use
    # `--out <temp-dir>` (writes <temp-dir>/graphify-out/, per graphify's
    # own --help) and an atomic rename - docs/graphify.md §6.4's "temp
    # output dir + atomic rename" recovery mechanism for a fresh sync.
    local tmp_out
    tmp_out="$(mktemp -d "${TMPDIR:-/tmp}/omes-graphify-sync.XXXXXX")"
    if omes_run graphify extract "$resolved" --code-only --out "$tmp_out"; then
      if [[ -f "${tmp_out}/graphify-out/graph.json" ]] && _graphify_sync_json_valid "${tmp_out}/graphify-out/graph.json"; then
        mv "${tmp_out}/graphify-out" "$out_dir"
      else
        log_error "graphify: sync: extract reported success but graph.json is missing or invalid; nothing was moved into place"
        run_failed=1
      fi
    else
      run_failed=1
    fi
    rm -rf "$tmp_out"
  else
    # Subsequent run: `graphify update` has no --out option and mutates
    # <path>/graphify-out/ in place, so OMES takes its own backup first
    # (docs/graphify.md §6.4) and restores it if the run fails or leaves
    # a corrupt graph.json - the same recovery-from-interrupted-runs
    # guarantee as the first-run path, using lib/omes/backup.sh instead
    # of a redundant second temp-dir mechanism.
    backup_begin "graphify-sync" "pre-sync: ${out_dir}" >/dev/null
    # Capture the session directory (and its basename/timestamp) BEFORE
    # calling backup_finish, and always call backup_finish as a plain
    # statement rather than via command substitution: backup_finish's
    # `unset OMES_CURRENT_BACKUP_DIR` (lib/omes/backup.sh) only takes
    # effect in the shell it actually runs in, and command substitution
    # runs in a subshell - `ts="$(backup_finish)"` would leave
    # OMES_CURRENT_BACKUP_DIR still set in THIS shell, which would then
    # make restore_backup's own pre-restore-backup step reuse this same
    # active session instead of opening its own, corrupting/looping over
    # the very manifest restore_backup is reading. Both the OMES_ROOT
    # backup/restore helpers themselves are outside this issue's file
    # scope, so this sequencing is worked around here instead.
    local backup_session_dir="$OMES_CURRENT_BACKUP_DIR"
    local backup_ts
    backup_ts="$(basename "$backup_session_dir")"
    backup_path "$out_dir"
    if omes_run graphify update "$resolved"; then
      if [[ -f "${out_dir}/graph.json" ]] && _graphify_sync_json_valid "${out_dir}/graph.json"; then
        backup_finish >/dev/null
      else
        log_error "graphify: sync: update reported success but graph.json is missing or invalid; restoring the pre-sync backup"
        backup_finish >/dev/null
        restore_backup "$backup_ts" || true
        run_failed=1
      fi
    else
      backup_finish >/dev/null
      restore_backup "$backup_ts" || true
      run_failed=1
    fi
  fi

  if [[ "$run_failed" -eq 1 ]]; then
    log_error "graphify: sync: graphify ${action} failed; source tree left as-is, graphify-out/ recovered to its pre-sync state"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand sync)" "$(json_kv ok false --raw)" "$(json_kv action "$action")" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_ERROR"
  fi

  _graphify_export_py scan --path "$resolved" --manifest "$manifest" "${exclude_args[@]}" --max-file-mb "${OMES_GRAPHIFY_MAX_FILE_MB:-5}" --write >/dev/null || true
  log_info "graphify: sync: ran graphify ${action} and refreshed the change-detection manifest (${manifest})"

  if [[ "$OMES_JSON" == "1" ]]; then
    json_obj \
      "$(json_kv command graphify)" \
      "$(json_kv subcommand sync)" \
      "$(json_kv ok true --raw)" \
      "$(json_kv action "$action")" \
      "$(json_kv path "$resolved")" \
      "$(json_kv out_dir "$out_dir")" \
      "$(json_kv exit_code 0 --raw)"
    printf '\n'
  fi
  exit "$OMES_EX_OK"
}

# _graphify_cmd_init_ignore <path> [--yes] [--dry-run] [--json]
# Writes/updates <path>/.graphifyignore from the bundled template
# (modules/graphify/templates/.graphifyignore) and ensures <path>/.gitignore
# ignores graphify-out/ and the default vault subdirectory (issue #55,
# docs/graphify-privacy.md). Idempotent - re-running when both marker
# blocks are already present is a no-op. Backs up both files before any
# write, exactly like every other OMES mutation.
GRAPHIFYIGNORE_MARKER_BEGIN="# BEGIN OMES graphify ignore patterns (omes graphify init-ignore, issue #55)"
GITIGNORE_MARKER_BEGIN="# BEGIN OMES graphify .gitignore entries (omes graphify init-ignore)"
GITIGNORE_MARKER_END="# END OMES graphify .gitignore entries"

_graphify_cmd_init_ignore() {
  local path=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
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
    log_error "graphify: usage: omes graphify init-ignore <path> [--dry-run] [--yes] [--json]"
    exit "$OMES_EX_USAGE"
  fi

  local resolved out_dir
  {
    read -r resolved
    read -r out_dir
  } < <(_graphify_sync_resolve "$path")
  _graphify_sync_check_resolve_error "$resolved"

  local ignore_file="${resolved}/.graphifyignore"
  local gitignore_file="${resolved}/.gitignore"
  local template="${OMES_ROOT}/modules/graphify/templates/.graphifyignore"

  local need_ignore=1 need_gitignore=1
  if [[ -f "$ignore_file" ]] && grep -qF "$GRAPHIFYIGNORE_MARKER_BEGIN" "$ignore_file" 2>/dev/null; then
    need_ignore=0
  fi
  if [[ -f "$gitignore_file" ]] && grep -qF "$GITIGNORE_MARKER_BEGIN" "$gitignore_file" 2>/dev/null; then
    need_gitignore=0
  fi

  if [[ "$need_ignore" -eq 0 ]] && [[ "$need_gitignore" -eq 0 ]]; then
    log_info "graphify: init-ignore: already initialized (idempotent no-op): ${ignore_file}, ${gitignore_file}"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj \
        "$(json_kv command graphify)" \
        "$(json_kv subcommand init-ignore)" \
        "$(json_kv ok true --raw)" \
        "$(json_kv action none)" \
        "$(json_kv exit_code 0 --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_OK"
  fi

  if omes_dry_run; then
    log_info "[dry-run] would write/update ${ignore_file}${need_gitignore:+ and ${gitignore_file}}"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj \
        "$(json_kv command graphify)" \
        "$(json_kv subcommand init-ignore)" \
        "$(json_kv ok true --raw)" \
        "$(json_kv action would-write)" \
        "$(json_kv exit_code 0 --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_OK"
  fi

  if ! omes_confirm "Write/update ${ignore_file} and ensure graphify-out/ + the vault subdir are ignored in ${gitignore_file}?"; then
    log_error "aborted: confirmation required (re-run with --yes to proceed non-interactively)"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand init-ignore)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_ERROR"
  fi

  backup_begin "graphify-init-ignore" "pre-init-ignore: ${resolved}" >/dev/null
  [[ -f "$ignore_file" ]] && backup_path "$ignore_file"
  [[ -f "$gitignore_file" ]] && backup_path "$gitignore_file"

  if [[ "$need_ignore" -eq 1 ]]; then
    if [[ -f "$ignore_file" ]]; then
      {
        printf '\n'
        cat "$template"
      } >>"$ignore_file"
    else
      cp "$template" "$ignore_file"
    fi
    log_info "graphify: init-ignore: wrote ${ignore_file}"
  fi

  if [[ "$need_gitignore" -eq 1 ]]; then
    {
      printf '\n%s\n' "$GITIGNORE_MARKER_BEGIN"
      printf 'graphify-out/\n'
      printf 'graphify/\n'
      printf '%s\n' "$GITIGNORE_MARKER_END"
    } >>"$gitignore_file"
    log_info "graphify: init-ignore: ensured graphify-out/ and graphify/ are ignored in ${gitignore_file}"
  fi

  backup_finish >/dev/null

  if [[ "$OMES_JSON" == "1" ]]; then
    json_obj \
      "$(json_kv command graphify)" \
      "$(json_kv subcommand init-ignore)" \
      "$(json_kv ok true --raw)" \
      "$(json_kv action wrote)" \
      "$(json_kv graphifyignore "$ignore_file")" \
      "$(json_kv gitignore "$gitignore_file")" \
      "$(json_kv exit_code 0 --raw)"
    printf '\n'
  fi
  exit "$OMES_EX_OK"
}

# _graphify_cmd_purge <path> [--vault <path>] [--dry-run] [--yes] [--json]
# Removes ONLY OMES-generated artifacts by marker/ownership (issue #55,
# docs/graphify-privacy.md "deletion and re-index procedure"): the entire
# <graphify-out>/ directory (100% machine-generated - never contains
# user-authored content), plus, when --vault/OBSIDIAN_VAULT_PATH is given,
# every file under the exported vault subdirectory that carries the
# omes_generated marker (Markdown) or is listed in that export's own
# .omes_export_manifest.json bookkeeping file (everything else) - see
# lib/omes/py/graphify/obsidian.py's plan_purge_export/purge_export.
# Never removes a user-authored note or any file it does not recognize as
# its own.
_graphify_cmd_purge() {
  local path="" vault=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --vault)
        [[ $# -ge 2 ]] || {
          log_error "graphify: --vault requires an argument"; exit "$OMES_EX_USAGE"
        }
        vault="$2"
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
    log_error "graphify: usage: omes graphify purge <path> [--vault <path>] [--dry-run] [--yes] [--json]"
    exit "$OMES_EX_USAGE"
  fi

  local resolved out_dir
  {
    read -r resolved
    read -r out_dir
  } < <(_graphify_sync_resolve "$path")
  _graphify_sync_check_resolve_error "$resolved"

  local project_name
  project_name="${OMES_GRAPHIFY_PROJECT_NAME:-$(basename "$resolved")}"
  local vault_arg="${vault:-${OBSIDIAN_VAULT_PATH:-}}"
  local target_dir=""
  if [[ -n "$vault_arg" ]] && [[ -e "$vault_arg" ]]; then
    local resolved_vault
    if resolved_vault="$(realpath "$vault_arg" 2>/dev/null)"; then
      target_dir="${resolved_vault}/${OMES_GRAPHIFY_VAULT_SUBDIR:-graphify/${project_name}}"
    fi
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    log_error "graphify: python3 is required for 'omes graphify purge' but was not found"
    exit "$OMES_EX_ERROR"
  fi

  local export_removable="[]" export_skipped="[]"
  if [[ -n "$target_dir" ]] && [[ -d "$target_dir" ]]; then
    local plan_json
    if plan_json="$(_graphify_export_py purge-plan --target-dir "$target_dir")"; then
      export_removable="$(python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["removable"]))' "$plan_json" 2>/dev/null || printf '[]')"
      export_skipped="$(python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["skipped"]))' "$plan_json" 2>/dev/null || printf '[]')"
    fi
  fi

  log_info "graphify: purge: graphify-out: ${out_dir} ($([[ -d "$out_dir" ]] && printf 'present' || printf 'absent'))"
  [[ -n "$target_dir" ]] && log_info "graphify: purge: vault export subdir: ${target_dir}"

  if omes_dry_run; then
    log_info "[dry-run] would remove ${out_dir} (if present) and, under ${target_dir:-<no vault given>}, only OMES-owned files: ${export_removable}"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj \
        "$(json_kv command graphify)" \
        "$(json_kv subcommand purge)" \
        "$(json_kv ok true --raw)" \
        "$(json_kv graphify_out "$out_dir")" \
        "$(json_kv target_dir "$target_dir")" \
        "$(json_kv export_removable "$export_removable" --raw)" \
        "$(json_kv export_skipped "$export_skipped" --raw)" \
        "$(json_kv exit_code 0 --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_OK"
  fi

  if [[ ! -d "$out_dir" ]] && [[ "$export_removable" == "[]" ]]; then
    log_info "graphify: purge: nothing to remove"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand purge)" "$(json_kv ok true --raw)" "$(json_kv action none)" "$(json_kv exit_code 0 --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_OK"
  fi

  if ! omes_confirm "Remove ${out_dir} (if present) and OMES-generated notes under ${target_dir:-<no vault given>}?"; then
    log_error "aborted: confirmation required (re-run with --yes to proceed non-interactively)"
    if [[ "$OMES_JSON" == "1" ]]; then
      json_obj "$(json_kv command graphify)" "$(json_kv subcommand purge)" "$(json_kv ok false --raw)" "$(json_kv exit_code "$OMES_EX_ERROR" --raw)"
      printf '\n'
    fi
    exit "$OMES_EX_ERROR"
  fi

  backup_begin "graphify-purge" "pre-purge: ${resolved}" >/dev/null
  [[ -d "$out_dir" ]] && backup_path "$out_dir"
  [[ -n "$target_dir" ]] && [[ -d "$target_dir" ]] && backup_path "$target_dir"
  local backup_dir
  backup_dir="$(backup_finish)"

  if [[ -d "$out_dir" ]]; then
    rm -rf "$out_dir"
    log_warn "graphify: purge: removed ${out_dir}"
  fi

  local removed_json="[]" skipped_json="[]"
  if [[ -n "$target_dir" ]] && [[ -d "$target_dir" ]]; then
    local purge_json
    if purge_json="$(_graphify_export_py purge --target-dir "$target_dir")"; then
      removed_json="$(python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["removed"]))' "$purge_json" 2>/dev/null || printf '[]')"
      skipped_json="$(python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["skipped"]))' "$purge_json" 2>/dev/null || printf '[]')"
      log_warn "graphify: purge: removed OMES-generated files under ${target_dir} (backup: ${backup_dir})"
      if [[ "$skipped_json" != "[]" ]]; then
        log_warn "graphify: purge: kept non-OMES-generated file(s) under ${target_dir}: ${skipped_json}"
      fi
    fi
  fi

  if [[ "$OMES_JSON" == "1" ]]; then
    json_obj \
      "$(json_kv command graphify)" \
      "$(json_kv subcommand purge)" \
      "$(json_kv ok true --raw)" \
      "$(json_kv backup "$backup_dir")" \
      "$(json_kv graphify_out_removed "$([[ -d "$out_dir" ]] && printf 'false' || printf 'true')" --raw)" \
      "$(json_kv export_removed "$removed_json" --raw)" \
      "$(json_kv export_skipped "$skipped_json" --raw)" \
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
      _graphify_cmd_run "$@"
      ;;
    extract)
      _graphify_cmd_extract "$@"
      ;;
    query)
      _graphify_cmd_query "$@"
      ;;
    hook)
      _graphify_cmd_hook "$@"
      ;;
    skill)
      _graphify_cmd_skill "$@"
      ;;
    mcp)
      _graphify_cmd_mcp "$@"
      ;;
    export)
      _graphify_cmd_export "$@"
      ;;
    sync)
      _graphify_cmd_sync "$@"
      ;;
    status)
      _graphify_cmd_status "$@"
      ;;
    init-ignore)
      _graphify_cmd_init_ignore "$@"
      ;;
    purge)
      _graphify_cmd_purge "$@"
      ;;
    *)
      log_error "graphify: usage: omes graphify {update|uninstall|run|extract|query|hook|skill|mcp|export|sync|status|init-ignore|purge} [args...]"
      exit "$OMES_EX_USAGE"
      ;;
  esac
}
