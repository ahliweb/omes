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

## 3. Pinning: `OMES_HERMES_INSTALLER_SHA256`

Set `OMES_HERMES_INSTALLER_SHA256` before `omes install`/`omes update`
to pin the expected sha256 of the Hermes installer script
(`docs/hermes-integration.md` §2, `docs/security.md` §6). A mismatch
aborts the install with **no execution** (exit 6) — nothing is ever run
past a failed comparison. When unset, the installer runs with a logged
`WARN` and the resulting provenance record's `checksum.status` is
`unverified`, which `omes audit provenance` surfaces as a `WARN`
finding, never silently.

For upstream Hermes release **v2026.9.14** (Hermes Agent v0.21.3), the
verified installer SHA-256 is:
`00f9080c6452bf87f03ef2fffb4b2c23b9f43f946aaae956e4c547d17e310b22`.

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

## 6. Related

- `docs/compatibility-evidence.md` — the related runtime
  version/compatibility evidence report (issue #83).
- `docs/hermes-integration.md` §2 — the Hermes installer's
  download-to-file, optional-sha256-pin contract this provenance record
  is attached to.
- `docs/security.md` §6 — supply-chain rules.
- `docs/threat-model.md` T15, T41 — the threats this control mitigates.
