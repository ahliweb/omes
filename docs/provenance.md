# Supply-chain provenance (issue #84)

> Status: implemented. Covers `lib/omes/py/provenance/{record,audit}.py`,
> the bash helper `provenance_record_component` and `omes audit
> provenance` in `lib/omes/cmd/audit-provenance.sh`, and
> `modules/hermes/module.sh`'s provenance recording at install.
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
`OMES_HERMES_INSTALLER_SHA256` is unset), `unknown` (audit-time state,
e.g. an unparseable record), or `locally-built` (no remote checksum
concept applies, e.g. a component built from local source).

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

## 4. Remediation

- **Checksum mismatch (FAIL):** do not re-run the install with the same
  pin. Verify the pin value against a trusted source, or re-download the
  installer over a connection you trust and recompute
  `OMES_HERMES_INSTALLER_SHA256` yourself before retrying.
- **Unverified checksum (WARN):** set `OMES_HERMES_INSTALLER_SHA256` once
  you have a trusted hash to pin against; upstream Hermes does not
  currently publish a stable release hash (`docs/hermes-integration.md`
  §2).
- **Mutable URL (WARN):** prefer an immutable release asset or pinned
  commit SHA where the upstream project offers one.
- **Executable review list:** inspect the listed files (mode/size/sha256)
  before trusting a skill/plugin/MCP server; this audit deliberately
  never runs them for you.

## 5. What this audit does NOT do

- It does not execute, sandbox-run, or statically analyze the content of
  any discovered file — only stat/hash.
- It does not verify a package's contents against an upstream registry
  (e.g. it does not re-download and re-hash an apt package or an npm
  global); apt/uv/pipx/npm provenance recorded here is what the local
  package manager itself reports (`dpkg-query`, `apt-cache policy`, `uv
  tool list`, `pipx list`, npm global package **names only** — never
  npm package contents).
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
