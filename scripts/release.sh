#!/usr/bin/env bash
# scripts/release.sh - cut an OMES release per docs/adr/0010:
#   1. compile changes/*.md fragments into a new CHANGELOG.md section,
#   2. write the new version to VERSION,
#   3. remove the consumed fragments,
#   4. commit and create the vX.Y.Z tag (unless --no-commit).
#
# Usage: scripts/release.sh <X.Y.Z> [--date YYYY-MM-DD] [--no-commit] [--dry-run]
#
# Fragments are `changes/<issue>-<slug>.md` files with front-matter
# (`issue: N`, `type: added|changed|fixed|docs|ci|security`) followed by a
# one-line description. The compiled section groups entries by type and
# links each to its GitHub issue.

set -Eeuo pipefail
IFS=$'\n\t'

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_url="https://github.com/ahliweb/omes"

version=""
date_str="$(date -u +%Y-%m-%d)"
do_commit=1
dry_run=0

usage() {
  sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date)
      date_str="$2"
      shift 2
      ;;
    --no-commit)
      do_commit=0
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    -*)
      echo "release: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ -n "$version" ]]; then
        echo "release: unexpected argument: $1" >&2
        exit 2
      fi
      version="$1"
      shift
      ;;
  esac
done

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "release: a SemVer version X.Y.Z is required (got: '${version}')" >&2
  exit 2
fi

cd "$repo_root"

if [[ "$do_commit" -eq 1 ]] && [[ "$dry_run" -eq 0 ]] && [[ -n "$(git status --porcelain)" ]]; then
  echo "release: working tree is not clean; commit or discard changes first" >&2
  exit 1
fi

if git rev-parse -q --verify "refs/tags/v${version}" >/dev/null; then
  echo "release: tag v${version} already exists" >&2
  exit 1
fi

shopt -s nullglob
fragments=(changes/*.md)
shopt -u nullglob
if [[ "${#fragments[@]}" -eq 0 ]]; then
  echo "release: no fragments in changes/; nothing to release" >&2
  exit 1
fi

# type -> heading, in the order they appear in the changelog section.
type_order=(security added changed fixed docs ci)
declare -A type_heading=(
  [security]="Security"
  [added]="Added"
  [changed]="Changed"
  [fixed]="Fixed"
  [docs]="Documentation"
  [ci]="CI and tooling"
)
declare -A entries=()

for f in "${fragments[@]}"; do
  issue=""
  type=""
  desc=""
  in_front=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == "---" ]]; then
      if [[ "$in_front" -eq 0 ]]; then in_front=1; else in_front=2; fi
      continue
    fi
    if [[ "$in_front" -eq 1 ]]; then
      case "$line" in
        issue:*) issue="$(printf '%s' "${line#issue:}" | tr -d '[:space:]')" ;;
        type:*) type="$(printf '%s' "${line#type:}" | tr -d '[:space:]')" ;;
      esac
      continue
    fi
    # Body: every non-empty, non-comment line is part of the description
    # (fragments may wrap the sentence across lines).
    [[ -n "${line// /}" ]] || continue
    [[ "$line" != \<!--* ]] || continue
    desc="${desc:+$desc }${line}"
  done < "$f"

  if [[ -z "$issue" || -z "$type" || -z "$desc" ]]; then
    echo "release: malformed fragment (need issue, type and a description): $f" >&2
    exit 1
  fi
  if [[ -z "${type_heading[$type]:-}" ]]; then
    echo "release: unknown type '${type}' in $f (expected: ${type_order[*]})" >&2
    exit 1
  fi
  entries["$type"]+="- ${desc} ([#${issue}](${repo_url}/issues/${issue}))"$'\n'
done

section="## [${version}](${repo_url}/releases/tag/v${version}) - ${date_str}"$'\n'
for t in "${type_order[@]}"; do
  [[ -n "${entries[$t]:-}" ]] || continue
  section+=$'\n'"### ${type_heading[$t]}"$'\n\n'"${entries[$t]}"
done

# shellcheck disable=SC2016  # literal backticks, nothing to expand
header='# Changelog

All notable changes to OMES are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and OMES uses
[Semantic Versioning](https://semver.org/). Entries are compiled from
`changes/*.md` fragments by `scripts/release.sh` (see
`docs/adr/0010-versioning-and-change-fragments.md`); do not edit by hand.
'

if [[ -f CHANGELOG.md ]]; then
  # Keep everything from the first release section onward.
  previous="$(awk 'found || /^## \[/ {found=1; print}' CHANGELOG.md)"
else
  previous=""
fi

new_changelog="${header}"$'\n'"${section}"
if [[ -n "$previous" ]]; then
  new_changelog+=$'\n'"${previous}"$'\n'
fi

if [[ "$dry_run" -eq 1 ]]; then
  printf '%s\n' "$new_changelog"
  echo "release: [dry-run] would set VERSION=${version}, remove ${#fragments[@]} fragment(s), tag v${version}" >&2
  exit 0
fi

printf '%s' "$new_changelog" > CHANGELOG.md
printf '%s\n' "$version" > VERSION
git rm -q "${fragments[@]}"
git add CHANGELOG.md VERSION
echo "release: compiled ${#fragments[@]} fragment(s) into CHANGELOG.md; VERSION=${version}"

if [[ "$do_commit" -eq 1 ]]; then
  git commit -q -m "chore(release): v${version}

Compile changes/ fragments into CHANGELOG.md and bump VERSION."
  git tag -a "v${version}" -m "OMES v${version}"
  echo "release: committed and tagged v${version}; push with: git push origin main --follow-tags"
else
  echo "release: staged (--no-commit); review, then commit and tag v${version}"
fi
