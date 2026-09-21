---
issue: 179
type: refactor
summary: move domain-heavy content workflows out of OMES core to AWCMS and Hermes boundaries (ADR-0024)
---

### Summary of changes

1. **Architectural Decision Record (ADR-0024)**:
   - Authored `docs/adr/0024-content-workflow-boundary-and-migration.md` evaluating the content workflow boundary across 11 architectural criteria.
   - Formally classified all existing `content` responsibilities across 5 standard dispositions:
     - `MOVE_TO_HERMES_ADAPTER`: reasoning, caption generation, browser automation, and chat messaging.
     - `MOVE_TO_CONTROL_PLANE`: content records, publishing intent, business approvals, tenant policies, and durable reconciliation (AWCMS).
     - `KEEP_OMES_HOST_GLUE`: local directory layout, process isolation, and export runner.
     - `COMPATIBILITY_SHIM`: `omes content` CLI commands and report export.
     - `RETIRE`: direct Telegram bot API calls and host-stored browser session cookies.
   - Registered ADR-0024 in `docs/adr/README.md` and marked ADR-0015 as superseded.
   - Updated `architecture/capabilities.json` with ADR-0024 reference and removal trigger.

2. **AWCMS Successor Export Utility (`export_awcms_v1`)**:
   - Implemented `export_awcms_v1` in `lib/omes/py/content/reports.py` generating `awcms-content-migration-manifest.json` (`schema_version: awcms-content-v1`).
   - Strictly excludes `content/sessions/` (zero cookies, tokens, or browser profiles exported).
   - Preserves SHA-256 artifact hashes, publication verification URLs, approval history, and hash-chained audit events without data loss.
   - Enforces duplicate publish protection across migrated jobs.
   - Flags partial or uncertain publications (`manual-review`, `retryable-failure`, failed platform attempts) with `requires_manual_review: true` to prevent automatic republishing in the successor.

3. **Compatibility CLI & Deprecation Notices**:
   - Added `--format {legacy,awcms-v1}` to `omes content export` in `lib/omes/py/content/cli.py`.
   - Documented explicit deprecation timeline: `omes content` remains functional in compatibility mode throughout OMES v1.x and is scheduled for retirement in OMES v2.0 upon Control Center GA.
   - Emitted deprecation notices on direct session management (`omes content session login/revoke`).

4. **Testing & Verification**:
   - Added unit test suite `tests/py/content/test_migration.py` covering:
     - Non-secret history and artifact hash preservation.
     - Strict session/cookie exclusion and recursive secret redaction.
     - Duplicate publish protection across migrated jobs.
     - Manual review enforcement on uncertain or partial publications.
     - Non-destructive and reversible export behavior.
     - Compatibility CLI export invocations with both legacy and AWCMS v1 formats.

5. **Documentation Alignment**:
   - Updated `docs/content-distribution.md`, `docs/content-threat-model.md`, `skills/content/SKILL.md`, `docs/architecture.md`, `docs/control-center-and-integrations.md`, and `AGENTS.md`.
