#!/usr/bin/env bash
# shellcheck shell=bash
#
# scripts/lint.sh - run ShellCheck, shfmt, and yamllint the same way locally
# and in .github/workflows/lint.yml.
#
# Usage:
#   scripts/lint.sh [shellcheck|shfmt|yamllint|all]
#     (default: all)
#
# Uses a locally installed tool when available (e.g. GitHub-hosted Ubuntu
# runners already ship ShellCheck), otherwise falls back to the pinned
# Docker images below so contributors and CI see identical results. See
# docs/ci.md for the policy on why these images are pinned by digest.
set -Eeuo pipefail

SHELLCHECK_IMAGE="koalaman/shellcheck@sha256:bb596a0d169b85ddd81d8b6d3a2ff6d5baf5fca10b97f575ebc647c3dff62b3d" # stable
SHFMT_IMAGE="mvdan/shfmt@sha256:bc5c1c08c0cfd8a77f5de08a448574fe163ea1acf41f97a7cbe02c101fcf1cfa"              # latest
YAMLLINT_IMAGE="cytopia/yamllint@sha256:3e9eb827ab2b12a5ea5f49d4257bb3aca94bba9f1ba427c8bc7f2456385a5204"      # latest

repo_root() {
  git rev-parse --show-toplevel
}

# is_shell_script FILE
# Best-effort detection for extensionless scripts under bin/ and install/:
# true if the file's shebang or MIME type identifies it as a shell script.
is_shell_script() {
  local f="$1"
  [[ -f "$f" ]] || return 1
  if head -n1 -- "$f" 2>/dev/null | grep -qE '^#!.*/(env[[:space:]]+)?(bash|sh)([[:space:]]|$)'; then
    return 0
  fi
  if command -v file >/dev/null 2>&1; then
    file --mime-type -b -- "$f" 2>/dev/null | grep -qE '^text/x-shellscript$'
    return $?
  fi
  return 1
}

# collect_shell_files
# Prints one tracked path per line, matching the deliverable's file scope:
#   *.sh, bin/*, install/*, modules/**/*.sh, lib/**/*.sh, tests/**/*.bash
# (any path ending in .sh already covers modules/**/*.sh and lib/**/*.sh).
collect_shell_files() {
  local -a out=()
  local f

  while IFS= read -r f; do
    case "$f" in
      *.sh) out+=("$f") ;;
      tests/*.bash) out+=("$f") ;;
    esac
  done < <(git ls-files)

  while IFS= read -r f; do
    case "$f" in
      *.sh) continue ;; # already collected above
      bin/* | install/*)
        is_shell_script "$f" && out+=("$f")
        ;;
    esac
  done < <(git ls-files -- 'bin/*' 'install/*' 2>/dev/null)

  if [[ ${#out[@]} -gt 0 ]]; then
    printf '%s\n' "${out[@]}" | sort -u
  fi
}

run_shellcheck() {
  local -a files
  mapfile -t files < <(collect_shell_files)
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "scripts/lint.sh: no shell files found to check" >&2
    return 0
  fi

  local status=0

  echo "== shellcheck -S warning (blocking) =="
  if command -v shellcheck >/dev/null 2>&1; then
    shellcheck -x -S warning "${files[@]}" || status=$?
  else
    docker run --rm -v "$(repo_root):/mnt" -w /mnt "$SHELLCHECK_IMAGE" \
      -x -S warning "${files[@]}" || status=$?
  fi

  echo
  echo "== shellcheck -S style (advisory, reported not blocking) =="
  if command -v shellcheck >/dev/null 2>&1; then
    shellcheck -x -S style "${files[@]}" || true
  else
    docker run --rm -v "$(repo_root):/mnt" -w /mnt "$SHELLCHECK_IMAGE" \
      -x -S style "${files[@]}" || true
  fi

  return "$status"
}

run_shfmt() {
  local -a files
  mapfile -t files < <(collect_shell_files)
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "scripts/lint.sh: no shell files found to format-check" >&2
    return 0
  fi

  echo "== shfmt -i 2 -ci -bn -d (non-blocking; repo shell style is still being established) =="
  local status=0
  if command -v shfmt >/dev/null 2>&1; then
    shfmt -i 2 -ci -bn -d "${files[@]}" || status=$?
  else
    docker run --rm -v "$(repo_root):/mnt" -w /mnt "$SHFMT_IMAGE" \
      -i 2 -ci -bn -d "${files[@]}" || status=$?
  fi

  if [[ "$status" -ne 0 ]]; then
    echo "scripts/lint.sh: shfmt reported formatting differences (see docs/ci.md: shfmt is advisory)" >&2
  fi
  return "$status"
}

run_yamllint() {
  local -a files=()
  local f
  while IFS= read -r f; do
    files+=("$f")
  done < <(git ls-files -- '*.yml' '*.yaml' 2>/dev/null)

  if [[ ${#files[@]} -eq 0 ]]; then
    echo "scripts/lint.sh: no YAML files found to lint" >&2
    return 0
  fi

  local -a config_args=()
  [[ -f "$(repo_root)/.yamllint.yml" ]] && config_args=(-c .yamllint.yml)

  echo "== yamllint =="
  local status=0
  if command -v yamllint >/dev/null 2>&1; then
    yamllint "${config_args[@]}" "${files[@]}" || status=$?
  else
    docker run --rm -v "$(repo_root):/mnt" -w /mnt "$YAMLLINT_IMAGE" \
      "${config_args[@]}" "${files[@]}" || status=$?
  fi
  return "$status"
}

run_contracts() {
  echo "== scripts/check-contracts.py (Control Center contract fixtures) =="
  if command -v python3 >/dev/null 2>&1; then
    python3 "$(repo_root)/scripts/check-contracts.py"
  else
    echo "scripts/lint.sh: python3 not available; cannot run scripts/check-contracts.py" >&2
    return 1
  fi
}

run_architecture() {
  echo "== scripts/check-architecture.py (ADR-0017 architecture boundary) =="
  if command -v python3 >/dev/null 2>&1; then
    python3 "$(repo_root)/scripts/check-architecture.py"
  else
    echo "scripts/lint.sh: python3 not available; cannot run scripts/check-architecture.py" >&2
    return 1
  fi
}

cmd_all() {
  local overall=0
  run_shellcheck || overall=1
  echo
  run_shfmt || true # advisory: never fails the aggregate run
  echo
  run_yamllint || overall=1
  echo
  run_contracts || overall=1
  echo
  run_architecture || overall=1
  return "$overall"
}

main() {
  cd "$(repo_root)"
  local cmd="${1:-all}"
  case "$cmd" in
    shellcheck) run_shellcheck ;;
    shfmt) run_shfmt ;;
    yamllint) run_yamllint ;;
    contracts) run_contracts ;;
    architecture) run_architecture ;;
    all) cmd_all ;;
    *)
      echo "usage: scripts/lint.sh [shellcheck|shfmt|yamllint|contracts|architecture|all]" >&2
      return 2
      ;;
  esac
}

main "$@"
