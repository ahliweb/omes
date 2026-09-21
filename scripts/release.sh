#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/release.sh - cut and verify an OMES release per docs/adr/0010 & issue #168:
#   1. enforces clean main branch and required green checks on release commit,
#   2. compiles changes/*.md fragments into a new CHANGELOG.md section,
#   3. writes the new version to VERSION and synchronizes README status,
#   4. removes the consumed fragments,
#   5. commits and creates an annotated git tag vX.Y.Z pointing to exact commit,
#   6. optionally pushes and publishes GitHub Release with read-back verification.
#
# Usage: scripts/release.sh <X.Y.Z> [OPTIONS]
#
# Options:
#   --date YYYY-MM-DD     Release date for CHANGELOG.md (default: UTC today)
#   --no-commit           Stage changes without committing or tagging
#   --dry-run             Validate conditions and print actions without mutation
#   --allow-non-main      Allow running on a non-main branch (for testing)
#   --allow-dirty         Allow running on a dirty working tree (for testing)
#   --skip-ci-check       Skip querying GitHub check-runs API
#   --bundle-dir DIR      Directory to write release evidence bundle in (default: dist/release-vX.Y.Z)
#   --skip-bundle         Skip generating SLSA provenance and SBOM evidence bundle
#   --push                Push the created tag to origin
#   --publish             Push tag, create GitHub Release, and verify read-back
#   -h, --help            Show this help text

set -Eeuo pipefail
IFS=$'\n\t'

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_url="https://github.com/ahliweb/omes"

version=""
date_str="$(date -u +%Y-%m-%d)"
do_commit=1
dry_run=0
allow_non_main=0
allow_dirty=0
skip_ci_check=0
bundle_dir=""
skip_bundle=0
do_push=0
do_publish=0

log() { printf '[release] %s\n' "$*"; }
warn() { printf '[release] WARN %s\n' "$*" >&2; }
err() { printf '[release] ERROR %s\n' "$*" >&2; }
die() {
  err "$*"
  exit 1

}

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date)
      [[ $# -ge 2 ]] || die "--date requires an argument"
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
    --allow-non-main)
      allow_non_main=1
      shift
      ;;
    --allow-dirty)
      allow_dirty=1
      shift
      ;;
    --skip-ci-check)
      skip_ci_check=1
      shift
      ;;
    --bundle-dir)
      [[ $# -ge 2 ]] || die "--bundle-dir requires an argument"
      bundle_dir="$2"
      shift 2
      ;;
    --skip-bundle)
      skip_bundle=1
      shift
      ;;
    --push)

      do_push=1
      shift
      ;;
    --publish)
      do_publish=1
      do_push=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    -*)
      err "unknown option: $1"
      usage >&2
      exit 2
      ;;
    *)
      if [[ -n "$version" ]]; then
        err "unexpected argument: $1"
        exit 2
      fi
      version="$1"
      shift
      ;;
  esac
done

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  err "a SemVer version X.Y.Z is required (got: '${version}')"
  exit 2
fi

cd "$repo_root"

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

# 1. Branch verification
current_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")"
if [[ "$allow_non_main" -ne 1 && "$current_branch" != "main" ]]; then
  die "release must be executed on branch 'main' (current: '${current_branch}'); pass --allow-non-main to override"
fi

# 2. Working tree cleanliness
if [[ "$do_commit" -eq 1 && "$dry_run" -eq 0 && "$allow_dirty" -ne 1 ]]; then
  dirty_status="$(git status --porcelain 2>/dev/null || true)"
  if [[ -n "$dirty_status" ]]; then
    die "working tree is not clean; commit, stash or discard changes before releasing"
  fi
fi

# 3. Tag collision and commit target check
tag_name="v${version}"
if git rev-parse -q --verify "refs/tags/${tag_name}" >/dev/null; then
  existing_tag_sha="$(git rev-parse "refs/tags/${tag_name}^{commit}" 2>/dev/null || true)"
  current_head_sha="$(git rev-parse HEAD)"
  if [[ "$existing_tag_sha" != "$current_head_sha" ]]; then
    die "tag ${tag_name} already exists pointing to a different commit (${existing_tag_sha} != current HEAD ${current_head_sha}); refusing to rewrite public release tag"
  fi
  warn "tag ${tag_name} already exists and matches current HEAD"
fi

# 4. CI checks verification on current HEAD if not skipped
if [[ "$skip_ci_check" -ne 1 && "$dry_run" -ne 1 ]] && command -v gh >/dev/null 2>&1; then
  current_sha="$(git rev-parse HEAD)"
  log "verifying GitHub CI checks for commit ${current_sha}..."

  # Query commit check runs via GitHub API
  check_output="$(gh api "repos/:owner/:repo/commits/${current_sha}/check-runs" 2>/dev/null || true)"
  if [[ -n "$check_output" ]]; then
    failing_checks="$(python3 -c "
