---
issue: 200
type: docs
summary: adopt OMES Control Center UI/UX design system and canonical 9-screen architecture
---

### Summary of changes

1. **Interactive Prototype Integration**:
   - Integrated the interactive prototype into `ui/control-center/index.html` and `ui/control-center/support.js`, establishing a runnable, client-side dynamic prototype demonstrating the 9 core screens and 3 diagnostic views.
   - Added `ui/control-center/README.md` documenting screen mappings, local viewing instructions, and authority boundaries.
2. **Normative Design System Document**:
   - Authored `docs/ui-ux-design-system.md` specifying:
     - High-density dark system palette (`#0E1216` background, `#151A20` panel base, `#5FC8D6` primary cyan, `#6FD08C` healthy, `#E8B44A` drift, `#E86D5B` danger, `#8B9CF7` control plane).
     - Typography standards (`Public Sans` body UI, `JetBrains Mono` telemetry/code).
     - Two-role RBAC interaction model (`Owner` approval gate vs `Operator` proposal).
     - Quick Operation Command Drawer (`Cmd+K`) for allowlisted operations.
     - 9-screen inventory covering fleet overview, servers/drift, deployments, operations/jobs, Hermes orchestration, health, backup, audit, and pull-worker enrollment.
     - 3 companion diagnostic views (live swimlane, architecture stack, integration progress).
     - Non-negotiable security principles (no raw secrets, fail-closed telemetry, pull-worker transport only).
3. **Architectural Decision Record (ADR-0023)**:
   - Authored `docs/adr/0023-control-center-ui-ux-design-system.md` evaluating the design system against the 11 architectural criteria.
   - Registered ADR-0023 in `docs/adr/README.md`.
4. **Integration Cross-References**:
   - Updated `docs/control-center-and-integrations.md`, `docs/web-panel-reference-evaluation.md`, and `docs/architecture.md` linking to the canonical UI/UX design baseline.
