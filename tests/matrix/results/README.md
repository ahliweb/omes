# tests/matrix/results/

One JSON file per `<image>-<scenario>` combination, written by
`scripts/test-matrix.sh` (issue #15). Every other file in this directory is
gitignored (see `.gitignore`) — this README is the only file that is ever
committed, so the directory itself still exists in a fresh checkout.

## Schema

```json
{
  "image": "ubuntu:24.04",
  "scenario": "fresh",
  "exit_code": 0,
  "ok": true,
  "duration_seconds": 42,
  "log": "tests/matrix/logs/ubuntu-24.04-fresh.log",
  "tier": "tier1",
  "notes": ""
}
```

- `tier` is per [`docs/compatibility-matrix.md`](../../../docs/compatibility-matrix.md)
  section 1/2, as classified by `scripts/test-matrix.sh`'s `image_tier()`.
- `notes` is empty on success; on failure it is a short human-readable reason
  (the full transcript is always in `log`).
- `log` is a path relative to the repository root, under the equally
  gitignored `tests/matrix/logs/` directory.

## How these are produced and archived

Run `scripts/test-matrix.sh` locally, or let `.github/workflows/compatibility.yml`
run it in CI. Locally, the files land here and stay on disk until you delete
them or re-run the matrix (each run overwrites its own `<image>-<scenario>.json`,
it does not accumulate history). In CI, the whole `tests/matrix/results/` and
`tests/matrix/logs/` directories are uploaded as a build artifact per run (see
the `compatibility.yml` `matrix` job) — that CI artifact, not this directory in
git, is the durable, referenceable evidence for a specific run. See
[`docs/testing.md`](../../../docs/testing.md) section "Evidence and release
gates" for how a specific artifact/run is cited from
`docs/business/release-gates.md`'s go/no-go checklist.
