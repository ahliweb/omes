# Supply-chain provenance (issue #84)

> Status: implemented. Covers `lib/omes/py/provenance/{record,audit}.py`,
> the bash helper `provenance_record_component` and `omes audit
> provenance` in `lib/omes/cmd/audit-provenance.sh`,
> `modules/hermes/module.sh`'s provenance recording at install (both for
> the Hermes CLI itself and for uv-tool-/pipx-managed packages discovered
> on the host), and `modules/apt-base`/`modules/containers`'s
> package-manager provenance recording for every apt-managed package they
> install, via `lib/omes/pkg.sh`'s `pkg_record_provenance`.
>
> **A provenance report is not a security certification.** It records
> what OMES observed about how a component was installed and whether a
> checksum was verified at that time. It does not audit the component's
> own code, does not detect a compromise that happened after
> installation, and does not replace `docs/security.md` §6's
> supply-chain rules or CI's `scripts/check-supply-chain.sh`. See
> [docs/compatibility-evidence.md](./compatibility-evidence.md) for the
> related (but distinct) runtime version/compatibility evidence report
> (issue #83).

## 1. What is recorded, and where

At Hermes install time (`modules/hermes/module.sh`'s
`_hermes_download_and_install`, after a successful install),
`provenance_record_component` writes
`<state-dir>/provenance/<component>.json` (directory mode `0700`, file
mode `0600`):

```json
{
  "component": "hermes",
  "profile": "hermes",
  "installer_source_url": "https://hermes-agent.nousresearch.com/install.sh",
  "resolved_version": "hermes 1.2.3",
  "install_time": "2026-09-19T01:00:00Z",
  "checksum": {
    "algorithm": "sha256",
    "expected": "<hex or null>",
    "actual": "<hex or null>",
    "status": "verified"
  },
  "package_manager": null,
  "recorded_at": "2026-09-19T01:00:00Z"
}
```

`checksum.status` is one of: `verified` (a pin was supplied and
matched), `pinned` (a pin exists but was not independently reverified in
this record), `unverified` (no pin was supplied — the default when
`OMES_HERMES_INSTALLER_SHA256` is unset, and the default for a
uv-tool-/pipx-managed package with no known index URL — see §1a below),
`unknown` (audit-time state, e.g. an unparseable record),
`locally-built` (no remote checksum concept applies, e.g. a component
built from local source), or `package_manager_verified` (apt/dpkg
verified the package's signature/hash itself at install time — see §1a).

**This record never contains a credential, token, or `.env` content.**
`provenance_record_component`/`record.py` only ever accept the fields
above; there is no code path that copies an arbitrary caller-supplied
key into the record.

### `provenance_record_component` — the documented helper other modules may call

Any module that installs a component may call
`provenance_record_component <component> <profile> <payload-json>`
(defined in `lib/omes/cmd/audit-provenance.sh`) to register a
provenance record in the same place `omes audit provenance` reads from.
It honors `--dry-run` (skips the write, logs what would happen) and
never fails the caller's `module_apply` — a recording failure is logged
as a `WARN` and swallowed, since a provenance record is diagnostic, not
a precondition for the install itself.

## 1a. Package-manager provenance (apt, uv, pipx)

Beyond the Hermes installer record above, every package-manager-managed
component OMES installs also gets a provenance record, populating the
schema's `package_manager` field: `{"name": "apt"|"uv"|"pipx",
"package": "<name>", "version": "<resolved version>", "origin":
"<url or null>"}`.

**apt-managed packages (`modules/apt-base`, `modules/containers`).**
After a successful `module_apply`, both modules call
`lib/omes/pkg.sh`'s `pkg_record_provenance <pkg...>` for every package
they manage (not just the ones that specific run happened to install),
so the record always reflects the currently-resolved state:

- `resolved_version` — `dpkg-query -W -f='${Version}' <pkg>`.
- `installer_source_url` / `package_manager.origin` — the repository
  origin URL from `apt-cache policy <pkg>` (the first source line under
  "Version table:" for the current candidate).
- `checksum.status` — `package_manager_verified`. apt/dpkg verifies each
  package's signature and hash as part of `apt-get install` itself; OMES
  did not additionally pin or re-verify a checksum, but this is
  meaningfully different from `unverified` (see `omes audit provenance`
  below — `package_manager_verified` does not produce a WARN).
- Component name is the package name itself (e.g. `curl`, `docker-ce`),
  so `<state-dir>/provenance/curl.json` etc. sit alongside `hermes.json`.

A package that ends up not installed (e.g. `module_check` failed before
`module_apply` ran) is skipped — `pkg_record_provenance` never writes a
record for a package it cannot find via `dpkg-query`.

**uv-tool-/pipx-managed packages (`modules/hermes`).** After a
successful `module_apply`, the `hermes` module also discovers and
records provenance for any package already managed by `uv tool` or
`pipx` on the host — this is a general host-side discovery, not tied to
Hermes installing anything itself; today it is mainly relevant to
graphify (`uv tool install graphifyy` / `pipx install graphifyy`, see
[docs/graphify.md](graphify.md)), once
[#50](https://github.com/ahliweb/omes/issues/50) lands:

- **uv**: `uv tool list` output is parsed for each top-level
  `<name> <version>` line (indented entrypoint lines are ignored).
- **pipx**: `pipx list --json` is parsed (stdlib `json`, per
  [ADR-0012](adr/0012-python-stdlib-for-workflow-engines.md) — no `jq`
  dependency) for each venv's `metadata.main_package`.
- Component name is `uv:<package>` / `pipx:<package>` (e.g.
  `uv:graphifyy`), so these never collide with an apt package or with
  `hermes` itself.
- **Neither `uv tool list` nor `pipx list` exposes an index/origin URL.**
  `installer_source_url`/`package_manager.origin` are therefore always
  empty for these components today, and `checksum.status` is
  `unverified` — this is a documented current limitation, not a silent
  downgrade: `omes audit provenance` surfaces it as a `WARN`
  (`checksum_unverified`) and a missing-metadata `WARN`
  (`installer_source_url` is a required field), exactly as it would for
  any other unverified/incomplete record. If a future upstream `uv`/
  `pipx` release exposes the configured package index URL, that value
  should be threaded through here instead of leaving it empty.
- Both are best-effort and silent when `uv`/`pipx`/`python3` are not on
  PATH, or produce no parseable output — this is a discovery step, never
  a precondition for `module_apply` succeeding.

## 1b. Model/runtime artifact provenance (issue #236, threat AI-06)

A local model must not be trusted merely because it is local. #215
verifies the Hermes gateway's non-root identity and denies outbound
network access from that unit; it does not verify that the model
weights or runtime binaries a local inference server actually loads
are the ones the operator expects. `lib/omes/py/provenance/artifacts.py`
closes that gap by **layering on the same checksum/provenance model**
above, rather than building a second, parallel evidence system:

- **The operator explicitly DECLARES the artifact paths OMES should
  track** as OMES's own state — `ai.model_artifacts.declared` (set with
  `state_set ai.model_artifacts.declared
  '[{"component":"main-model","path":"/opt/models/model.gguf","expected_sha256":"<hex>"}]'`)
  or the `OMES_AI_MODEL_ARTIFACTS` environment override for testing.
  This module NEVER scans `$HERMES_HOME`, `.hermes/`, or any other
  Hermes-owned or auto-discovered directory for model files — per
  ADR-0017, artifact locations must come from an interface/config the
  operator declares, never from reading Hermes' private state.
- **`omes audit provenance --verify-artifacts [--force]`** hashes each
  declared artifact (SHA-256) and writes a provenance record under the
  component name `model-artifact:<name>` via the same
  `lib/omes/py/provenance/record.py` writer every other component uses,
  so the existing `omes audit provenance` reader, its
  checksum-mismatch-is-FAIL / missing-required-metadata-is-WARN findings
  evaluator, and its executable-review listing all apply to these
  records too, with no duplicated logic. A missing declared artifact
  (checksum status `missing`) is evaluated the same as a checksum
  mismatch: `FAIL`, fail-closed — never merely `unverified`.
- **Performance: hashing does not run on every check.** Hashing a
  multi-GB model file on every `omes health ai-privacy` invocation (or
  even every `omes audit provenance` run) would be prohibitively slow
  and is unnecessary, since the artifact is expected to change rarely.
  `--verify-artifacts` is therefore a deliberately separate, explicit,
  operator/cron-triggered operation — run it on whatever cadence matches
  how often your artifacts actually change (e.g. after every model
  update, or nightly). Even then, a declared artifact whose stat
  snapshot — `(size, mtime_ns, ctime_ns, ino, dev)`, not merely `(size,
  mtime)` — is unchanged since its last recorded verification is **not**
  re-hashed unless `--force` is also given: `mtime` alone is
  attacker-settable (`touch -d`/`os.utime`), so a same-size content swap
  that also restores the original mtime is still caught because `ctime`
  cannot be forged via `utime` and changes on any content or metadata
  write; a legacy snapshot recorded by older code (missing these keys)
  is always treated as "needs rehash", failing closed. **Documented
  residual limit:** this cache still trusts the filesystem's stat
  metadata — an attacker with root access or clock control able to
  forge `ctime` alongside `mtime`, or a filesystem without a reliable
  ctime, would not be caught until the next `--force` run. This is a
  deliberate, bounded trade-off between integrity assurance and the cost
  of hashing large files repeatedly; operators with a stronger
  requirement should run `--force` on their own schedule.
- **`omes health ai-privacy`'s `model_artifact_provenance` field is a
  cheap, hashing-free summary** of the already-recorded
  `model-artifact:*` provenance records (`artifacts.py`'s
  `summarize()`) — it only stats/reads the small JSON records already on
  disk, never the artifact bytes themselves, so the health check stays
  fast even when the declared artifacts are multi-gigabyte files. Under
  a declared `restricted_local_only` posture, missing evidence for a
  currently-local destination is `BLOCKED` (fail-closed, matching the
  #215 local-only-posture-source pattern); otherwise it is `WARN`. A
  reported checksum mismatch or missing artifact is always `FAIL`,
  regardless of declared posture. Evidence older than 7 days (the
  oldest declared artifact's last recorded verification) is `WARN`
  (`stale`) rather than trusted indefinitely — see
  `docs/ai-data-privacy-and-model-security.md` sections 10-11.
- **Signature verification (e.g. sigstore/minisign) is evaluated but not
  implemented.** Most local model-weight formats (GGUF, safetensors,
  etc.) have no widely-adopted upstream signature to verify against
  today, unlike an apt package or a signed release binary; SHA-256
  digest pinning was chosen as the primary, lower-complexity control,
  layered on this repository's existing checksum model rather than
  introducing a new verification toolchain and trust-root management
  problem. If a future upstream model distribution channel publishes
  signatures, this should be added as an additional, optional layer —
  not a replacement for digest pinning — via a reviewed ADR.
- This audit does NOT verify a separately-managed local inference
  server's own process identity, non-root posture, or network binding —
  that is `modules/hermes-restricted/module.sh`'s scope (issue #215),
  not this one.

## 2. `omes audit provenance [--profile <name>] [--json]`

Reads every `<state-dir>/provenance/*.json` record and reports:

- **Checksum mismatch → FAIL, fail-closed.** If a record's
  `checksum.expected` and `checksum.actual` are both set and differ,
  this is reported as a `FAIL` finding and the audit's overall `ok` is
  `false` (process exit 7). This never silently downgrades to a warning.
- **Unverified checksum status → WARN.** A `checksum.status` other than
  `verified`, `pinned`, or `locally-built` produces a `WARN` finding.
- **Mutable installer-source reference → WARN.** An
  `installer_source_url` containing `/main/`, `/master/`, `/latest/`,
  `@latest`, or `/HEAD/` is flagged, since such a reference can change
  behind the operator's back after the fact.
- **Missing required metadata → WARN** (or **FAIL** if the record file
  itself could not be read/parsed at all). Required fields: `component`,
  `installer_source_url`, `resolved_version`, `install_time`.
- **Managed executable review (never executed).** Lists every
  executable file found under `$HERMES_HOME/{skills,plugins,mcp}/**`
  with its mode, size, and sha256, for manual review. This audit NEVER
  runs a file it discovers — it only calls `os.stat`/reads bytes to hash
  them.

Exit codes: **0** clean (no findings), **7** findings present.

Example:

```
$ omes audit provenance --json
{"ok": false, "profile": null, "components": [...], "findings": [{"component": "hermes", "severity": "FAIL", "kind": "checksum_mismatch", "detail": "..."}], "review": [...]}
```

## 3. Trusted Baselines and Pinning: `OMES_HERMES_INSTALLER_SHA256`

For known OMES-supported Hermes releases, installer verification is **enabled by default** (issue #170).
OMES automatically resolves the expected digest from trusted repository metadata (`lib/omes/versions.sh`) without requiring manual operator configuration:
- For upstream Hermes release **v2026.9.14** (Hermes Agent v0.21.3), the verified installer SHA-256 is:
  `00f9080c6452bf87f03ef2fffb4b2c23b9f43f946aaae956e4c547d17e310b22`.

Operators can override the expected hash by setting `OMES_HERMES_INSTALLER_SHA256` before `omes install`/`omes update`
(`docs/hermes-integration.md` §2, `docs/security.md` §6). A mismatch aborts the install with **no execution** (exit 6)
and records fail-closed provenance with `checksum.status="mismatch"`.

Unmapped baselines fail closed by default. An explicit development override `OMES_HERMES_ALLOW_UNVERIFIED_INSTALLER=1`
is required to execute an unmapped baseline without verification, recording provenance as `unverified`, which
`omes audit provenance` surfaces as a `WARN` finding, never silently.

## 4. Remediation

- **Checksum mismatch (FAIL):** do not re-run the install with the same
  pin. Verify the pin value against a trusted source, or re-download the
  installer over a connection you trust and recompute
  `OMES_HERMES_INSTALLER_SHA256` yourself before retrying.
- **Unverified checksum (WARN):** set `OMES_HERMES_INSTALLER_SHA256` once
  you have a trusted hash to pin against (for v2026.9.14, use
  `00f9080c6452bf87f03ef2fffb4b2c23b9f43f946aaae956e4c547d17e310b22`);
  upstream Hermes installer script is verified periodically per release
  (`docs/hermes-integration.md` §2).
- **Mutable URL (WARN):** prefer an immutable release asset or pinned
  commit SHA where the upstream project offers one.
- **Executable review list:** inspect the listed files (mode/size/sha256)
  before trusting a skill/plugin/MCP server; this audit deliberately
  never runs them for you.

## 5. What this audit does NOT do

- It does not execute, sandbox-run, or statically analyze the content of
  any discovered file — only stat/hash.
- It does not verify a package's contents against an upstream registry
  (e.g. it does not re-download and re-hash an apt package); apt/uv/pipx
  provenance recorded here (see §1a) is exactly what the local package
  manager itself reports (`dpkg-query`, `apt-cache policy`, `uv tool
  list`, `pipx list --json`) — never a re-verification against the
  upstream registry. npm-managed component provenance is **not
  implemented yet** (tracked in #84's remaining scope, if ever needed).
- It is not a replacement for `scripts/check-supply-chain.sh` (CI-time
  checks) or `docs/security.md` §6's supply-chain rules — it is a
  runtime, host-side, diagnostic layer on top of them.
- §1b's model/runtime artifact verification does not implement signature
  verification (e.g. sigstore/minisign) — see §1b's residual-limit note —
  and does not verify a separately-managed local inference server's own
  process identity or non-root posture (that is issue #215's scope).

## 6. Release-scoped evidence bundles (issue #173, ADR-0010)

For each published release, OMES generates and verifies a complete, reproducible evidence bundle (`dist/release-v<version>/`):

1. **`release-manifest.json`**: Conforms to `contracts/provenance/v1/release-manifest.schema.json`. Records version, tag, exact commit SHA, release date, repository URL, and an artifact inventory table.
2. **`sbom.json`**: Machine-readable Software Bill of Materials distinguishing managed distributions (OMES source, Hermes `v2026.9.14`, Omarchy `v4.0.4`, Graphify `0.9.64`) from discovered host packages.
3. **`provenance.slsa.json`**: SLSA/in-toto compatible build provenance attestation binding the release tag, commit SHA, build definition, and resolved upstream dependencies.
4. **`compatibility-evidence.json`**: Platform compatibility matrix evidence for Tier 1 (Ubuntu 24.04/26.04, Linux Mint 22) and Tier 2. Enforces fail-closed evaluation: missing evidence cannot be rendered as `PASS`.
5. **`recovery-evidence.json`**: Rollback, backup/restore, and disaster recovery gate statuses (`PASS`, `FAIL`, `WARN`, `BLOCKED`, `NOT TESTED`).
6. **`security-checks.json`**: Verifies `gitleaks` secret scan, `scripts/check-supply-chain.sh`, `scripts/check-contracts.py`, and `scripts/check-architecture.py` gate passes.
7. **`limitations.json`**: Codifies known operational limitations, platform caveats, and staged feature boundaries.
8. **`SHA256SUMS`**: SHA-256 cryptographic hashes for all evidence bundle files.

### 6.1 Generation and verification CLI

```bash
# Generate evidence bundle
python3 scripts/generate-release-bundle.py \
  --version 0.3.0 \
  --tag v0.3.0 \
  --commit <40-char-sha> \
  --output dist/release-v0.3.0

# Verify evidence bundle integrity and tamper detection
python3 scripts/verify-release-bundle.py \
  --bundle-dir dist/release-v0.3.0 \
  --version 0.3.0 \
  --commit <40-char-sha>
```

`scripts/release.sh` integrates this generation and verification automatically prior to tagging and publishing.

## 7. Related

- [docs/compatibility-evidence.md](compatibility-evidence.md) — the related runtime
  version/compatibility evidence report (issue #83).
- [docs/hermes-integration.md](hermes-integration.md) §2 — the Hermes installer's
  download-to-file, optional-sha256-pin contract this provenance record
  is attached to.
- [docs/security.md](security.md) §6 — supply-chain rules.
- [docs/threat-model.md](threat-model.md) T15, T41, AI-06 — the threats this control mitigates.
- [docs/ai-data-privacy-and-model-security.md](ai-data-privacy-and-model-security.md) section 10 —
  §1b's model/runtime artifact provenance is one input to the Restricted local-only posture table.
- [ADR-0010](adr/0010-versioning-and-change-fragments.md), [ADR-0017](adr/0017-upstream-first-ownership-and-boundary-enforcement.md), and [ADR-0029](adr/0029-ai-data-boundary-and-private-inference.md).
