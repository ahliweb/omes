#!/usr/bin/env bash
# shellcheck shell=bash
# lib/omes/module.sh - module loader/runner: profiles, topo-sort, check-then-apply.
#
# Meant to be sourced after lib/omes/core.sh, lib/omes/log.sh,
# lib/omes/state.sh and lib/omes/backup.sh.

if [[ -n "${OMES_MODULE_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317  # unreachable only when sourced; the `exit`
  # branch is the (correct) fallback for accidental direct execution.
  return 0 2>/dev/null || exit 0
fi
OMES_MODULE_SH_LOADED=1

# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------

# module_load <name>
# Sources modules/<name>/module.sh, resetting any metadata/functions left
# over from a previously loaded module first, then validates that the
# required metadata (MODULE_NAME, MODULE_DESCRIPTION, MODULE_SCOPE) and
# functions (module_check/apply/verify/rollback) are present. Dies with a
# usage error (exit 2) on any contract violation.
module_load() {
  local name="$1"
  local file="${OMES_ROOT}/modules/${name}/module.sh"

  if [[ ! -r "$file" ]]; then
    omes_die "$OMES_EX_USAGE" "module not found: ${name} (${file})"
  fi

  unset MODULE_NAME MODULE_DESCRIPTION MODULE_SCOPE MODULE_REQUIRES MODULE_PROFILES 2>/dev/null || true
  unset -f module_check module_apply module_verify module_rollback 2>/dev/null || true

  # shellcheck disable=SC1090
  source "$file"

  local var
  for var in MODULE_NAME MODULE_DESCRIPTION MODULE_SCOPE; do
    if [[ -z "${!var:-}" ]]; then
      omes_die "$OMES_EX_USAGE" "module '${name}' is missing required metadata: ${var}"
    fi
  done

  if [[ "$MODULE_NAME" != "$name" ]]; then
    omes_die "$OMES_EX_USAGE" "module '${name}' declares MODULE_NAME='${MODULE_NAME}' (expected '${name}')"
  fi

  case "$MODULE_SCOPE" in
    root | user) ;;
    *)
      omes_die "$OMES_EX_USAGE" "module '${name}' has invalid MODULE_SCOPE: ${MODULE_SCOPE}"
      ;;
  esac

  local func
  for func in module_check module_apply module_verify module_rollback; do
    if ! declare -F "$func" >/dev/null 2>&1; then
      omes_die "$OMES_EX_USAGE" "module '${name}' is missing required function: ${func}"
    fi
  done

  if ! declare -p MODULE_REQUIRES >/dev/null 2>&1; then
    MODULE_REQUIRES=()
  fi
  if ! declare -p MODULE_PROFILES >/dev/null 2>&1; then
    MODULE_PROFILES=()
  fi
}

