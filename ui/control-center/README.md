# OMES Control Center — UI/UX Interactive Prototype

This directory contains the canonical interactive UI/UX prototype for the **OMES Control Center** (issues [#195](https://github.com/ahliweb/omes/issues/195)–[#202](https://github.com/ahliweb/omes/issues/202), [#192](https://github.com/ahliweb/omes/issues/192), [#183](https://github.com/ahliweb/omes/issues/183), [#211](https://github.com/ahliweb/omes/issues/211) redesign v2).

The prototype establishes the visual design system, screen architecture, role-based interaction boundaries (`Owner` vs `Operator`), and operational flows for the AWCMS-based OMES Control Center.

**This is a reference-only, presentation prototype.** The functional Control Center screens
(actual API calls, live polling, real auth) are **not implemented yet** — that work is tracked
in [#200](https://github.com/ahliweb/omes/issues/200) (admin screens) and
[#201](https://github.com/ahliweb/omes/issues/201) (health/backup/audit). `index.html` only
renders example data produced by `scripts/generate-control-center-data.py` (see below); it does
not call any OMES/AWCMS API.

## Files

- [index.html](index.html): Full interactive single-file prototype containing all 9 core screens and 3 diagnostic views (including the `live` Hermes orchestration-tree view).
- [support.js](support.js): Standalone runtime supporting dynamic state rendering and interactive role switching. Byte-identical vendor file — do not hand-edit.
- [data.js](data.js): **Generated** (`window.OMES_CC_DATA`). Regenerate with:
  ```bash
  python3 scripts/generate-control-center-data.py
  ```
  Built from [contracts/control-center/v1/fixtures/*/valid-*.json](../../contracts/control-center/v1/fixtures) (the same fixtures `scripts/check-contracts.py` validates) plus `sample-fleet.json`. `scripts/generate-control-center-data.py --check` fails if `data.js` is stale relative to those inputs; it is run by `tests/run.sh`, `scripts/lint.sh`, and the `check-control-center-data` CI job. Do not hand-edit `data.js` — edit the fixtures/`sample-fleet.json`, then regenerate.
- [sample-fleet.json](sample-fleet.json): Small, clearly-labelled **supplementary sample** for fleet telemetry (CPU/memory/disk, OS, agent version, uptime) that has no v1 contract fixture yet. See its own `_provenance` field.

## How to View

Open `ui/control-center/index.html` in any modern web browser:
```bash
# Example using Python http.server:
python3 -m http.server 8080 --directory ui/control-center
# Then visit http://localhost:8080 in your browser
```
Alternatively, open `ui/control-center/index.html` directly as a local file (`file://...`).

## Screen Inventory

| Screen | Owning Issue | Description |
|---|---|---|
| **Overview / Fleet** | [#200](https://github.com/ahliweb/omes/issues/200) | Risk-sorted host fleet, KPI summary tiles (Hosts, Drifted, Unhealthy, Queued), and lifecycle flow (`Bootstrap → Check → Diff → Apply → Verify → Rollback`). |
| **Servers & Drift** | [#200](https://github.com/ahliweb/omes/issues/200), [#197](https://github.com/ahliweb/omes/issues/197) | Host inventory, OS distribution, drift metrics, and outbound pull-worker transport status. |
| **Deployments** | [#200](https://github.com/ahliweb/omes/issues/200) | Active runtime deployments across systemd and Docker Compose topologies (`shared` vs `dedicated`). |
| **Operations & Jobs** | [#198](https://github.com/ahliweb/omes/issues/198), [#200](https://github.com/ahliweb/omes/issues/200) | Allowlisted job execution drawer (`Cmd+K`), operation queue, step progress, and approval gates. |
| **Hermes Orchestration** | [#183](https://github.com/ahliweb/omes/issues/183) | Hermes agent gateway status, multi-profile supervision, active models, and delegated subagent swimlanes. |
| **Health / Doctor** | [#201](https://github.com/ahliweb/omes/issues/201), [#178](https://github.com/ahliweb/omes/issues/178) | Layered diagnostics attributing authority (`hermes` vs `omes-host` vs `external-provider`). |
| **Backup & Restore** | [#201](https://github.com/ahliweb/omes/issues/201), [#176](https://github.com/ahliweb/omes/issues/176) | Recovery classes (`portable-profile`, `full-runtime-dr`, `omes-host`), SHA-256 verification, and retention. |
| **Audit Log** | [#196](https://github.com/ahliweb/omes/issues/196), [#201](https://github.com/ahliweb/omes/issues/201) | Append-only audit trail with tenant-scoped Row Level Security (RLS) and structured diff inspection. |
| **Worker Enrollment** | [#199](https://github.com/ahliweb/omes/issues/199), [#192](https://github.com/ahliweb/omes/issues/192) | Outbound-only pull worker enrollment with single-use bootstrap tokens. |

### Companion Diagnostic Views

- **Orkestrasi Langsung (`live`)**: Real-time planner and subagent activity timeline with step budgets.
- **Arsitektur Sistem (`arch`)**: 6-layer architecture topology visualizer.
- **Progres Integrasi (`progress`)**: Milestone progress tracking against repository backlog.

## Normative Guidelines

For the complete technical specification and token system, refer to:
- [docs/ui-ux-design-system.md](../../docs/ui-ux-design-system.md) (§8 data provenance, §9 accessibility)
- [docs/adr/0023-control-center-ui-ux-design-system.md](../../docs/adr/0023-control-center-ui-ux-design-system.md)
- [docs/adr/0028-hermes-orchestration-visualization.md](../../docs/adr/0028-hermes-orchestration-visualization.md)
- [docs/testing.md](../../docs/testing.md) (§2.6 `data.js` freshness check)
