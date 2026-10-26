# OMES worker compatibility manifest

Owning issue: [#352](https://github.com/ahliweb/omes/issues/352). Parent architecture: [#350](https://github.com/ahliweb/omes/issues/350) and [ADR-0036](adr/0036-omes-web-separated-repositories-versioned-integration.md) section 4. Transport: [ADR-0027](adr/0027-control-center-pull-worker-transport.md).

The manifest is a small, non-secret, schema-validated JSON document that says which OMES release a Control Center or product deploy may pair with. It is **evidence, not authority**: it grants nothing, runs nothing, and never replaces the AWCMS `omes_control` checks on enrollment, nonce, idempotency, tenant scope and approval.

## 1. What is implemented

| Piece | Location | Status |
|---|---|---|
| Schema `worker-compatibility-manifest` (v1) | [`contracts/compatibility/v1/`](../contracts/compatibility/v1/worker-compatibility-manifest.schema.json) | Implemented |
| Valid and invalid fixtures (with `.reason.txt` sidecars) | `contracts/compatibility/v1/fixtures/worker-compatibility-manifest/` | Implemented; validated by `scripts/check-contracts.py` |
| Consumer decision table (replayable by AWCMS and OMES-Web) | [`contracts/compatibility/v1/consumer-scenarios.json`](../contracts/compatibility/v1/consumer-scenarios.json) | Implemented |
| Producer, `--check` gate and reference consumer decision | `scripts/generate-worker-compat-manifest.py` | Implemented |
| Tests, including a synthetic enroll/poll/result/heartbeat flow | `tests/py/compat/test_worker_compat_manifest.py` | Implemented |
| Signing the manifest | n/a | Not implemented yet (tracked in #352) |
| Publishing the manifest as an asset of a tagged release | n/a | Not implemented yet (tracked in #352) |
| A reviewed installer URI and sha256 | n/a | Not implemented yet (tracked in #352); no release currently ships an installer asset, so `installer.status` is `not_published` |
| AWCMS builds proven against a release (`verified_builds`) | n/a | Not implemented yet (tracked in #352); the consumer fixture run belongs to [AWCMS #1185](https://github.com/ahliweb/awcms/issues/1185) |
| OMES-Web deploy gate | OMES-Web | Not in this repository ([OMES-Web #20](https://github.com/ahliweb/omes-web/issues/20)) |

## 2. Content

Every field comes from a repository fact; nothing is typed by hand at release time.

| Field | Source |
|---|---|
| `product_version` | `VERSION` |
| `channel`, `release_tag`, `release_time`, `commit_sha` | Producer arguments. `stable` needs all three and `release_tag == v<VERSION>`; `unreleased` carries none |
| `worker_protocol` | Pull-worker facts: outbound HTTPS only, operations `enroll`, `poll`, `result`, `heartbeat`. `request_signing` is `not_implemented` because `lib/omes/py/jobs/worker.py` does not sign requests yet; a test fails when that changes |
| `control_contract` | sha256 of each pinned `contracts/control-center/v1` worker schema plus `operation-request`, and one digest over the sorted `path:sha256` lines |
| `awcms_omes_control` | Declared contract range (`>= contract_version`, `< next major`) and `verified_builds`, empty until tested fixtures exist |
| `platform_support` | Supported Ubuntu/Mint versions and architectures, mirroring `lib/omes/detect.sh` |
| `capability_posture` | The same `capability_registry_digest` formula the worker puts in its heartbeat, the worker operation allowlist and the pinned Hermes baseline from `architecture/capabilities.json` |
| `integrity` | `signature_status: unsigned` until signing exists |
| `consumer_policy` | Fixed: accept `stable` only, fail closed when unverifiable, never fetch at host boot |

The schema forbids any credential-like field, and the validator additionally rejects secret-shaped values ([contracts/README.md](../contracts/README.md)).

## 3. Producing and checking

```bash
python3 scripts/generate-worker-compat-manifest.py                    # unreleased manifest to stdout
python3 scripts/generate-worker-compat-manifest.py --check            # self-consistency gate, no output files
python3 scripts/generate-worker-compat-manifest.py --channel stable \
  --tag v<VERSION> --release-time <UTC ISO-8601> --commit <40-hex> --out worker-compatibility.json
python3 scripts/generate-worker-compat-manifest.py --verify worker-compatibility.json \
  --consumer-contract-version 1.0.0 --consumer-contract-digest sha256:<pinned digest>
```

Output is deterministic (sorted keys, no wall-clock value). A stable manifest must be generated from the tagged checkout, so the digests describe the contract bytes of that tag. `--check` is not yet wired into `scripts/ci-local.sh` or `scripts/lint.sh`; see the change fragment for the exact line to add.

## 4. Consumer rules (AWCMS and OMES-Web)

1. Pin an exact `release_tag` and the manifest's sha256 from reviewed release metadata. Never follow `main`.
2. Resolve the manifest at **build or deploy time**, from the pinned release. OMES hosts never fetch it at boot, and `consumer_policy.fetch_at_host_boot` is always `false`.
3. Fail closed. Reject `channel != stable`, an unknown `schema_version`, any schema or secret violation, a missing release identity, a tag that disagrees with `product_version`, a `control_contract.digest` that does not recompute from `files`, a digest that differs from the consumer's vendored copy, and a contract version outside the declared range. Show the result as **unsupported/unknown** with an upgrade hint; never as healthy.
4. Treat an empty `verified_builds` as "range declared, build unverified". Do not display it as tested.
5. Do not assume signed requests while `worker_protocol.request_signing` is `not_implemented`.
6. Replay [`consumer-scenarios.json`](../contracts/compatibility/v1/consumer-scenarios.json) in the consumer's own CI. The expected `reason_codes` are the stable vocabulary: `manifest_invalid`, `unknown_schema_version`, `unsupported_channel`, `stable_release_identity_missing`, `release_tag_version_mismatch`, `manifest_digest_inconsistent`, `contract_version_out_of_range`, `contract_digest_mismatch`, `installer_not_published`, `installer_incomplete`.
7. Do not import OMES host internals and do not install a Bun runtime for the Python worker. The inputs are this schema, its fixtures and the pinned control-center contract files.

## 5. Provenance, rollover and rollback

- **Provenance.** The manifest will be an additional asset of an official tagged release next to `release-manifest.json` and `SHA256SUMS`, so its sha256 is covered by the existing checksum file. Until a release actually ships it, consumers have no published manifest to pin: Not implemented yet (tracked in #352). Fixtures carry the clearly synthetic version `0.6.0-fixture` and must never be treated as a release.
- **Rollover.** Promote a new OMES release by pinning its tag and manifest digest in staging first, running the consumer scenarios and the AWCMS compatibility fixtures, then promoting. A contract major bump needs a new `contracts/control-center/v<next>` directory and a new manifest range.
- **Rollback.** Re-pin the previous tag and manifest digest. Host, CMS and site roll back independently ([ADR-0036](adr/0036-omes-web-separated-repositories-versioned-integration.md) section 7); database migrations stay forward-only.
- **Unreleased `main`.** The producer can describe it for diagnostics (`channel: unreleased`), but every consumer must refuse it.

## 6. Boundaries

The manifest adds no listener, scheduler, executor or UI. It does not change the signed HTTPS pull-worker into an inbound WebSocket, and it does not duplicate the AWCMS Control Center, Hermes runtime or the release system ([ADR-0017](adr/0017-upstream-first-ownership-and-boundary-enforcement.md)).
