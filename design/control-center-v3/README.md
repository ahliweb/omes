# Control Center redesign v3 — audit, screens and Hermes agent office

Reference-only design proposal for the OMES Control Center. It audits the v2
prototype in [`ui/control-center/index.html`](../../ui/control-center/index.html)
(commit `8990850`) and proposes a v3 redesign. UI copy is Indonesian, matching
the v2 prototype (`<html lang="id">`).

**Not implemented.** These files render example data only and call no API. The
functional Control Center screens live in `ahliweb/awcms` (see
[docs/control-center-release-closeout.md](../../docs/control-center-release-closeout.md));
Hermes orchestration visualization is tracked in
[#183](https://github.com/ahliweb/omes/issues/183) and governed by
[ADR-0028](../../docs/adr/0028-hermes-orchestration-visualization.md). The
Hermes screens visualize Hermes-reported signals only; they do not imply that
OMES schedules, routes or supervises Hermes agents.

## How to view

```bash
python3 -m http.server 8080   # from the repository root
# then open http://localhost:8080/design/control-center-v3/Main.dc.html
```

Each `*.dc.html` file is a self-contained Design Component page rendered by the
existing vendor runtime
[`ui/control-center/support.js`](../../ui/control-center/support.js), loaded by
relative path, so serve the repository root rather than this folder. Pages load Public Sans and JetBrains Mono from Google
Fonts; without network access they fall back to system fonts.
[`canvas.json`](canvas.json) records the artboard layout used in the design
canvas the files were authored in.

## Contents

| File | Screen | Interactive |
|---|---|---|
| [`Audit.dc.html`](Audit.dc.html) | 01 · UX audit of prototype v2: 17 findings (4 critical, 6 high, 5 medium, 2 low) with annotated screenshots | no |
| [`Main.dc.html`](Main.dc.html) | 02 · Fleet overview: "needs action" panel, risk-sorted fleet, honest KPIs | filter |
| [`Operations.dc.html`](Operations.dc.html) | 03 · Operations and approvals: plan → review → approve → run via worker → verify; typed host confirmation for critical changes; owner/operator preview | yes |
| [`Health.dc.html`](Health.dc.html) | 04 · Health grouped by authority (`omes-host`, `hermes`, `external-provider`) and host matrix that never shows stale data as healthy | no |
| [`Mobile.dc.html`](Mobile.dc.html) | 05 · On-call phone view (390×844) | links |
| [`Tokens.dc.html`](Tokens.dc.html) | 06 · Design system v3: surfaces, status vocabulary with shapes and contrast ratios, 6-step type scale, core components | no |
| [`HermesOffice.dc.html`](HermesOffice.dc.html) | 07 · Hermes agent office: animated 2D floor plan ↔ 3D isometric office, session replay of fixture events, per-agent detail (task, management chain, tool allowlist, agenda) | yes |
| [`HermesAgenda.dc.html`](HermesAgenda.dc.html) | 08 · Management structure, task board and session agenda timeline per agent | filter |
| [`AvatarKit.dc.html`](AvatarKit.dc.html) | 09 · Avatar kit and settings: agent, 3D/2D style, status, animation, sizes, usage rules | yes |
| [`Avatar.dc.html`](Avatar.dc.html) | Shared avatar component (`agent`, `state`, `look`, `size`, `animate`, `badge`) imported by screens 07–09 | — |

The audit page uses three screenshots of the v2 prototype in `assets/`
(`prototype-v2-overview.png`, `prototype-v2-servers.png`,
`prototype-v2-mobile-390.png`), and `previews/` holds a static PNG render of
each v3 screen. Animations respect
`prefers-reduced-motion`, and the office view has a pause control.

## Data provenance

- Fleet, deployments, jobs, health, backups, workers and Hermes values come from
  [`ui/control-center/data.js`](../../ui/control-center/data.js), i.e. the
  [contracts/control-center/v1](../../contracts/control-center/v1) fixtures plus
  `sample-fleet.json`. Timestamps are shown in WIB (UTC+7).
- The Hermes event `sub-code-001` has no name and no parent in the
  `hermes-orchestration-tree` fixture. The label "Analis Kode" is a design
  placeholder, and the screens show its parent as unknown rather than guessing.
- Items tagged **contoh** (example) in the agenda and task board are illustrative
  scenarios; the repository has no Hermes schedule data yet.
- Proposed rules that need team agreement: resource thresholds (≥75% attention,
  ≥90% critical) and the stale-heartbeat rule (> 3 × poll interval).

## Audit highlights

1. A "Buka SSH" button contradicts the outbound-only pull-worker transport
   ([ADR-0027](../../docs/adr/0027-control-center-pull-worker-transport.md)).
2. One-click mutations, including a critical firewall change, without plan
   review or separation of requester and approver.
3. Hard-coded counts that contradict the data (`3` vs one queued operation;
   `PASS 6 · WARN 2 · FAIL 1` vs four passing checks) and a suggested
   `omes doctor --fix --dry-run` flag that the CLI does not have.
4. Stale hosts shown with `0%` CPU/memory instead of an unknown state, and a
   backup vault at 88% disk labelled healthy.

The full list, with fixes and references (WCAG 2.2 / ISO/IEC 40500:2025,
ISO/IEC 27001:2022 Annex A, ISO/IEC 20000-1, ISO 9241), is in
[`Audit.dc.html`](Audit.dc.html).
