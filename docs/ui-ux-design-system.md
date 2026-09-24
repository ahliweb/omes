# OMES Control Center — UI/UX Design System and Screen Architecture

> Interactive prototype: [`ui/control-center/index.html`](../ui/control-center/index.html) — a
> reference-only, presentation prototype in this repository, not a shipping interface. Its
> example data is **generated**, not hand-typed, by `scripts/generate-control-center-data.py`
> from [contracts/control-center/v1](../contracts/control-center/v1) fixtures (see §8); the
> committed `ui/control-center/data.js` is freshness-checked in CI.
> Target epics and issues: [#195](https://github.com/ahliweb/omes/issues/195)–[#202](https://github.com/ahliweb/omes/issues/202), [#192](https://github.com/ahliweb/omes/issues/192), [#183](https://github.com/ahliweb/omes/issues/183), [#211](https://github.com/ahliweb/omes/issues/211).
>
> **Implementation status.** Eight `/admin/omes/*` screens are implemented in `ahliweb/awcms` under [#200](https://github.com/ahliweb/omes/issues/200) and [#201](https://github.com/ahliweb/omes/issues/201); the `omes_control` module is `active` there. A dedicated enrollment-token management screen (Screen 9 here) is not among them — Servers renders read-only enrollment/trust evidence instead. Merge evidence: [control-center-and-integrations.md](control-center-and-integrations.md) §11.1 and [control-center-release-closeout.md](control-center-release-closeout.md).
>
> The redesign v2 of this in-repository prototype, driven by v1 contract fixtures, **shipped** in commit `0820e6e` (PR [#212](https://github.com/ahliweb/omes/pull/212), closing [#211](https://github.com/ahliweb/omes/issues/211)): generated fixture-backed data, a `live` Hermes orchestration-tree screen, and WCAG-AA contrast/focus-visible/keyboard-reachability fixes. Everything below describes the v2 prototype now on `main`.

---

## 1. Executive Summary and Purpose

The **OMES Control Center** is an operator- and customer-facing multi-tenant management interface built on top of the AWCMS foundation (`ahliweb/awcms`). It provides visibility, policy control, and safe mutation over host fleets, Hermes runtime deployments, allowlisted background jobs, disaster recovery, and pull workers.

This document formalizes the canonical UI/UX design tokens, interaction models, component primitives, and screen architecture derived from the interactive prototype in [`ui/control-center/index.html`](../ui/control-center/index.html).

### Authority Boundaries (ADR-0011, ADR-0017)
The Control Center respects the non-negotiable repository boundaries:
- **OMES** owns host compatibility, preflight checks, lifecycle execution, system hardening, backup/restore, provenance, and host health aggregation.
- **Hermes Agent** owns reasoning, messaging channels, sessions, memory, skills, and model routing. The Control Center visualizes supported Hermes signals; it never implements a second Hermes runtime.
- **AWCMS** owns multi-tenant billing, permissions, subscription states, and web admin layout.
- **Web UI is NOT an authority**: UI state switching is an ergonomic convenience; all mutations are strictly validated against JSON contracts and authorized server-side.

---

## 2. Design Tokens and Aesthetics

The design system employs a modern, dark-first aesthetic tailored for system administration and telemetry, ensuring high data density without visual fatigue.

### 2.1 Color Palette

```text
Canvas & Panels:
  Canvas Background:   #0E1216  (Deep charcoal canvas)
  Panel / Card Base:   #151A20  (Elevated panel surface)
  Panel Hover / Alt:   #1A2027  (Interactive hover surface)
  Sidebar Background:  #11161B  (Grounded navigation surface)

Borders & Dividers:
  Primary Border:      #242C35  (Card outlines and table gridlines)
  Subtle Divider:      #1E262E  (Section dividers and secondary lines)

Text & Content Ink:
  Text Primary:        #E6EDF3  (High contrast readable text)
  Text Muted:          #8B99A6  (Secondary descriptions and timestamps)
  Text Dim:            #7A8894  (Tertiary captions and subtle labels)

Semantic Accents:
  Primary Cyan:        #5FC8D6  (OMES brand, primary CTA, active navigation)
  Healthy Green:       #6FD08C  (Passed checks, healthy nodes, success state)
  Warning Amber:       #E8B44A  (Drift detected, approval pending, high load)
  Danger Red:          #E86D5B  (Host stale, service failed, error state)
  Control Plane Violet:#8B9CF7  (AWCMS modules, workflow gates, job queues)
```

### 2.2 Typography
- **Primary Body and UI**: `'Public Sans', system-ui, sans-serif` (weights 400, 500, 600, 700). High legibility at small sizes.
- **Monospace / Code / Telemetry**: `'JetBrains Mono', ui-monospace, monospace` (weights 400, 500, 600). Used for hostnames, IP addresses, correlation IDs, SHA-256 digests, versions, and CLI snippets.

### 2.3 Micro-Animations and Visual Dynamics
All transitions utilize performant GPU-accelerated CSS properties (`opacity`, `transform`):
- `omesPulse`: Subtle breathing pulse for active background operations and live heartbeat indicators.
- `omesSlide`: Smooth vertical entrance (translateY: 6px → 0) for opened panels, modals, and tab switches.
- `omesTravel` & `omesFlowLine`: Animated dashed paths indicating active data transfer and queue processing.
- `omesShimmer`: Lightweight shimmer effect indicating active preflight and telemetry collection.

---

## 3. Two-Role RBAC Model (`Owner` vs `Operator`)

The Control Center enforces a strict dual-role interaction model reflected in the UI shell:

| Role | Permissions & UI Affordances | Restrictions |
|---|---|---|
| **Operator** | - View all fleet telemetry, drift status, and job logs<br>- Trigger read-only preflight checks (`omes check`)<br>- Inspect proposed changes (`omes diff`)<br>- Submit operation proposals into the job queue | - Cannot execute destructive mutations<br>- Cannot approve firewall changes or database rollbacks<br>- Cannot access or export root secrets or master keys |
| **Owner** | - Full administrative privileges<br>- Approve or reject queued operational jobs<br>- Authorize destructive actions (restore, rollback, firewall modify)<br>- Manage pull-worker enrollment tokens and tenant quotas | - All actions remain subject to immutable audit logging with correlation IDs |

### UI Role Switcher
Located at the base of the navigation sidebar, the role selector provides immediate visual feedback of active role permissions:
- `Owner`: highlighted with cyan accent border and approval badges.
- `Operator`: subdued indicator reminding the user that mutations require approval.

---

## 4. Navigation and Global Shell

### 4.1 Sticky Navigation Sidebar
Width: `232px`, persistent on desktop displays:
1. **Brand Header**: OMES icon with cyan gradient badge (`o`), title, and environment indicator.
2. **Primary Navigation Links**: 9 primary screens with distinct monospace glyphs and active item highlights.
3. **Badge Counters**: Live notification count for queued approvals (e.g. `Operations [3]`, `Workers [1]`).
4. **Role Selector & Session Notes**: Quick toggle between Owner and Operator view states.

### 4.2 Quick Operation Drawer / Command Palette (`Cmd+K`)
An accessible, keyboard-invocable modal drawer providing instant access to allowlisted operations:
- Supported commands: `preflight`, `check`, `diff`, `apply`, `verify`, `doctor`, `backup`, `restore`, `rollback`.
- Target selector: filter by host ID, region, or deployment profile.
- Idempotency preview: displays generated idempotency key and correlation ID before submission.

---

## 5. Screen Inventory and Specifications

The Control Center comprises **9 core operational screens** and **3 diagnostic companion views**.

### Screen 1: Overview / Fleet
- **Backlog**: Issue [#200](https://github.com/ahliweb/omes/issues/200), [README.md](../README.md).
- **Core Widgets**:
  - **4 Top KPI Cards**: Total Hosts (e.g., 12), Drifted Hosts (e.g., 3), Unhealthy/Stale Hosts (e.g., 1), Queued Jobs (e.g., 3).
  - **Lifecycle Flow Visualizer**: Graphical state progression demonstrating the mandatory OMES lifecycle:
    $$\text{Bootstrap} \longrightarrow \text{Check} \longrightarrow \text{Diff} \longrightarrow \text{Apply} \longrightarrow \text{Verify} \longrightarrow \text{Rollback}$$
  - **Fleet Table**: Risk-sorted host inventory showing Server ID, Hostname, OS platform, Assigned Role, Status Badge (`Sehat`, `Drift`, `Perhatian`, `Stale`), Module Count, CPU/Memory/Disk utilization bars, Uptime, Agent Version, and Last Seen heartbeat.

### Screen 2: Servers & Drift
- **Backlog**: Issue [#200](https://github.com/ahliweb/omes/issues/200), [#197](https://github.com/ahliweb/omes/issues/197).
- **Core Widgets**:
  - **Host Detail Card**: Kernel version, architecture (`x86_64`), systemd init state, and storage mount points.
  - **Module Drift Breakdown**: Per-module desired vs. observed state comparison (`apt`, `systemd`, `ufw`, `caddy`, `php-fpm`, `postgres`).
  - **Transport Status**: Outbound mTLS pull-worker heartbeat latency and jitter.

### Screen 3: Deployments
- **Backlog**: Issue [#200](https://github.com/ahliweb/omes/issues/200), [#174](https://github.com/ahliweb/omes/issues/174), [#177](https://github.com/ahliweb/omes/issues/177).
- **Core Widgets**:
  - **Active Deployments List**: Environment classification (`production`, `staging`, `lab`).
  - **Runtime Backend Indicators**: Clear badges distinguishing `systemd` native units from rootless `compose` containers.
  - **Topology Classification**: `shared` (multi-profile efficiency under s6 supervision) vs `dedicated` (isolated container per agent with dedicated resource quotas).
  - **Contract Version**: `v1` (AgentDeployment) vs `v2` (RuntimeDeployment).

### Screen 4: Operations & Jobs
- **Backlog**: Issue [#198](https://github.com/ahliweb/omes/issues/198), [#200](https://github.com/ahliweb/omes/issues/200).
- **Core Widgets**:
  - **Job Queue Table**: Filterable by status (`queued`, `planning`, `approval_required`, `applying`, `succeeded`, `failed`).
  - **Job Execution Card**: Operation name, tenant scope, correlation ID, idempotency key, target server, execution timer.
  - **Approval Surface**: Visual gate requiring Owner signature for destructive operations before triggering host execution.

### Screen 5: Hermes Orchestration
- **Backlog**: Issue [#183](https://github.com/ahliweb/omes/issues/183).
- **Core Widgets**:
  - **Gateway Status**: Multi-profile gateway health, port binds, active LLM provider routing (e.g. Ollama local vs upstream APIs).
  - **Subagent Activity Swimlane**: Visual breakdown of delegated subagents (`planner`, `sa-01` to `sa-04`), tool allowlist enforcement, and remaining step budget (e.g. 19/40 steps).
  - **Execution Audit Stream**: Real-time log of non-sensitive tool calls and delegated subtasks.

### Screen 6: Health / Doctor
- **Backlog**: Issue [#201](https://github.com/ahliweb/omes/issues/201), [#178](https://github.com/ahliweb/omes/issues/178).
- **Core Widgets**:
  - **Two-Layer Health View**:
    - *Layer 1 (Host & Service)*: Owned by `authority: omes-host` (systemd unit state, container runtime, disk/mem thresholds, unauthenticated listener exposure).
    - *Layer 2 (Runtime & Diagnostics)*: Owned by `authority: hermes` (`hermes doctor` self-check, gateway status).
    - *Layer 3 (Provider & Channel)*: Owned by `authority: external-provider` (Ollama model readiness, Telegram token status with `compatibility_fallback`).
  - **Remediation Advisor**: Displays clear remediation guidance when a check fails; never replaces failures with guessed healthy states.

### Screen 7: Backup & Restore
- **Backlog**: Issue [#201](https://github.com/ahliweb/omes/issues/201), [#176](https://github.com/ahliweb/omes/issues/176).
- **Core Widgets**:
  - **Recovery Class Selector**:
    - `portable-profile`: Agent profiles, configuration, sessions, and memory (safe for export).
    - `full-runtime-dr`: Comprehensive disaster recovery snapshot for full restore.
    - `omes-host`: Managed host configurations, systemd units, and rollback state files.
  - **Integrity Verification**: SHA-256 checksum status, snapshot age, retention pruning indicators, and restore test history.

### Screen 8: Audit Log
- **Backlog**: Issue [#196](https://github.com/ahliweb/omes/issues/196), [#201](https://github.com/ahliweb/omes/issues/201).
- **Core Widgets**:
  - **Append-Only Event Stream**: Tenant-isolated log governed by Postgres Row Level Security (RLS).
  - **Event Metadata**: Timestamp, Actor identity, Role (`owner` / `operator`), Operation name, Resource target, Correlation ID.
  - **Structured Diff Viewer**: Side-by-side inspectable hunk view showing before/after configuration state.

### Screen 9: Worker Enrollment
- **Backlog**: Issue [#199](https://github.com/ahliweb/omes/issues/199), [#192](https://github.com/ahliweb/omes/issues/192).
- **Core Widgets**:
  - **One-Command Bootstrap Helper**: Single-use token enrollment snippet:
    ```bash
    omes worker enroll --token <one-time-token> --server https://control.ahliweb.net
    ```
  - **Worker Telemetry**: Polling interval (e.g. 15s), jitter, mTLS certificate expiry, last seen timestamp.
  - **Zero-Inbound Security Verification**: Confirms that target host has no inbound ports open to the control plane.

---

## 6. Companion Diagnostic Views

In addition to the 9 operational screens, the prototype provides 3 dedicated diagnostic views accessible via sidebar navigation:
1. **Orkestrasi Langsung (`live`)**: Real-time visual activity map showing concurrent planner subagents, step budgets, and security toolset filtering.
2. **Arsitektur Sistem (`arch`)**: Interactive 6-layer architecture stack (`01 Owner & Operator` → `02 AWCMS Control Plane` → `03 OMES v1 Contracts` → `04 Pull-Worker Transport` → `05 OMES Host CLI` → `06 Providers & Modules`).
3. **Progres Integrasi (`progress`)**: Roadmap and milestone progress tracking against open and closed GitHub issues in `ahliweb/omes`.

---

## 7. Security and Implementation Rules

1. **Client-Side Sanitization**: UI templates must never receive or render raw secrets, private tokens, passwords, or decrypted sensitive documents. Pointers must use `secret_reference` patterns.
2. **Approval Enforcement**: The UI must prevent execution of destructive operations without explicit confirmation from an authenticated `Owner` role.
3. **Fail-Closed Presentation**: If telemetry data is stale, corrupted, or unreachable, the UI must render an amber warning or red failure badge; it must never assume or fabricate a green healthy state.
4. **Standard Admin Shell Reuse**: All screens implemented in `ahliweb/awcms` must reuse existing admin components (`AdminLayout`, `admin-screens.css`, `status-badge`, `stat-grid`, `data-table`) rather than introducing an unvetted third-party CSS/JS framework.

---

## 8. Data Provenance (issue #211)

The prototype's domain data (fleet, deployments, jobs/operations, health checks, backups, audit
events, workers, and the Hermes orchestration tree/events) is **not** hand-typed in
`ui/control-center/index.html`. It is generated into `ui/control-center/data.js`
(`window.OMES_CC_DATA`, loaded before the prototype's own script runs) by
[`scripts/generate-control-center-data.py`](../scripts/generate-control-center-data.py) from:

1. `contracts/control-center/v1/fixtures/*/valid-*.json` — the same fixtures
   `scripts/check-contracts.py` validates against their JSON Schemas.
2. `ui/control-center/sample-fleet.json` — a small, clearly-labelled supplementary sample for
   fleet-level telemetry (CPU/memory/disk, OS, agent version, uptime) that has no v1 contract
   fixture yet.

The Progress screen's milestone/issue roadmap snapshot has no contract fixture at all (it
describes GitHub issue backlog state, not a runtime contract), so it is kept as a small constant
table inside that script instead. Generation is deterministic (sorted fixture paths, sorted JSON
keys, no wall-clock timestamp); `--check` fails the build if a fixture changes without
regenerating `data.js` (wired into `tests/run.sh`, `scripts/lint.sh`, and the
`check-control-center-data` CI job — see [docs/ci.md](ci.md) and [docs/testing.md](testing.md)).
See [`ui/control-center/README.md`](../ui/control-center/README.md) for the exact regenerate
command.

## 9. Accessibility (issue #211)

- **Contrast**: every body/label text token in the palette in §2.1 (`#7A8894` and lighter against
  the canvas/panel backgrounds in that same palette) meets or exceeds WCAG AA (4.5:1) for normal
  text. This is verified programmatically — not just checked once by hand — by
  [`tests/py/control_center/test_color_contrast.py`](../tests/py/control_center/test_color_contrast.py),
  which parses the §2.1 palette hex values straight out of this document and computes the WCAG 2.x
  relative-luminance contrast ratio for every text-on-background pair the design uses; it runs as
  part of `tests/run.sh` (`python3 -m unittest discover -s tests/py -t .`). The tightest pair,
  Text Dim `#7A8894` on Panel Hover/Alt `#1A2027`, is `~4.51:1` — a narrow margin above the `4.5:1`
  threshold. A dimmer token (e.g. something around `#4E5A66` on `#0B0F13`) would fail that test and
  must not be introduced without it passing.
- **Keyboard focus**: `:focus-visible` renders a visible cyan outline on every interactive
  element (nav buttons, role switcher, table rows, drawer controls).
- **Keyboard reachability**: navigation items and clickable fleet/server rows are native
  `<button>` elements (not click-only `<div>`s), so they are reachable and activatable via Tab
  and Enter/Space without a custom key-handling layer.
- **Reduced motion**: `@media (prefers-reduced-motion: reduce)` collapses all `omes*` animation
  and transition durations to effectively zero for users who ask for it at the OS/browser level.
- **Document metadata**: `<html lang="id">` (the prototype's UI copy is Indonesian) and a `<title>` are both present.
- **Dialog/status semantics**: the operation drawer is a labelled modal dialog with a keyboard-focusable surface, and the polling status is exposed through a polite live region. The reference prototype does not yet implement an interactive ARIA tree or live event stream; that remains part of the functional Control Center work tracked by #198/#201.
