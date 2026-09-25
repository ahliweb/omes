# ADR-0020: Delegate Profile and Full-Runtime Backup to Hermes Native Commands

- Status: Accepted
- Date: 2026-09-21
- Related issues: [#176](https://github.com/ahliweb/omes/issues/176), [#175](https://github.com/ahliweb/omes/issues/175), [#177](https://github.com/ahliweb/omes/issues/177), [#82](https://github.com/ahliweb/omes/issues/82)

## Context

In upstream Hermes Agent (`v2026.9.14`), native backup and restore capabilities are officially supported through the CLI:
- `hermes profile export <profile> --output <path>`: exports an isolated, portable profile archive containing skills, memory, configurations, and session state. Upstream intentionally excludes sensitive credentials (`.env`, `auth.json`) from this portable format.
- `hermes profile import <path> [--profile <profile>]`: imports a portable profile into the Hermes runtime.
- `hermes backup --output <path>`: exports a complete full-runtime disaster recovery archive including sessions, memory, skills, configurations, and runtime credentials.
- `hermes import <path>`: restores a full runtime disaster recovery archive.
- `hermes doctor`: provides native runtime health diagnostics across dependencies, tools, databases, and memory backends.

Previously, OMES implemented custom tarball assembly in `lib/omes/py/hermesbackup/` (ADR-0013 / issue #82), slicing Hermes data into arbitrary data classes (`config`, `skills`, `memory`, `sessions`, `runtime-state`, `secrets`) and building ad-hoc tarballs (`archive.tar` and `MANIFEST`). This bespoke implementation duplicated upstream functionality, risked breaking on internal database schema migrations or state file layout changes, and did not integrate with native upstream validation.

## Evaluation of Architectural Options

### Option 1: Retain custom tarball assembly and slice-based data classes in OMES
- **Pros**: Maintains existing Python tarball assembly routines.
- **Cons**: High capability duplication violating ADR-0017 (`DELEGATE -> PORT -> ADAPT -> DEFER -> REJECT`); risks silent corruption if Hermes changes internal session or cache representations; requires continual reverse-engineering of Hermes file layouts.
- **Security**: Fragile; manually manages credentials without upstream format separation.
- **Maintainability**: Poor; high long-term maintenance overhead.

### Option 2: Wrap Hermes CLI commands with destructive direct file replacement
- **Pros**: Delegates execution to upstream commands.
- **Cons**: Restoring directly into `$HERMES_HOME` without pre-restore snapshots or integrity verification causes unrecoverable data loss on corrupt archives or failed imports.

### Option 3: Upstream-First Delegation with Recovery Classes, Integrity Hashing, and Health Gates (Chosen)
- **Pros**:
  - **Crisp Authority Separation**: Upstream Hermes owns archive format specification, serialization, profile isolation, and runtime restoration (`hermes profile export/import`, `hermes backup/import`). OMES owns lifecycle orchestration, retention pruning, pre-restore protection, credential safety enforcement, and SHA-256 integrity verification.
  - **Standard Recovery Classes**:
    1. `portable-profile` (default): maps to `hermes profile export/import`, safe for sharing across hosts and development environments; clean of credentials by upstream design.
    2. `full-runtime-dr`: maps to `hermes backup/import` for complete disaster recovery.
    3. `omes-host`: preserves host-level and managed-path backups.
  - **Sensitive Credential Protection**: Full runtime backups contain credentials by upstream design. OMES strictly requires explicit opt-in (`--allow-sensitive-credentials` / `--include-credentials`), enforces restrictive file mode `0600`, and rejects restore without confirmation.
  - **Cryptographic Integrity**: Computes SHA-256 checksum at creation and verifies it before mutating live files or delegating to import.
  - **Safety Boundaries**: Always creates an automatic pre-restore recovery point prior to live mutation; executes `hermes doctor` post-restore to verify runtime health.
  - **Backward Compatibility**: Inspects session archives and seamlessly detects legacy OMES tarballs, preserving full read, verify, and restore compatibility.
- **Evaluation Criteria**:
  - **Advantages and Disadvantages**: Aligns with official Hermes backup topology while adding host-level safety guards; requires learning recovery class terminology.
  - **Security**: Strongest posture; prevents accidental credential leakage by default, isolates sensitive DR archives, and strictly rejects unconfirmed restoration.
  - **Performance**: High efficiency; leverages native upstream streaming tar/gzip creation.
  - **Maintainability**: Low maintenance; eliminates custom file-slicing logic and stays automatically compatible with upstream Hermes database updates.
  - **Scalability**: Seamlessly supports multi-profile setups via `--profile <name>`.
  - **Accessibility & UI/UX**: Machine-readable JSON output and clear human-readable inventory reports showing format, recovery class, profile, and sensitivity.
  - **SEO & Web Impact**: Enables consistent backup inventory telemetry in the Web Control Center.
  - **Compatibility**: Compatible with both modern native backups and legacy OMES archives across Ubuntu 24.04/26.04 and Linux Mint.
  - **Operational Complexity**: Low; intuitive CLI options with clear safety warnings.
  - **Long-term Technical Implications**: Unifies backup architecture under upstream standards and unblocks native Docker topology alignment (issue #177).

## Decision

1. **Upstream Command Delegation**:
   - `portable-profile` backups delegate to `hermes profile export <profile> --output <path>` and restore via `hermes profile import <path>`.
   - `full-runtime-dr` backups delegate to `hermes backup --output <path>` and restore via `hermes import <path>`.
2. **Safety Enforcement**:
   - `full-runtime-dr` backup and restore require explicit operator opt-in (`--allow-sensitive-credentials`).
   - Archive files containing credentials are set to mode `0600`.
   - Pre-restore recovery points are taken before modifying `$HERMES_HOME`.
   - SHA-256 checksums are verified before restore; mismatches abort immediately.
   - Post-restore runs `hermes doctor` to verify health.
3. **Legacy Archive Compatibility**:
   - Existing backups without `format: native-*` are classified as `format: legacy-omes` and remain fully verifiable and restorable through the legacy tarball extractor.
4. **CLI & Inventory**:
   - `omes agent-backup create` defaults to `--recovery-class portable-profile`.
   - `omes agent-backup list` and `omes agent-backup inventory` report session format, recovery class, profile, and sensitivity status.

## Amendment (2026-09-25, issue #235)

`portable-profile` and `full-runtime-dr` both include session state by this ADR's own design (see
"Context" above: `hermes profile export` explicitly covers "skills, memory, configurations, and
session state"; `hermes backup` is a strict superset). Per
[docs/ai-data-privacy-and-model-security.md](../ai-data-privacy-and-model-security.md) section 12,
that session state is Restricted-class prompt/session/context data that must never silently enter a
default backup. Issue #235 found that this ADR's own decision — defaulting `omes agent-backup
create` to `portable-profile` — was exactly that silent default, with no enforcement.

This amendment does not change the delegation decision above (OMES still fully delegates archive
creation for these two classes to `hermes profile export`/`hermes backup`); it adds a preflight gate
in front of it:

- `omes agent-backup create`'s no-argument default now uses the `omes-host` recovery class (its own
  `config`+`skills` classes), not `portable-profile`, and `omes agent apply`'s automatic
  pre-mutation backup step is pinned to `omes-host` explicitly for the same reason.
- Creating or restoring a `portable-profile`/`full-runtime-dr` backup (or the legacy
  `sessions`/`memory` classes) now requires an explicit `--allow-restricted-scope` opt-in, refused
  with the stable reason code `BACKUP_RESTRICTED_SCOPE_REQUIRES_OPT_IN` otherwise.
- See [docs/hermes-backup.md](../hermes-backup.md) section 3a and
  [lib/omes/py/hermesbackup/restricted_scope.py](../../lib/omes/py/hermesbackup/restricted_scope.py)
  for the full mechanism.
