# Graphify privacy, provenance, and deletion (issue #55)

> Status: implemented, as described. This document is the privacy-specific companion to
> [docs/graphify.md](graphify.md) (the integration's full technical reference) — it does not
> repeat that document's install/workflow/export/sync mechanics except where needed to make the
> privacy picture self-contained. Verified 2026-09-19 against graphify `0.9.64`
> (see [ADR-0014](adr/0014-graphify-integration-boundary.md)).

## 1. Local AST extraction vs. provider-backed semantic extraction

This is the single most important distinction in this document — repeated from
[docs/graphify.md §1.6](graphify.md#16-local-vs-external-processing-boundary) because every other
section here depends on it:

| Mode | What runs | What leaves the machine |
|---|---|---|
| **Code-only / local AST** (`graphify extract --code-only`, `graphify update`, `omes graphify sync`'s only extraction paths, `omes graphify run` default) | tree-sitter AST parsing only | **Nothing.** No network call is made, no API key is read or required. |
| **Semantic** (`graphify extract` without `--code-only`, `omes graphify run --mode semantic`) | AST parsing, then a semantic pass over the code/docs/papers/images to resolve `INFERRED` edges | **The content graphify decides to send to the configured LLM backend** — see §2. |

OMES never enables semantic extraction by default, anywhere, in any command:

- `omes graphify run` defaults to `--mode code`; `--mode semantic` requires the operator to
  additionally set `OMES_GRAPHIFY_PROVIDER_ENV=<VAR_NAME>` pointing at a real, non-empty
  credential variable, AND pass `--yes` (or answer an interactive confirmation prompt that states
  "this calls an external LLM API") — [docs/graphify.md §3.1](graphify.md#31-omes-graphify-extract-and-omes-graphify-run).
- `omes graphify sync` never runs a semantic pass at all — only `extract --code-only` (first run)
  or `update` (later runs), both 100% local —
  [docs/graphify.md §6.2](graphify.md#62-omes-graphify-sync-change-detection-debounce-and-the-two-extraction-paths).
- `omes graphify export` never re-extracts anything; it only reads an already-produced
  `graph.json` and/or shells out to `graphify export obsidian` (a 100% local rendering step, per
  [docs/graphify.md §1.6](graphify.md#16-local-vs-external-processing-boundary)'s table).

**Token/cost implication** (also documented in
[docs/graphify.md §6.2](graphify.md#62-omes-graphify-sync-change-detection-debounce-and-the-two-extraction-paths)):
because `sync` has no concept of semantic caching, a semantic pass must be re-triggered
explicitly, every time, via `omes graphify run --mode semantic` — each such call is a fresh,
separately-billed request to whatever provider is configured. Running `sync` on a schedule
(§docs/graphify.md §6.6) is free and safe; scheduling a semantic re-run is a recurring cost the
operator opts into every time, never something OMES automates.

## 2. What actually leaves the machine in semantic mode

When `--mode semantic` runs (an explicit, operator-initiated action - never a default):

1. Extracted code/doc/paper/image **content** graphify's own chunking/dispatch logic selects is
   sent to whichever backend is configured (`--backend {gemini|kimi|claude|openai|deepseek|
   ollama}`, or an OpenAI/Anthropic-compatible self-hosted endpoint via `OPENAI_BASE_URL`/
   `ANTHROPIC_BASE_URL` — verified in `graphify extract --help`), except **`--backend ollama`**,
   which stays 100% local (a locally-running Ollama model, no external network call).
2. **OMES never reads, stores, or forwards the actual provider credential value.**
   `OMES_GRAPHIFY_PROVIDER_ENV` names the environment variable that holds it; `omes graphify run`
   only ever checks that the *named* variable is non-empty (via indirect parameter expansion) —
   the credential's value never appears in a log line, `--json` output, or the provenance sidecar
   (§4) — only the variable's **name** does.
3. What graphify itself decides to include in a semantic-pass request (source excerpts, doc/paper
   text, image content) is entirely upstream's own chunking/dispatch logic — OMES does not
   filter, redact, or inspect that payload before it is sent. This is why §3's ignore-file
   defaults matter: they are the only pre-send filter OMES can offer, by keeping obviously
   sensitive paths out of graphify's own scan in the first place.
4. `graphify export {neo4j,falkordb}` is a separate, always-explicit, opt-in path that pushes an
   already-extracted graph to an operator-configured graph database — outside OMES's default
   invocation path entirely ([docs/graphify.md §1.5](graphify.md#15-non-goals)).

## 3. Ignored paths: `.gitignore` and `.graphifyignore`

Re-verified 2026-09-19 (`docker run --rm python:3.12-slim bash -c 'pip install -q graphifyy && ...'`,
a live `extract --code-only` run against a synthetic two-directory repo, once with only a
`.gitignore` and once with only a `.graphifyignore`): **both mechanisms are honored by graphify
BY DEFAULT, with no flag needed** — a file matched by either is excluded from extraction. Passing
`--no-gitignore` additionally disables `.gitignore`/`.git/info/exclude` parsing specifically, "in
favor of" `.graphifyignore` (per `graphify extract --help`'s own wording) — but `.graphifyignore`
is not conditional on that flag; it is read either way. Both files use plain gitignore-style
glob syntax.

### `omes graphify init-ignore <path> [--dry-run] [--yes] [--json]`

Writes/updates `<path>/.graphifyignore` from the bundled template
(`modules/graphify/templates/.graphifyignore`) covering:

- **Secrets and credentials:** `.env`/`.env.*`, `*.pem`, `*.key`, `*.p12`/`*.pfx`, `id_rsa*`/
  `id_ed25519*`/`*_rsa`/`*_ed25519`, `credentials*.json`, `secrets.yml`/`secrets.yaml`, `.aws/`,
  `.ssh/`, `.gnupg/`.
- **Dependency/build noise:** `node_modules/`, `vendor/`, `.venv/`/`venv/`, `__pycache__/`,
  `dist/`, `build/`, `target/`.
- **Graphify's own output and the OMES-managed vault export subdirectory:** `graphify-out/`,
  `graphify/` (the default `OMES_GRAPHIFY_VAULT_SUBDIR` parent — [docs/graphify.md §5](graphify.md#5-safe-obsidian-export-omes-graphify-export)).
- **Other vaults/personal notes a repository might happen to contain:** `*.obsidian/`.

It also ensures `<path>/.gitignore` contains `graphify-out/` and `graphify/` (with a backup taken
first, exactly like every other OMES mutation — `lib/omes/backup.sh`), so a semantic pass never
accidentally re-ingests graphify's own prior output, and so neither ends up committed to the
repository by accident.

**Idempotency and safety:** both files are marked with an OMES-owned marker block
(`# BEGIN OMES graphify ignore patterns ...` / `# BEGIN OMES graphify .gitignore entries ...`,
mirroring the same marker-block convention `modules/hermes/module.sh` already uses for its own
`.bashrc`/`.profile` snippet). A file that already carries its marker is left completely
untouched on a re-run (`"action":"none"`, exit 0) — pre-existing, non-OMES content in either file
(an operator's own `.gitignore` rules, a hand-customized `.graphifyignore`) is never overwritten,
only appended to once.

**Exit codes:** 0, 1 (write failed, or confirmation declined without `--yes`), 2 (usage error:
missing `<path>`, or `<path>` does not exist / resolves inside a `graphify-out/` directory).

**JSON schema:**
`{"command":"graphify","subcommand":"init-ignore","ok":true,"action":"wrote","graphifyignore":"/abs/path/.graphifyignore","gitignore":"/abs/path/.gitignore","exit_code":0}`.

## 4. Private repos, credentials, and personal notes

- **Private repos:** OMES never uploads, clones, or transmits repository content anywhere on its
  own — every command in this integration operates on a local filesystem path the operator
  already has. The only network activity possible is the semantic-mode LLM call described in §2,
  always explicit.
- **Credentials:** never read into OMES beyond the single presence/non-empty check in §2 point 2;
  never written to the provenance sidecar (§5), a log line, `--json` output, or a backup. Secrets
  living in a scanned tree are the ignore-file's job to exclude (§3) — OMES does not scan file
  *contents* for secret patterns itself (that is a different, unimplemented concern; the default
  `.graphifyignore` only excludes secret *paths* by name/extension).
- **Personal notes / other vaults:** an Obsidian vault's own top-level `.obsidian/` directory and
  every note outside the OMES-managed export subdirectory are never read, written, or otherwise
  touched by any command in this integration — [docs/graphify.md §5](graphify.md#5-safe-obsidian-export-omes-graphify-export)'s
  export contract and its own test suite (`tests/integration/graphify-export.bats`) enforce this
  structurally, not just by convention.

## 5. Provenance record fields

Already implemented, linked here rather than re-specified:

- **`omes graphify run`'s provenance sidecar** (`<out_dir>/omes-provenance.json`) — source path,
  timestamp, tool version, extraction mode, backend (if semantic), and the provider env var
  **name** only — [docs/graphify.md §3.3](graphify.md#33-provenance-sidecar).
- **Every generated Obsidian note's YAML front matter** — `omes_generated: true`, source path,
  graphify version, extraction mode, generated-at timestamp, and the source `graph.json`'s
  sha256 — [docs/graphify.md §5.3](graphify.md#53-generated-note-front-matter-and-provenance).
- **Graphify's own `EXTRACTED`/`INFERRED` edge tagging** is never duplicated or replaced by any
  OMES-added provenance — [docs/graphify.md §1.7](graphify.md#17-provenance-model-use-graphifys-own-tags-do-not-reinvent-one).

## 6. Deletion and re-index procedure

### `omes graphify purge <path> [--vault <path>] [--dry-run] [--yes] [--json]`

Removes **only** OMES-generated artifacts, identified by marker/ownership, never anything else:

1. **`<path>/graphify-out/`** (the entire directory) is removed if present. This directory is
   100% machine-generated by graphify/OMES — it never contains user-authored content, so a
   whole-directory removal is safe without a per-file marker check.
2. **When `--vault <path>` (or `OBSIDIAN_VAULT_PATH`) is given**, every file under that export's
   managed subdirectory (`<vault>/<subdir>/`, [docs/graphify.md §5](graphify.md#5-safe-obsidian-export-omes-graphify-export))
   is classified before anything is deleted (`lib/omes/py/graphify/obsidian.py`'s
   `plan_purge_export`/`purge_export`):
   - a `.md` file is removable only if it carries the `omes_generated: true` marker (§5);
   - any other file (the canvas, a nested `.obsidian/graph.json`, the upstream/OMES export
     manifests) is removable only if it is listed in that export's own
     `.omes_export_manifest.json` bookkeeping file (the same ownership test
     [docs/graphify.md §5.4](graphify.md#54-non-markdown-output-ownership-upstream-render-path-only)
     already uses for the overwrite-refusal rule);
   - **anything else — a user-authored note, or any file this export never recognizes as its
     own — is always skipped, never deleted**, and reported separately in `--json` output
     (`"skipped"`) and as a `log_warn` line.
   - once every owned file is removed, now-empty subdirectories are cleaned up; a directory that
     still holds a kept (skipped) file is left in place.
3. **Backup before deletion:** exactly like every other OMES mutation, `purge` opens a backup
   session (`lib/omes/backup.sh`, module `graphify-purge`) covering `graphify-out/` and the export
   subdirectory (whichever are present) before removing anything — recoverable via the standard
   `omes restore` command (no dedicated `purge`-specific rollback subcommand; the generic restore
   path already covers it, verified in `tests/integration/graphify-privacy.bats`).
4. **Idempotent:** re-running `purge` after everything owned is already gone reports
   `"action":"none"` and exits 0 — it never errors on "nothing left to remove."

**Re-index procedure** (after a `purge`, or to pick up ignore-file changes): re-run
`omes graphify sync <path> --yes` (or `omes graphify run <path>`) — since the manifest and
`graphify-out/` were removed, this is treated as a first run
([docs/graphify.md §6.2](graphify.md#62-omes-graphify-sync-change-detection-debounce-and-the-two-extraction-paths)),
a fresh `graphify extract --code-only` into a new `graphify-out/`. Re-running
`omes graphify export` afterward re-populates the vault subdirectory from the fresh graph.

**Exit codes:** 0 (success, including "nothing to remove"), 1 (python3/graphify.cli invocation
failed, or confirmation declined without `--yes`), 2 (usage error: missing `<path>`, or `<path>`
does not exist / resolves inside a `graphify-out/` directory).

**JSON schema:**
`{"command":"graphify","subcommand":"purge","ok":true,"backup":"/state/backups/<ts>","graphify_out_removed":true,"export_removed":[...],"export_skipped":[...],"exit_code":0}`.

**Examples:**

```bash
omes graphify init-ignore ~/code/myrepo --yes                       # safe defaults, idempotent
omes graphify purge ~/code/myrepo --dry-run                          # preview what would be removed
omes graphify purge ~/code/myrepo --vault ~/Documents/MyVault --yes  # remove graphify-out/ + owned notes
omes graphify sync ~/code/myrepo --yes                                # re-index from scratch afterward
```

## 7. Summary table

| Concern | Control | Reference |
|---|---|---|
| Default extraction mode | Code-only, local AST, no network call | §1, docs/graphify.md §1.5 |
| Semantic opt-in | Explicit `--mode semantic` + `OMES_GRAPHIFY_PROVIDER_ENV` + confirmation | §1, §2, docs/graphify.md §3.1 |
| Credential handling | Name only, never the value; never logged/stored | §2, §5 |
| Ignored paths | `.gitignore` + `.graphifyignore`, both honored by default; `omes graphify init-ignore` ships safe defaults | §3 |
| Vault isolation | Writes only under the OMES-managed subdirectory; `.obsidian/` and unrelated notes untouched | §4, docs/graphify.md §5 |
| Provenance | Sidecar JSON + per-note YAML front matter; graphify's own EXTRACTED/INFERRED tags preserved | §5 |
| Deletion | `omes graphify purge`, marker/manifest-scoped, backed up first | §6 |
