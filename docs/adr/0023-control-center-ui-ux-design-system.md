# ADR-0023: Adopt OMES Control Center UI/UX Design System and Screen Architecture

- Status: Accepted
- Date: 2026-09-21
- Related issues: [#200](https://github.com/ahliweb/omes/issues/200), [#201](https://github.com/ahliweb/omes/issues/201), [#195](https://github.com/ahliweb/omes/issues/195), [#196](https://github.com/ahliweb/omes/issues/196), [#197](https://github.com/ahliweb/omes/issues/197), [#198](https://github.com/ahliweb/omes/issues/198), [#199](https://github.com/ahliweb/omes/issues/199), [#192](https://github.com/ahliweb/omes/issues/192), [#183](https://github.com/ahliweb/omes/issues/183), [#89](https://github.com/ahliweb/omes/issues/89)

## Context

Previous architectural decisions ([ADR-0011](0011-control-center-and-provider-boundaries.md), [ADR-0016](0016-herman-web-panel-reference.md), and [ADR-0017](0017-upstream-first-ownership-and-boundary-enforcement.md)) established that the OMES Control Center is an operator- and customer-facing web control plane built on the AWCMS foundation (`ahliweb/awcms`). Furthermore, ADR-0016 explicitly rejected importing external local-operator panels (such as Herman) as runtimes, but approved adopting ergonomic patterns behind typed contracts.

With the analysis of the interactive redesign prototype (`ui/control-center/index.html`, derived from `redesign/Análisis Repo Omes GitHub.zip`), OMES now possesses an authoritative, tailor-made visual and interaction specification covering 9 core screens and 3 diagnostic views mapped directly to Control Center backlog issues (#195–#202, #192, #183).

An architectural decision is required to formally adopt this design system as the normative UI/UX baseline across all subsequent Control Center issues.

## Evaluation of Architectural Options

### Option 1: Ad-Hoc Screen Design (Develop screens independently during each issue)
- **Pros**: Low initial coordination overhead.
- **Cons**: Inconsistent visual tokens, disjointed user flows, fragmented role-based access control (RBAC) boundaries, and risk of accidental single-user or raw-credential assumptions.

### Option 2: Generic Off-The-Shelf Admin Dashboard (Import an external third-party dashboard template)
- **Pros**: Quick out-of-the-box UI widgets.
- **Cons**: High dependency bloat; lack of native support for OMES's pull-worker transport model, two-layer health signals, allowlisted command runner drawers, and AWCMS RLS tenant scoping.

### Option 3: Standardize on the OMES Control Center Design System (Chosen)
- **Pros**:
  - **Crisp Authority Alignment**: Respects the three-authority architecture: OMES owns host execution/provenance, Hermes owns agent reasoning, and AWCMS owns multi-tenant billing, permissions, and web UI.
  - **Comprehensive 9-Screen Coverage**: Directly models fleet overview, server drift, deployment runtime topologies (`shared` vs `dedicated`), allowlisted operation queues with approval gates, Hermes orchestration swimlanes, two-layer health diagnostics, backup recovery classes, tenant-scoped audit logs, and pull-worker enrollment.
  - **Built-in Safety**: Explicit two-role RBAC (`Owner` approval gate vs `Operator` proposal), zero exposure of raw secrets, and outbound-only worker transport.
  - **High-Density Dark Aesthetics**: Harmonious dark palette (`#0E1216` background, `#151A20` cards, `#5FC8D6` primary cyan, `#6FD08C` healthy green, `#E8B44A` drift amber, `#E86D5B` error red) with crisp typography (`Public Sans` and `JetBrains Mono`).

## 11-Criteria Architectural Evaluation

1. **Advantages and Disadvantages**: Provides a unified, highly polished, dark-themed operational console tailored specifically to OMES architecture. Avoids external dashboard bloat while giving developers and operators an interactive, working reference.
2. **Security**: Strongest security posture:
   - UI is never an authority: client-side role switching (`Owner` vs `Operator`) changes UI affordances, but all mutations are strictly validated and authorized server-side (ADR-0011).
   - Zero raw secrets or private tokens rendered in UI, logs, or fixtures.
   - Destructive operations require explicit approval gates before submission.
3. **Performance**: Lightweight vanilla CSS styling; no heavyweight runtime charting dependencies for MVP tables and summary tiles; high frame-rate animations and micro-interactions.
4. **Maintainability**: Clear separation between UI tokens, component templates, and contract schemas. Centralized in `docs/ui-ux-design-system.md` and `ui/control-center/`.
5. **Scalability**: Designed to scale from single-host development to multi-region fleets (12+ hosts, multiple regions, risk-sorted filtering). Keyset pagination and filterable data tables conform to AWCMS admin standards.
6. **Accessibility**: High-contrast ratios compliant with WCAG 2.1 AA; clear visual status badges with both color and text/glyph distinctions; keyboard navigation (`Cmd+K` command drawer).
7. **SEO Impact**: The Control Center is an authenticated, internal management application (`/admin/*`) protected behind login; headers enforce `noindex, nofollow`, eliminating any negative public SEO impact.
8. **UI/UX Implications**: Exceptional developer and operator experience: clear visibility of host drift, instant status identification, step-by-step operation tracking, and intuitive subagent swimlanes.
9. **Compatibility**: Fully compatible with AWCMS system-admin layout (`AdminLayout`, `admin-screens.css`, `status-badge`, `stat-grid`, `data-table`). Preserves compatibility with Ubuntu 24.04/26.04 and Linux Mint 22.
10. **Operational Complexity**: Low; operators interact via clear graphical cues while retaining full parity with the OMES CLI (`omes check`, `omes diff`, `omes apply`, `omes doctor`).
11. **Long-Term Technical Implications**: Establishes a permanent, reusable design standard for all future Control Center modules and provider integrations.

## Decision

1. **Adopt Design System**: Adopt the visual design system, token definitions, and screen layouts defined in `docs/ui-ux-design-system.md` and `ui/control-center/` as the official UI/UX baseline.
2. **Canonical Screen Map**:
   - Screen 1: Overview / Fleet ([#200](https://github.com/ahliweb/omes/issues/200))
   - Screen 2: Servers & Drift ([#200](https://github.com/ahliweb/omes/issues/200), [#197](https://github.com/ahliweb/omes/issues/197))
   - Screen 3: Deployments ([#200](https://github.com/ahliweb/omes/issues/200))
   - Screen 4: Operations & Jobs ([#198](https://github.com/ahliweb/omes/issues/198), [#200](https://github.com/ahliweb/omes/issues/200))
   - Screen 5: Hermes Orchestration ([#183](https://github.com/ahliweb/omes/issues/183))
   - Screen 6: Health / Doctor ([#201](https://github.com/ahliweb/omes/issues/201), [#178](https://github.com/ahliweb/omes/issues/178))
   - Screen 7: Backup & Restore ([#201](https://github.com/ahliweb/omes/issues/201), [#176](https://github.com/ahliweb/omes/issues/176))
   - Screen 8: Audit Log ([#196](https://github.com/ahliweb/omes/issues/196), [#201](https://github.com/ahliweb/omes/issues/201))
   - Screen 9: Worker Enrollment ([#199](https://github.com/ahliweb/omes/issues/199), [#192](https://github.com/ahliweb/omes/issues/192))
3. **Role Boundary Enforcement**:
   - Operator submits operation requests; Owner approves destructive mutations.
   - UI reflects role permissions dynamically, while API contracts remain the authoritative gate.
4. **Interactive Asset Maintenance**:
   - The interactive prototype is permanently housed in `ui/control-center/` as a live reference for frontend and backend contributors.
