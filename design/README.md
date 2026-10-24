# OMES design artifacts

This directory holds design explorations and UI/UX proposals for OMES surfaces.
Everything here is **reference-only design material**: it is not shipped code, it
does not call any OMES, AWCMS or Hermes API, and nothing in it changes runtime
behavior. The canonical interactive prototype remains
[`ui/control-center/`](../ui/control-center/README.md); the canonical design
rules remain [`docs/ui-ux-design-system.md`](../docs/ui-ux-design-system.md)
until a proposal here is accepted through an ADR.

| Folder | Contents | Status |
|---|---|---|
| [`control-center-v3/`](control-center-v3/README.md) | UX audit of the v2 Control Center prototype, redesign v3 (fleet overview, operations and approvals, health, on-call mobile, design tokens), and the Hermes agent office, task/agenda board and avatar kit | Proposal, not implemented ([#323](https://github.com/ahliweb/omes/issues/323)) |