# module_discover
# Prints the names of all modules under modules/ that have a module.sh
# entry point, sorted.
module_discover() {
  local dir="${OMES_ROOT}/modules"
  [[ -d "$dir" ]] || return 0
  local d
  for d in "$dir"/*/; do
    [[ -f "${d}module.sh" ]] || continue
    basename "$d"
  done | sort
}

# ---------------------------------------------------------------------------
# Managed paths / backups
# ---------------------------------------------------------------------------

# omes_manage_path <path>
# Registers <path> as managed by the module currently being applied
# (appended to OMES_MANAGED_PATHS, later persisted to
# module.<name>.managed_paths) and, when a backup session is active,
# immediately backs it up before the module mutates it.
omes_manage_path() {
  local path="$1"
  OMES_MANAGED_PATHS+=("$path")
  if [[ -n "${OMES_CURRENT_BACKUP_DIR:-}" ]] && ! omes_dry_run; then
    backup_path "$path"
  fi
}

# ---------------------------------------------------------------------------
# Profiles: parsing + topological ordering
# ---------------------------------------------------------------------------

# profile_load <name>
# Reads profiles/<name>.profile (one module per line, blank lines and
# `#`-comments ignored), resolves MODULE_REQUIRES ordering via
# module_topo_sort, and prints the resulting module names in apply order.
profile_load() {
  local profile="$1"
  local file="${OMES_ROOT}/profiles/${profile}.profile"

  if [[ ! -r "$file" ]]; then
    omes_die "$OMES_EX_USAGE" "profile not found: ${profile} (${file})"
  fi

  local -a raw=()
  local line trimmed
  while IFS= read -r line || [[ -n "$line" ]]; do
    trimmed="${line%%#*}"
    trimmed="${trimmed#"${trimmed%%[![:space:]]*}"}"
    trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
    [[ -z "$trimmed" ]] && continue
    raw+=("$trimmed")
  done <"$file"

  module_resolve_order "${raw[@]}"
}

# module_resolve_order <name> [name...]
# Loads each module's metadata (populating the global MODULE_REQUIRES_OF
# map from MODULE_REQUIRES) and prints the resulting topological order.
# Used by profile_load, and directly for ad-hoc --module selections so
# MODULE_REQUIRES ordering applies there too.
module_resolve_order() {
  declare -gA MODULE_REQUIRES_OF=()
  local m
  for m in "$@"; do
    module_load "$m"
    MODULE_REQUIRES_OF["$m"]="${MODULE_REQUIRES[*]:-}"
  done

  module_topo_sort "$@"
}

# _module_topo_visit <name>
# Internal DFS step for module_topo_sort. Relies on the caller's local
# `visited`, `onstack` and `order` arrays being visible via bash's dynamic
# scoping, and on the global MODULE_REQUIRES_OF map.
_module_topo_visit() {
  local n="$1"

  if [[ "${visited[$n]:-0}" == "1" ]]; then
    return 0
  fi
  if [[ "${onstack[$n]:-0}" == "1" ]]; then
    omes_die "$OMES_EX_USAGE" "cycle detected in module dependencies involving: ${n}"
  fi

  onstack[$n]=1
  local reqs=""
  if declare -p MODULE_REQUIRES_OF >/dev/null 2>&1; then
    reqs="${MODULE_REQUIRES_OF[$n]:-}"
  fi
  local dep
  for dep in $reqs; do
    [[ -z "$dep" ]] && continue
    _module_topo_visit "$dep"
  done
  onstack[$n]=0
  visited[$n]=1
  order+=("$n")
}

# module_topo_sort <name> [name...]
# Prints <name...> ordered so that every module's MODULE_REQUIRES (read
# from the global MODULE_REQUIRES_OF map, space-separated) come before it.
# Dies with a usage error (exit 2) if a dependency cycle is found.
module_topo_sort() {
  local -a input=("$@")
  local -A visited=()
  local -A onstack=()
  local -a order=()

  local m
  for m in "${input[@]}"; do
    _module_topo_visit "$m"
  done

  printf '%s\n' "${order[@]}"
}

# ---------------------------------------------------------------------------
# Scope helpers
# ---------------------------------------------------------------------------

# module_current_scope
# Prints "root" or "user" depending on the current effective privilege.
module_current_scope() {
  if omes_is_root; then
    printf 'root\n'
  else
    printf 'user\n'
  fi
}

# module_filter_by_scope <name> [name...]
# Prints only the modules matching the current privilege scope.
module_filter_by_scope() {
  local current
  current="$(module_current_scope)"
  local m
  for m in "$@"; do
    module_load "$m"
    if [[ "$MODULE_SCOPE" == "$current" ]]; then
      printf '%s\n' "$m"
    fi
  done
}

# module_other_scope_modules <name> [name...]
# Prints modules that do NOT match the current privilege scope (used to
# tell the operator what to run separately, e.g. as the other user).
module_other_scope_modules() {
  local current
  current="$(module_current_scope)"
  local m
  for m in "$@"; do
    module_load "$m"
    if [[ "$MODULE_SCOPE" != "$current" ]]; then
      printf '%s\n' "$m"
    fi
  done
}

# ---------------------------------------------------------------------------
# Runner: check-all-then-apply
# ---------------------------------------------------------------------------

# run_checks <name> [name...]
# Runs module_check for every module (NO mutation). Populates the parallel
# arrays OMES_CHECK_NAMES / OMES_CHECK_OK / OMES_CHECK_DETAILS for the
# caller to render, and returns 0 only if every module's checks (including
# scope enforcement) passed.
run_checks() {
  OMES_CHECK_NAMES=()
  OMES_CHECK_OK=()
  OMES_CHECK_DETAILS=()

  local overall=0
  local m
  for m in "$@"; do
    module_load "$m"

    if [[ "$MODULE_SCOPE" == "root" ]] && ! omes_is_root; then
      OMES_CHECK_NAMES+=("$m")
      OMES_CHECK_OK+=("false")
      OMES_CHECK_DETAILS+=("requires root privilege")
      overall=1
      continue
    fi
    if [[ "$MODULE_SCOPE" == "user" ]] && omes_is_root; then
      OMES_CHECK_NAMES+=("$m")
      OMES_CHECK_OK+=("false")
      OMES_CHECK_DETAILS+=("must not run as root")
      overall=1
      continue
    fi

    local detail=""
    if detail="$(module_check 2>&1)"; then
      OMES_CHECK_NAMES+=("$m")
      OMES_CHECK_OK+=("true")
      OMES_CHECK_DETAILS+=("${detail:-ok}")
    else
      OMES_CHECK_NAMES+=("$m")
      OMES_CHECK_OK+=("false")
      OMES_CHECK_DETAILS+=("${detail:-check failed}")
      overall=1
    fi
  done

  return "$overall"
}

# run_apply <name> [name...]
# For each module (assumed to have already passed run_checks): enforce
# scope, open a backup session, apply, verify, then persist state. Stops
# immediately on the first failure, naming the offending module (exit 6 for
# apply failures, exit 7 for verification failures, exit 5 for a scope
# violation).
run_apply() {
  local m
  for m in "$@"; do
    module_load "$m"

    if [[ "$MODULE_SCOPE" == "root" ]] && ! omes_is_root; then
      omes_die "$OMES_EX_PRIVILEGE" "module '${m}' requires root privilege"
    fi
    if [[ "$MODULE_SCOPE" == "user" ]] && omes_is_root; then
      omes_die "$OMES_EX_PRIVILEGE" "module '${m}' must not run as root"
    fi

    log_info "applying module: ${m}"

    OMES_MANAGED_PATHS=()

    if ! omes_dry_run; then
      backup_begin "$m" "pre-apply" >/dev/null
    fi

    local apply_rc=0
    module_apply || apply_rc=$?
    if [[ "$apply_rc" -ne 0 ]]; then
      if ! omes_dry_run; then
        backup_finish >/dev/null
      fi
      # A module_apply that returns exactly $OMES_EX_NETWORK (e.g. via
      # lib/omes/pkg.sh's pkg_install/pkg_apt_update detecting network is
      # unavailable mid-apply) is surfaced as exit 8, not the generic
      # module-apply-failed exit 6 - see lib/omes/pkg.sh's return-code
      # convention comment.
      local apply_exit="$OMES_EX_MODULE_APPLY"
      if [[ "$apply_rc" == "$OMES_EX_NETWORK" ]]; then
        apply_exit="$OMES_EX_NETWORK"
      fi
      omes_die "$apply_exit" "module apply failed: ${m} (exit ${apply_rc})"
    fi

    if ! omes_dry_run; then
      backup_finish >/dev/null
      # Retention (docs/architecture.md Section 7.5): prune after every
      # successful apply's backup step, never after a failed one (a
      # failed apply's backup is exactly the recovery point an operator
      # needs, so it must not be pruned away by an unrelated success).
      backup_prune
    fi

    if omes_dry_run; then
      log_info "[dry-run] would verify: ${m}"
    else
      if ! module_verify; then
        omes_die "$OMES_EX_VERIFY" "module verification failed: ${m}"
      fi
    fi

    if ! omes_dry_run; then
      local joined=""
      if [[ "${#OMES_MANAGED_PATHS[@]}" -gt 0 ]]; then
        local IFS=':'
        joined="${OMES_MANAGED_PATHS[*]}"
      fi
      local now prev_status
      now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      prev_status="$(state_get "module.${m}.status" 2>/dev/null || true)"
      state_set "module.${m}.status" "applied"
      # applied_at records the first successful apply (it only moves when a
      # module transitions into "applied"); last_run_at moves on every
      # successful run, so an idempotent re-run is observable as
      # "last_run_at changed, applied_at did not".
      if [[ "$prev_status" != "applied" ]] || ! state_get "module.${m}.applied_at" >/dev/null 2>&1; then
        state_set "module.${m}.applied_at" "$now"
      fi
      state_set "module.${m}.last_run_at" "$now"
      state_set "module.${m}.version" "${OMES_VERSION}"
      state_set "module.${m}.managed_paths" "$joined"
    fi

    log_info "module applied: ${m}"
  done
}
