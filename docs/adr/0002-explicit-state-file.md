# ADR-0002: Explicit key=value state file

- Status: Accepted
- Date: 2026-09-18

## Context

OMES needs to durably record what it has done to a host: which modules are
applied, when, at what OMES version, which paths/packages/services each
module manages. This state is what makes `omes status`, `omes uninstall`,
`omes restore`, and idempotency checks possible without re-probing the
live system every time (see `docs/architecture.md` Section 6.6 for the
"why explicit state" rationale). The state file must be readable/writable
by bash without new dependencies (ADR-0001), must degrade safely if
partially written or corrupted, and must be diffable/greppable for
operators debugging by hand on a server with no extra tools installed.

Candidates considered: a flat `key=value` file, JSON parsed with `jq`, and
SQLite.

## Options considered

### Option A: `key=value` file

- Security: trivial to permission-lock (`0600`/`0700`, Section 6.5); no
  parser vulnerabilities (a `key=value` split has no injection surface
  beyond what shell quoting already requires); easy to grep for secrets-
  shaped keys during a security review.
- Performance: read/write cost is a single file read and a line-oriented
  parse — effectively free at OMES's scale (tens of modules, dozens of
  keys).
- Maintainability: no schema versioning machinery needed; adding a new key
  is additive and backward-compatible by construction (old readers simply
  ignore keys they don't know, per the parsing rule); `lib/omes/state.sh`
  is a small, easily-tested module.
- Scalability: not designed for large datasets, which OMES does not have;
  a state file with hundreds of modules across many keys is still a
  small text file.
- Compatibility: needs nothing beyond bash's own string handling (`grep`,
  parameter expansion, or a simple `while IFS='=' read` loop) — works
  identically on every target platform with zero extra tooling.
- Operational complexity: an operator can `cat`/`grep`/`sed` the state
  file by hand during an incident with tools guaranteed to exist on a
  minimal server, with no risk of corrupting a binary format.
- Long-term implications: does not support nested/structured values
  natively (hence the colon-separated-list convention for
  `managed_paths`/`installed_packages`/`services` — see
  `docs/architecture.md` Section 6.3), which is a real limitation if state
  needs to grow more structured later; but a deliberately small, flat key
  space has kept the schema stable and easy to reason about.

### Option B: JSON file, read/written via `jq`

- Security: JSON parsing itself is safe, but this option adds a hard
  dependency on `jq` being present — either it must be preflight-checked
  and installed, or OMES's own `state.sh` (which runs before any module,
  including the one that might install `jq`) would need a bootstrapping
  story for its own dependency.
- Performance: comparable to Option A at this scale; `jq` invocation
  overhead per read/write is small but nonzero and repeated across many
  small state reads during a run.
- Maintainability: JSON supports real nested structures, which would
  simplify `managed_paths` et al. from colon-separated strings to real
  arrays; genuinely nicer for anyone consuming state programmatically
  (e.g. `omes status --json`, which already exists as a *command* output
  format independent of the state file's own on-disk format).
- Scalability: fine at OMES's scale.
- Compatibility: `jq` is not guaranteed present on a minimal Ubuntu
  Server/Mint install; OMES would need to either vendor it (contradicts
  ADR-0001's zero-runtime-dependency goal) or make it a preflight-installed
  dependency, which is circular for the module that manages state itself.
- Operational complexity: an operator without `jq` on a broken host (the
  exact moment `omes restore`/`omes status` needs to work offline, per
  `docs/architecture.md` Section 11) is now dependent on a tool that may
  not be there; hand-editing JSON correctly under pressure is also more
  error-prone than editing `key=value` lines.
- Long-term implications: ties every future state read/write to `jq`'s
  continued presence and behavior; a partially-written JSON file is
  unparseable in its entirety (one syntax error invalidates the whole
  document), which is a worse corruption failure mode than a `key=value`
  file (Option A degrades to "one bad line ignored").

### Option C: SQLite database

- Security: SQLite itself is mature and safe, but a binary database file
  is opaque to `grep`/`cat`, harder to audit by eye, and its permission
  model is the same file-mode story as Option A/B with none of their
  readability.
- Performance: massive overkill for tens of key/value pairs; connection/
  transaction overhead is pure cost here.
- Maintainability: would require either a vendored SQLite CLI/library or
  a dependency on `sqlite3` being installed — again a bootstrapping
  circularity, and adds schema-migration machinery that this state
  model's size does not warrant.
- Scalability: SQLite's scalability advantages (concurrent readers,
  large datasets, complex queries) are not needed — OMES's state is a
  single-host, single-writer-at-a-time, small key set.
- Compatibility: `sqlite3` is not guaranteed present by default on either
  target platform.
- Operational complexity: a broken host's operator now needs `sqlite3` (or
  to trust OMES's own restore path exclusively) to inspect state during an
  incident — the worst offline-debuggability story of the three options,
  which directly conflicts with the "restore/status/uninstall must work
  offline" requirement.
- Long-term implications: would be justified if OMES's state model grew
  into something relational (many-to-many relationships, complex queries)
  — it has not, and the module/state design in `docs/architecture.md`
  Section 6 does not anticipate it needing to.

## Decision

Use a flat `key=value` state file (`<state-dir>/state`), one key per line,
no quoting/nesting, parsed and written exclusively through
`lib/omes/state.sh` with atomic write-temp-then-`mv` semantics (see
`docs/architecture.md` Section 6.4). Structured values (lists) are encoded
as colon-separated strings within a single key's value.

## Consequences

- `lib/omes/state.sh` is the single choke point for all state reads/
  writes; modules never open the state file directly, which keeps the
  atomicity and permission guarantees enforceable in one place.
- Colon-separated list encoding means list values must not themselves
  contain a literal colon (paths on Linux can, in theory, contain any
  byte but colon-containing paths are pathological); this is an accepted,
  documented limitation rather than a general-purpose serialization
  format.
- `omes status --json` and other `--json` outputs are free to expose a
  richer, nested JSON *view* of the same underlying flat state — the
  on-disk format and the CLI's `--json` output format are independent
  decisions; this ADR governs only the former.
- Adding a new key is backward compatible; removing or repurposing an
  existing key's meaning is a breaking change to this contract and would
  need a migration note in `CHANGELOG.md` and a version bump per
  ADR-0010.
