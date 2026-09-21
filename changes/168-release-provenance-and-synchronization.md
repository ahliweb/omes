# Change: Enforce linear, verified and synchronized release provenance (#168)

## Summary
- Upgraded `scripts/release.sh` to enforce linear release provenance: clean `main` branch preflight, green required CI checks verification, exact commit tag binding, and dry-run simulation mode.
- Added remote tag read-back and GitHub Release publishing with read-back verification.
- Documented the historical `v0.3.0` tag divergence note in ADR-0010, release gates, and published the missing GitHub Release for `v0.3.0`.
- Synchronized `README.md` status claims with authoritative `v0.3.0` release state.
- Added release provenance and commit integrity technical gates in `docs/business/release-gates.md`.
- Added 7 bats unit tests in `tests/unit/release.bats` covering branch validation, dirty tree checks, tag conflict prevention, fragment compilation, and dry-run execution.
