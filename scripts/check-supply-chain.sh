#!/usr/bin/env bash
# shellcheck shell=bash
#
# scripts/check-supply-chain.sh - local/CI supply-chain guardrails (issue #16).
#
# Checks performed:
#   1. Every `uses:` reference in .github/workflows/*.yml is pinned to a full
#      40-hex commit SHA (not a mutable tag/branch). Local composite actions
#      (`uses: ./...`) and Docker-image actions (`uses: docker://...`) are
#      skipped, since they are not GitHub-tag-versioned references.
#   2. No tracked shell script pipes a network download directly into a
#      shell interpreter (`curl ... | bash`, `wget ... | sh`, etc.). The only
#      permitted pattern for fetching a third-party installer is: download to
#      a file first (e.g. `curl -fsSL <url> -o <tmpfile>`), optionally verify
#      a checksum, then execute that file as its own, separate step (see
#      docs/security.md section 6 and ADR-0006). A line that is a deliberate,
#      reviewed exception may be marked with a trailing
#      `# check-supply-chain: allow` comment.
#   3. External (http/https) download URLs found under bin/, lib/,
#      modules/, install/ are listed for human review (informational only;
#      never fails the check).
#
# Usage: scripts/check-supply-chain.sh
set -Eeuo pipefail

repo_root() {
  git rev-parse --show-toplevel
}

# is_shell_script FILE - see scripts/lint.sh for the same predicate.
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
      *.sh) continue ;;
      bin/* | install/*)
        is_shell_script "$f" && out+=("$f")
        ;;
    esac
  done < <(git ls-files -- 'bin/*' 'install/*' 2>/dev/null)

  if [[ ${#out[@]} -gt 0 ]]; then
    printf '%s\n' "${out[@]}" | sort -u
  fi
}

# check_pinned_actions - fails if any workflow `uses:` is not a 40-hex SHA.
check_pinned_actions() {
  local status=0
  local wf lineno line val ref

  shopt -s nullglob
  local -a workflows=(.github/workflows/*.yml .github/workflows/*.yaml)
  shopt -u nullglob

  if [[ ${#workflows[@]} -eq 0 ]]; then
    echo "check-supply-chain: no .github/workflows/*.yml found" >&2
    return 0
  fi

  for wf in "${workflows[@]}"; do
    lineno=0
    while IFS= read -r line; do
      lineno=$((lineno + 1))
      [[ "$line" =~ ^[[:space:]]*-?[[:space:]]*uses:[[:space:]]*(.+)$ ]] || continue
      val="${BASH_REMATCH[1]}"
      val="${val%%#*}" # drop trailing comment
      val="$(printf '%s' "$val" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
      val="$(printf '%s' "$val" | sed -E "s/^[\"']//; s/[\"']\$//")"
      [[ -z "$val" ]] && continue

      case "$val" in
        ./* | docker://*) continue ;; # not a tag-versioned GitHub Action
      esac

      if [[ "$val" != *@* ]]; then
        echo "check-supply-chain: ${wf}:${lineno}: '${val}' has no @ref (unpinned action)" >&2
        status=1
        continue
      fi

      ref="${val##*@}"
      if ! [[ "$ref" =~ ^[0-9a-f]{40}$ ]]; then
        echo "check-supply-chain: ${wf}:${lineno}: '${val}' is not pinned to a full 40-hex commit SHA" >&2
        status=1
      fi
    done <"$wf"
  done

  return "$status"
}

# check_unsafe_pipes - fails if a tracked shell script pipes curl/wget
# directly into a shell interpreter.
check_unsafe_pipes() {
  local status=0
  local -a files
  mapfile -t files < <(collect_shell_files)

  local f lineno content
  for f in "${files[@]}"; do
    [[ -f "$f" ]] || continue
    while IFS=: read -r lineno content; do
      [[ -z "$lineno" ]] && continue
      [[ "$content" == *"check-supply-chain: allow"* ]] && continue
      echo "check-supply-chain: ${f}:${lineno}: unsafe pipe-to-shell (download to a file first): ${content#"${content%%[![:space:]]*}"}" >&2
      status=1
    done < <(grep -nE '\b(curl|wget)\b[^|]*\|[[:space:]]*(sudo[[:space:]]+)?(bash|sh)([[:space:]]|$)' "$f" 2>/dev/null || true)
  done

  return "$status"
}

# list_external_downloads - informational only, never fails.
list_external_downloads() {
  local -a files
  mapfile -t files < <(collect_shell_files | { grep -E '^(bin|lib|modules|install)/' || true; })

  if [[ ${#files[@]} -eq 0 ]]; then
    echo "check-supply-chain: no bin/, lib/, modules/, or install/ shell files yet (core installer tracked in #6)"
    return 0
  fi

  echo "check-supply-chain: external download URLs under bin/, lib/, modules/, install/ (for review):"
  if ! grep -nohE 'https?://[^"'\''[:space:])]+' "${files[@]}" 2>/dev/null | sort -u; then
    echo "  (none found)"
  fi
}

main() {
  cd "$(repo_root)"
  local overall=0

  echo "== Checking GitHub Actions are pinned to full commit SHAs =="
  check_pinned_actions || overall=1
  echo

  echo "== Checking for unsafe curl|wget piped directly into a shell =="
  check_unsafe_pipes || overall=1
  echo

  list_external_downloads
  echo

  if [[ "$overall" -ne 0 ]]; then
    echo "check-supply-chain: FAILED" >&2
  else
    echo "check-supply-chain: OK"
  fi
  return "$overall"
}

main "$@"
