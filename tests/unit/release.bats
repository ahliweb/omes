#!/usr/bin/env bats
# tests/unit/release.bats - unit & integration tests for scripts/release.sh (issue #168).

setup() {
  load '../test_helper.bash'
  omes_test_setup

  WORK_REPO="${OMES_TEST_TMPDIR}/work_repo"
  mkdir -p "$WORK_REPO"

  # Initialize a git repository to simulate release operations
  git -C "$WORK_REPO" init -q -b main
  git -C "$WORK_REPO" config user.email "release-test@example.invalid"
  git -C "$WORK_REPO" config user.name "Release Tester"

  # Copy scripts/release.sh and minimal project files
  mkdir -p "$WORK_REPO/scripts" "$WORK_REPO/changes"
  cp "${OMES_TEST_ROOT}/scripts/release.sh" "$WORK_REPO/scripts/release.sh"
  chmod +x "$WORK_REPO/scripts/release.sh"

  echo "0.2.0" > "$WORK_REPO/VERSION"
  cat > "$WORK_REPO/README.md" <<'EOF'
# OMES
**Pre-alpha, version `0.2.0`.**
EOF
  cat > "$WORK_REPO/CHANGELOG.md" <<'EOF'
# Changelog

## [0.2.0](https://github.com/ahliweb/omes/releases/tag/v0.2.0) - 2026-09-20

### Added
- Initial baseline release.
EOF

  # Commit initial state
  git -C "$WORK_REPO" add -A
  git -C "$WORK_REPO" commit -q -m "initial commit"
  git -C "$WORK_REPO" tag -a "v0.2.0" -m "OMES v0.2.0"
}

teardown() {
  omes_test_teardown
}

@test "release fails when run on non-main branch without override" {
  git -C "$WORK_REPO" checkout -q -b feature/test-branch

  cat > "$WORK_REPO/changes/100-test.md" <<'EOF'
---
issue: 100
type: added
---
Test feature description.
EOF

  run bash "$WORK_REPO/scripts/release.sh" 0.3.0
  [ "$status" -eq 1 ]
  [[ "$output" == *"must be executed on branch 'main'"* ]]

  # With --allow-non-main, branch check passes
  run bash "$WORK_REPO/scripts/release.sh" 0.3.0 --allow-non-main --dry-run
  [ "$status" -eq 0 ]
}

@test "release fails when working tree has uncommitted modifications" {
  cat > "$WORK_REPO/changes/100-test.md" <<'EOF'
---
issue: 100
type: added
---
Test feature description.
EOF
  # Introduce uncommitted dirty file
  echo "dirty modification" >> "$WORK_REPO/README.md"

  run bash "$WORK_REPO/scripts/release.sh" 0.3.0 --skip-ci-check
  [ "$status" -eq 1 ]
  [[ "$output" == *"working tree is not clean"* ]]
}

@test "release fails if tag already exists pointing to a different commit" {
  # Create an external/divergent commit and tag it v0.3.0
  git -C "$WORK_REPO" commit -q --allow-empty -m "divergent commit"
  git -C "$WORK_REPO" tag -a "v0.3.0" -m "OMES v0.3.0"
  git -C "$WORK_REPO" reset -q --hard HEAD~1

  cat > "$WORK_REPO/changes/100-test.md" <<'EOF'
---
issue: 100
type: added
---
Test feature description.
EOF
  git -C "$WORK_REPO" add changes/
  git -C "$WORK_REPO" commit -q -m "add change fragment"

  run bash "$WORK_REPO/scripts/release.sh" 0.3.0 --skip-ci-check
  [ "$status" -eq 1 ]
  [[ "$output" == *"already exists pointing to a different commit"* ]]
}

@test "release fails when no fragments exist and no changelog heading exists" {
  # Clean repo with no fragments and no 0.4.0 heading
  run bash "$WORK_REPO/scripts/release.sh" 0.4.0 --skip-ci-check
  [ "$status" -eq 1 ]
  [[ "$output" == *"no fragments found in changes/"* ]]
}

@test "release --dry-run validates and produces no mutations" {
  cat > "$WORK_REPO/changes/100-test.md" <<'EOF'
---
issue: 100
type: added
---
Test feature description.
EOF

  local before_head
  before_head="$(git -C "$WORK_REPO" rev-parse HEAD)"

  run bash "$WORK_REPO/scripts/release.sh" 0.3.0 --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"[dry-run]"* ]]

  # Verify no files were deleted, committed, or tagged
  [ -f "$WORK_REPO/changes/100-test.md" ]
  [ "$(cat "$WORK_REPO/VERSION")" = "0.2.0" ]
  local after_head
  after_head="$(git -C "$WORK_REPO" rev-parse HEAD)"
  [ "$before_head" = "$after_head" ]
  ! git -C "$WORK_REPO" rev-parse -q --verify "refs/tags/v0.3.0" >/dev/null
}

@test "release compiles fragments, bumps VERSION, synchronizes README, and tags HEAD" {
  cat > "$WORK_REPO/changes/100-test.md" <<'EOF'
---
issue: 100
type: added
---
Test feature description.
EOF
  git -C "$WORK_REPO" add changes/
  git -C "$WORK_REPO" commit -q -m "add change fragment"

  run bash "$WORK_REPO/scripts/release.sh" 0.3.0 --skip-ci-check
  [ "$status" -eq 0 ]
  [[ "$output" == *"compiled 1 fragment(s) into CHANGELOG.md; VERSION=0.3.0"* ]]

  # Fragments removed
  [ ! -f "$WORK_REPO/changes/100-test.md" ]

  # VERSION bumped
  [ "$(cat "$WORK_REPO/VERSION")" = "0.3.0" ]

  # README synchronized
  grep -q "Pre-alpha, version \`0.3.0\`." "$WORK_REPO/README.md"

  # Tag created pointing to HEAD
  local head_sha tag_sha
  head_sha="$(git -C "$WORK_REPO" rev-parse HEAD)"
  tag_sha="$(git -C "$WORK_REPO" rev-parse "refs/tags/v0.3.0^{commit}")"
  [ "$tag_sha" = "$head_sha" ]
}

@test "release --no-commit stages changelog and VERSION without committing or tagging" {
  cat > "$WORK_REPO/changes/100-test.md" <<'EOF'
---
issue: 100
type: added
---
Test feature description.
EOF
  git -C "$WORK_REPO" add changes/
  git -C "$WORK_REPO" commit -q -m "add change fragment"

  run bash "$WORK_REPO/scripts/release.sh" 0.3.0 --no-commit --skip-ci-check
  [ "$status" -eq 0 ]
  [[ "$output" == *"staged release files (--no-commit)"* ]]

  # Staged changes exist
  [ -n "$(git -C "$WORK_REPO" status --porcelain)" ]
  # Tag not yet created
  ! git -C "$WORK_REPO" rev-parse -q --verify "refs/tags/v0.3.0" >/dev/null
}