import sys, json
try:
    data = json.loads('''$check_output''')
    runs = data.get('check_runs', [])
    failing = [r['name'] for r in runs if r.get('conclusion') not in ('success', 'neutral', 'skipped') and r.get('status') == 'completed']
    in_progress = [r['name'] for r in runs if r.get('status') != 'completed']
    if failing:
        print('FAIL: ' + ', '.join(failing))
    elif in_progress:
        print('PENDING: ' + ', '.join(in_progress))
except Exception as e:
    pass
" 2>/dev/null || true)"

    if [[ -n "$failing_checks" ]]; then
      die "CI checks on ${current_sha} have not passed: ${failing_checks}"
    fi
    log "CI checks verified green for commit ${current_sha}"
  fi
fi

# ---------------------------------------------------------------------------
# Compile change fragments into CHANGELOG.md (if any)
# ---------------------------------------------------------------------------

shopt -s nullglob
fragments=(changes/*.md)
shopt -u nullglob

changelog_heading="## [${version}](${repo_url}/releases/tag/v${version}) - ${date_str}"

if [[ "${#fragments[@]}" -eq 0 ]]; then
  # If no fragments exist, verify that CHANGELOG.md already has this version heading
  if [[ -f CHANGELOG.md ]] && grep -qF "## [${version}]" CHANGELOG.md; then
    log "no fragments in changes/; existing changelog section found for ${version}"
  else
    die "no fragments found in changes/ and CHANGELOG.md does not contain heading for ${version}"
  fi
  compile_changelog=0
else
  compile_changelog=1
fi

if [[ "$compile_changelog" -eq 1 ]]; then
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
      [[ -n "${line// /}" ]] || continue
      [[ "$line" != \<!--* ]] || continue
      desc="${desc:+$desc }${line}"
    done <"$f"

    if [[ -z "$issue" || -z "$type" || -z "$desc" ]]; then
      die "malformed fragment (need issue, type and a description): $f"
    fi
    if [[ -z "${type_heading[$type]:-}" ]]; then
      die "unknown type '${type}' in $f (expected: ${type_order[*]})"
    fi
    entries["$type"]+="- ${desc} ([#${issue}](${repo_url}/issues/${issue}))"$'\n'
  done

  section="${changelog_heading}"$'\n'
  for t in "${type_order[@]}"; do
    [[ -n "${entries[$t]:-}" ]] || continue
    section+=$'\n'"### ${type_heading[$t]}"$'\n\n'"${entries[$t]}"
  done

  # shellcheck disable=SC2016
  header='# Changelog

All notable changes to OMES are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and OMES uses
[Semantic Versioning](https://semver.org/). Entries are compiled from
`changes/*.md` fragments by `scripts/release.sh` (see
`docs/adr/0010-versioning-and-change-fragments.md`); do not edit by hand.
'

  if [[ -f CHANGELOG.md ]]; then
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
    log "[dry-run] would set VERSION=${version}, remove ${#fragments[@]} fragment(s), tag ${tag_name}"
    exit 0
  fi

  printf '%s' "$new_changelog" >CHANGELOG.md
  printf '%s\n' "$version" >VERSION
  git rm -q "${fragments[@]}"
  git add CHANGELOG.md VERSION
  log "compiled ${#fragments[@]} fragment(s) into CHANGELOG.md; VERSION=${version}"

  # Synchronize README status claim if present
  if [[ -f README.md ]]; then
    sed -i -E "s/\*\*Pre-alpha, version \`[^\`]+\`\.\*\*/\*\*Pre-alpha, version \`${version}\`.\*\*/g" README.md
    git add README.md
  fi
else
  if [[ "$dry_run" -eq 1 ]]; then
    log "[dry-run] verified release prerequisites for ${version}; no fragments to compile"
    exit 0
  fi
  printf '%s\n' "$version" >VERSION
  git add VERSION
  if [[ -f README.md ]]; then
    sed -i -E "s/\*\*Pre-alpha, version \`[^\`]+\`\.\*\*/\*\*Pre-alpha, version \`${version}\`.\*\*/g" README.md
    git add README.md
  fi
fi

# ---------------------------------------------------------------------------
# Commit and Tag
# ---------------------------------------------------------------------------

target_commit=""

if [[ "$do_commit" -eq 1 ]]; then
  if [[ -n "$(git status --porcelain)" ]]; then
    git commit -q -m "chore(release): v${version}

Compile changes/ fragments into CHANGELOG.md and bump VERSION."
    log "created release commit for v${version}"
  fi
  target_commit="$(git rev-parse HEAD)"

  if ! git rev-parse -q --verify "refs/tags/${tag_name}" >/dev/null; then
    git tag -a "${tag_name}" -m "OMES ${tag_name}"
    log "created annotated tag ${tag_name} -> ${target_commit}"
  fi

  # Verify tag points to target commit
  verified_tag_sha="$(git rev-parse "refs/tags/${tag_name}^{commit}")"
  if [[ "$verified_tag_sha" != "$target_commit" ]]; then
    die "tag verification failure: tag ${tag_name} points to ${verified_tag_sha}, expected ${target_commit}"
  fi
  log "verified tag ${tag_name} matches release commit ${target_commit}"

  # Generate and verify release evidence bundle (SLSA provenance, SBOM, SHA256SUMS)
  if [[ "$skip_bundle" -eq 0 && -f "${repo_root}/scripts/generate-release-bundle.py" ]]; then
    bundle_path="${bundle_dir:-${repo_root}/dist/release-${tag_name}}"

    log "generating release evidence bundle in ${bundle_path}..."
    python3 "${repo_root}/scripts/generate-release-bundle.py" \
      --version "$version" \
      --tag "$tag_name" \
      --commit "$target_commit" \
      --output "$bundle_path" \
      --date "$date_str"
    log "verifying release evidence bundle..."
    python3 "${repo_root}/scripts/verify-release-bundle.py" \
      --bundle-dir "$bundle_path" \
      --version "$version" \
      --commit "$target_commit"
    log "evidence bundle verified: ${bundle_path}"
  fi
else
  log "staged release files (--no-commit); review before committing"
fi

# ---------------------------------------------------------------------------
# Push and Publish
# ---------------------------------------------------------------------------

if [[ "$do_push" -eq 1 && "$dry_run" -eq 0 && "$do_commit" -eq 1 ]]; then
  log "pushing tag ${tag_name} to origin..."
  git push origin "${tag_name}"

  # Read back tag from origin
  remote_tag_sha="$(git ls-remote origin "refs/tags/${tag_name}" | awk '{print $1}')"
  if [[ -z "$remote_tag_sha" ]]; then
    die "remote tag read-back failed: tag ${tag_name} not found on origin"
  fi
  log "verified remote tag ${tag_name} exists on origin (${remote_tag_sha})"
fi

if [[ "$do_publish" -eq 1 && "$dry_run" -eq 0 && "$do_commit" -eq 1 ]]; then
  command -v gh >/dev/null 2>&1 || die "'gh' CLI is required for --publish"

  # Extract release notes for this version from CHANGELOG.md
  tmp_notes="$(mktemp)"
  python3 -c "
import sys
with open('CHANGELOG.md') as f:
    content = f.read()

marker = '## [${version}]'
start = content.find(marker)
if start != -1:
    end = content.find('\n## [', start + len(marker))
    section = content[start:end] if end != -1 else content[start:]
    lines = section.splitlines()
    # Skip the heading line
    notes = '\n'.join(lines[1:]).strip()
    with open('${tmp_notes}', 'w') as out:
        out.write(notes + '\n')
"

  log "publishing GitHub Release for ${tag_name}..."
  if gh release view "${tag_name}" >/dev/null 2>&1; then
    gh release edit "${tag_name}" --title "OMES ${tag_name}" --notes-file "$tmp_notes"
  else
    gh release create "${tag_name}" --title "OMES ${tag_name}" --notes-file "$tmp_notes" --verify-tag
  fi
  rm -f "$tmp_notes"

  # Upload release bundle assets if present
  if [[ "$skip_bundle" -eq 0 && -n "${bundle_path:-}" && -d "$bundle_path" ]]; then
    log "uploading release bundle artifacts to GitHub Release ${tag_name}..."
    mapfile -t bundle_files < <(find "$bundle_path" -type f | sort)
    if [[ "${#bundle_files[@]}" -gt 0 ]]; then
      gh release upload "${tag_name}" "${bundle_files[@]}" --clobber
      log "uploaded ${#bundle_files[@]} release artifacts"
    fi
  fi

  # Read-back verification of GitHub Release
  rel_view="$(gh release view "${tag_name}" --json tagName,name,publishedAt,assets)"
  if [[ -z "$rel_view" ]]; then
    die "GitHub release read-back verification failed for ${tag_name}"
  fi
  log "GitHub Release published and verified: ${tag_name}"
fi

log "release v${version} completed successfully"
