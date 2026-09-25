---
issue: 238
type: ci
---
Add a blocking `scripts/release.sh --validate-fragments` mode that shares its parser with the release-compile path and runs in `tests/run.sh`, `scripts/lint.sh`, and a dedicated `validate-change-fragments` CI job, so a malformed `changes/*.md` fragment fails the pull request that introduces it instead of only surfacing at release time.
