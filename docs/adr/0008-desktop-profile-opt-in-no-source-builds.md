# ADR-0008: Desktop profile is opt-in; no source builds in the MVP

- Status: Accepted
- Date: 2026-09-18

## Context

The `desktop` profile (Linux Mint 22.x: `hyprland-session`, `desktop-config`,
etc.) introduces dependency and hardware-compatibility risk that the
`server`/`hermes` profiles do not have: GPU drivers, Wayland compositor
behavior, portals, display managers, and session management vary by
hardware and can regress across Mint point releases. The research
baseline is explicit that "Hyprland introduces dependency drift risk and
is not appropriate as a server baseline" and that "source builds must not
be the default MVP path" because Hyprland upstream itself warns Ubuntu-
family distributions may lag in packaged dependency versions. The brief's
30/60/90-day plan and go/no-go criteria (`docs/research-and-implementation-
plan.md` Sections 4 and 7) treat the desktop profile as something to
stabilize *after* the server/Hermes path, and explicitly says to "delay
the desktop release when Hyprland requires a fragile source build" or
"Cinnamon fallback is not guaranteed."

Candidates considered: desktop profile opt-in with packaged (not
source-built) components and mandatory Cinnamon fallback (adopted),
desktop profile on by default whenever Mint is detected, and building
Hyprland/its dependencies from source to get the newest upstream version.

## Options considered

### Option A: Desktop profile is opt-in (`--profile desktop`, never auto-selected), uses distro/PPA-packaged components only, and preserves Cinnamon as a selectable fallback session

- Security: smallest default attack surface — a server-only or
  Hermes-only operator never has Hyprland, Waybar, or any desktop
  component installed unless they explicitly ask for it; packaged
  components receive normal distro security updates, whereas a
  source-built component would need OMES to track and rebuild on every
  upstream security fix itself.
- Performance: no desktop overhead on hosts that do not select the
  profile; on hosts that do, packaged builds may lag slightly behind
  upstream's newest performance work, which is an accepted trade-off (see
  "compatibility" below).
- Maintainability: OMES's maintenance burden for the desktop profile is
  bounded to "track packaged versions across Mint point releases," not
  "maintain a build pipeline for Hyprland and its dependency chain" — a
  materially smaller and more predictable surface for a small maintainer
  team.
- Scalability: bounded — the desktop profile's compatibility matrix
  (issue #3) only needs to track supported Mint releases and packaged
  component versions, not an arbitrary matrix of source-build
  configurations across GPUs/kernels.
- Compatibility: packaged components are, by construction, the versions
  Mint's own package manager has tested against that release's kernel/
  Mesa/Xorg-Wayland stack — the same compatibility reasoning the research
  baseline uses to justify avoiding source builds. Cinnamon remaining
  selectable means a broken or unsupported-hardware Hyprland session does
  not leave the host unusable (a login-manager-level fallback always
  exists).
- Operational complexity: an operator who never wanted a desktop (the
  server/Hermes majority of the target audience, per the ICP priority
  order in `docs/research-and-implementation-plan.md` Section 5.1) never
  encounters desktop-specific troubleshooting at all.
- Long-term implications: keeps the desktop profile's risk contained and
  explicitly gated by the go/no-go criteria in the research plan (delay
  release if Cinnamon fallback or Hyprland packaging is not solid) — the
  architecture does not need to promise desktop stability before the
  product plan says it is ready to.

### Option B: Desktop profile is auto-selected whenever Mint is detected

- Security: worse — silently expands the installed surface (Wayland
  compositor, portals, session components) on any Mint host running
  `omes install` without a profile flag, even if the operator only wanted
  Hermes or a server-style baseline on their Mint box.
- Performance: forces desktop-profile costs (install time, disk, running
  a compositor's dependencies) onto operators who did not ask for them.
- Maintainability: removes the clean signal of "did the operator actually
  want a desktop" from the module/profile boundary, complicating future
  changes that should only apply to opted-in desktop users.
- Scalability: no material difference beyond the above.
- Compatibility: no material difference (packaging choice is independent
  of auto- vs. opt-in selection).
- Operational complexity: worse — an operator surprised by an
  unrequested desktop install is a support burden the research baseline's
  business-risk analysis explicitly wants to avoid ("desktop value
  proposition is indistinguishable from ordinary dotfiles" risk is made
  worse, not better, by forcing it on everyone).
- Long-term implications: contradicts the phased rollout in
  `docs/research-and-implementation-plan.md` Section 4 (Phase 3, Linux
  Mint desktop profile, is explicitly sequenced after and separate from
  Phase 2's server profile) — auto-selecting it removes the ability to
  ship server support before desktop support is ready.

### Option C: Build Hyprland and dependencies from source to track the newest upstream release

- Security: worse — OMES would be responsible for tracking and rebuilding
  on every upstream security advisory itself, rather than relying on the
  distribution's own security update pipeline; a missed rebuild is a
  silent, OMES-specific vulnerability window.
- Performance: could offer newer features/performance than packaged
  versions, but this benefit is speculative and unevenly realized across
  hardware, while the downside (build fragility) is certain per the
  research baseline's citation of Hyprland upstream's own warning about
  Ubuntu-family dependency lag.
- Maintainability: highest cost of all three options — a build pipeline
  with its own dependency chain (compilers, dev headers, build systems)
  that must be kept working across Mint point releases and kernel/Mesa
  updates; exactly what the research baseline says "must not be the
  default MVP path."
- Scalability: does not scale with a small maintainer team; each new
  hardware/GPU/kernel combination becomes a new build-compatibility case
  rather than "does the distro package work here."
- Compatibility: highest risk of breakage — a from-source Hyprland is
  exactly the "fragile source build" the go/no-go criteria says should
  delay the desktop release.
- Operational complexity: build failures during `install` are a much
  worse operator experience than a packaged `apt install` failing (build
  failures are harder to diagnose, slower to iterate on, and more likely
  to leave partially-built artifacts).
- Long-term implications: directly contradicts the go/no-go delay
  criterion in `docs/research-and-implementation-plan.md` Section 7
  ("Delay the desktop release when Hyprland requires a fragile source
  build"); rejected outright for the MVP.

## Decision

The `desktop` profile is never auto-selected — it runs only via explicit
`--profile desktop`. `desktop-preflight` and `hyprland-session` install
only packaged components available through Mint's supported package
sources (no compilation from source as part of any module's
`module_apply`). Cinnamon remains installed and selectable in the display/
session manager at all times; no desktop module removes or disables it.

## Consequences

- `desktop-preflight`'s `module_check` must fail (exit 4, no mutation)
  rather than fall back to a degraded install when a host's GPU/kernel/
  Mesa/portal combination is not supported — per
  `docs/architecture.md` Section 5's note that `desktop-preflight` gates
  the rest of the desktop profile.
- Any future contributor proposing a source-build path for a desktop
  component must open a new ADR superseding this one, with an explicit
  maintenance-cost argument, rather than adding it as an incidental module
  change.
- The compatibility matrix (issue #3) is the authoritative list of which
  Mint releases/hardware the desktop profile is tested against; this ADR
  does not itself enumerate that list.
- Desktop profile stabilization remains gated by the go/no-go criteria in
  `docs/research-and-implementation-plan.md` Section 7, independent of
  this ADR's architectural decision, which only fixes packaging strategy
  and Cinnamon-fallback policy, not release timing.
