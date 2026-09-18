# ADR-0006: Hermes — upstream installer, downloaded to file, with optional pinning

- Status: Accepted
- Date: 2026-09-18

## Context

Hermes Agent's documented installation path is
`curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`.
OMES needs to install Hermes as part of the `server` and `hermes`
profiles without either (a) blindly piping a live network response into a
shell, which removes any chance to inspect or checksum what is about to
execute as code, or (b) taking on the maintenance burden of re-packaging
or vendoring an upstream project OMES does not control. The brief's
working rules explicitly forbid "`curl | bash` of unpinned content inside
modules," and its security posture ("No destructive defaults... Everything
reversible") pushes toward a middle path: use the upstream installer
(OMES does not reimplement Hermes installation logic), but download it to
a file first and support an optional checksum pin.

Candidates considered: vendor a copy of the Hermes installer/binary inside
the OMES repository, install Hermes via `pip`/a language package manager,
and use the upstream installer downloaded-to-file with an optional
SHA-256 pin (adopted).

## Options considered

### Option A: Use the upstream installer, downloaded to a temp file, with an optional `OMES_HERMES_INSTALLER_SHA256` pin, then executed

- Security: strictly better than piping directly — the installer script
  is on disk and can be logged, inspected, and (if the operator supplies
  a hash) verified before execution; still trusts Nous Research's
  distribution channel for the *content* of the script (TLS gets you the
  channel, not a guarantee the content never changes), which is why the
  hash pin is offered as an *optional*, opt-in hardening rather than a
  false promise of default immutability.
  fetches by `hermes` itself (its own updater, `hermes gateway install`,
  etc.) after the initial installer hands off remain governed by
  Hermes's own security practices, outside OMES's control — documented as
  a boundary, not hidden.
- Performance: one extra `curl -o <tmpfile>` step and a `sha256sum`
  compare when a pin is supplied; negligible relative to the installer's
  own runtime (package downloads, etc.).
- Maintainability: OMES's `hermes` module stays thin — a wrapper around
  the officially documented installation flow (`docs/architecture.md`
  references `hermes doctor`, `hermes config set`, `HERMES_HOME`,
  `~/.hermes/.env` exactly as Hermes documents them) rather than a
  reimplementation of Hermes's own install logic, which OMES does not
  control and cannot commit to keeping in sync with upstream changes.
- Scalability: works the same regardless of how many hosts run it; no
  OMES-side infrastructure (a mirror, a build pipeline) is needed.
- Compatibility: matches exactly what Hermes documents and supports,
  avoiding drift between "how Hermes says to install itself" and "how
  OMES actually installs it" — a gap that would otherwise show up as
  support burden when Hermes's own docs/behavior change.
- Operational complexity: the checksum pin is optional specifically
  because Hermes's installer is expected to change over time (new
  releases); requiring a hash by default would break every fresh install
  as soon as upstream ships a new version, unless OMES also committed to
  tracking and republishing the current hash on every upstream release —
  a maintenance burden disproportionate to the benefit for an MVP audience
  that is not (yet) supply-chain-hardening at that level. Operators who
  need that guarantee can supply `OMES_HERMES_INSTALLER_SHA256` themselves
  from a version they have separately verified.
- Long-term implications: keeps OMES decoupled from Hermes's release
  cadence — Hermes can ship new installer versions without requiring an
  OMES release, at the cost of OMES never being able to *guarantee*
  exactly which installer version ran unless the operator opts into
  pinning.

### Option B: Vendor a copy of the Hermes installer (or a built binary) inside the OMES repository

- Security: superficially "more pinned" (the exact bytes are in OMES's
  own git history), but shifts the trust/maintenance burden onto OMES to
  notice upstream security fixes and re-vendor promptly — a lapse here is
  worse than trusting upstream's own distribution, since operators would
  reasonably assume a vendored copy is current.
- Performance: no meaningful difference.
- Maintainability: significant ongoing burden — every Hermes release
  requires an OMES PR to update the vendored copy, and OMES's
  `THIRD_PARTY_NOTICES.md`/licensing obligations grow to cover Hermes's
  own dependencies transitively.
- Scalability: does not scale with the number of upstream projects OMES
  might eventually integrate (Hermes today, potentially others later) —
  each would need its own vendoring pipeline.
- Compatibility: risks silent staleness — a vendored installer can
  reference an old `HERMES_HOME` layout or CLI surface if OMES falls
  behind Hermes's own changes, exactly the drift Option A avoids.
- Operational complexity: adds a whole subsystem (vendoring, update
  tracking, re-verification against upstream) that does not otherwise
  need to exist.
- Long-term implications: couples OMES's release cadence to Hermes's in a
  way that requires active OMES maintenance to avoid becoming a security
  liability (an outdated vendored installer with a known-fixed issue
  upstream but not yet re-vendored in OMES).

### Option C: Install Hermes via `pip`/a language package manager

- Security: depends entirely on how Hermes itself is distributed via that
  ecosystem (if at all) and inherits that ecosystem's own supply-chain
  posture; does not remove the fundamental question of trusting Hermes's
  published artifact, and adds a second package manager's trust chain on
  top.
- Performance: no material difference.
- Maintainability: only viable if Hermes is actually published to such a
  registry with the same features (CLI, gateway installer, `HERMES_HOME`
  layout) the brief documents; the brief's platform facts describe a
  shell installer as the actual, documented distribution mechanism — this
  option would require Hermes to support a path it is not documented as
  supporting.
- Scalability: no material difference if it existed.
- Compatibility: risks mismatch with the documented CLI surface
  (`hermes doctor`, `hermes gateway install --system`, etc.) if the
  packaged distribution lags or differs from the shell-installer path.
- Operational complexity: would add a new runtime dependency (a language
  package manager) purely to install Hermes, when Hermes's own
  documented path does not require one.
- Long-term implications: speculative — rejected primarily because it is
  not what Hermes actually documents/supports today; revisit only if
  Hermes itself changes its primary distribution mechanism.

## Decision

The `hermes` module downloads the upstream installer
(`https://hermes-agent.nousresearch.com/install.sh`) to a local temp file
via `curl -fsSL <url> -o <tmpfile>`, optionally verifies it against
`OMES_HERMES_INSTALLER_SHA256` when the operator supplies that
environment variable, and only then executes the downloaded file. The
installer is never piped directly from `curl` into a shell. This is the
same general pattern any future module needing an upstream installer must
follow (Section 10 of `docs/architecture.md`), not a Hermes-specific
exception.

## Consequences

- The `hermes` module's `module_check` must verify network reachability
  to the installer URL (and report a clear preflight failure, exit 4, if
  unreachable) rather than deferring that discovery to `module_apply`.
- When `OMES_HERMES_INSTALLER_SHA256` is unset, the module proceeds
  without verification but logs (at `WARN` level) that no pin was
  supplied, so the choice is visible in logs, not silent.
- When the hash is supplied and does not match, the module must fail
  closed — `module_check` or the start of `module_apply` refuses to
  execute the downloaded file, exit 6, naming the module.
- This ADR does not extend to `hermes`'s own subsequent self-updates or
  the Telegram gateway's own dependency fetches (Node/ffmpeg via apt is
  governed separately by `apt-base`/`pkg.sh`); it governs specifically
  the initial installer-script execution.

<!-- OMES-MERMAID: docs/adr/0006-hermes-upstream-installer-with-pinning.md -->

## Visual summary

```mermaid
flowchart LR
    A[Download installer] --> B[Verify optional pin]
    B --> C[Execute upstream installer]
    C --> D[Verify Hermes]
```

