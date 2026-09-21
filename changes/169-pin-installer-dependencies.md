# Change: Pin installer dependencies and eliminate unverified fetch paths (#169)

## Summary
- Upgraded `install/bootstrap.sh` to enforce explicit release channels: `stable` (default), `rc`, `edge`, and `dev`.
- Pinned stable channel default to immutable release tag (`v0.3.0`), eliminating uncontrolled execution from mutable `main`.
- Added remote origin URL validation before fetch/update to prevent target repository confusion or unintended remotes.
- Added uncommitted modification detection to refuse destructive overwrites in non-dev channels.
- Eliminated `git pull --ff-only ... || true`, ensuring all git checkout and update operations fail closed with descriptive non-zero exit codes.
- Added post-checkout commit SHA verification and recorded provenance metadata in `.omes-channel.json` with machine-readable `--json` output.
- Added 15 bats unit tests in `tests/unit/bootstrap.bats` covering all acceptance criteria.
