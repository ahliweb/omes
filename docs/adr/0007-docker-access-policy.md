# ADR-0007: Docker access policy — sudo docker default, rootless next, group opt-in

- Status: Accepted
- Date: 2026-09-18

## Context

The `containers` module (optional in the `server` profile) needs to
decide how the invoking user is expected to interact with Docker.
Membership in the `docker` group is functionally equivalent to
passwordless root (a member can bind-mount the host root filesystem into
a container and escape any container boundary), which the research
baseline (`docs/research-and-implementation-plan.md` Section 2.4)
flags explicitly: "OMES must not automatically add a user to that group
without explicit opt-in and a risk warning." The brief's global CLI
contract already reserves a flag for this (`--allow-docker-group`),
implying the default must be something else.

Candidates considered: default to `sudo docker` with rootless Docker
preferred when eligible and `docker` group membership only via explicit
opt-in (adopted), default straight to `docker` group membership for
convenience, and support only `sudo docker` with no rootless or group
path at all.

## Options considered

### Option A: `sudo docker` default → rootless Docker when eligible → `docker` group only via `--allow-docker-group`

- Security: strongest default — no user gains root-equivalent access
  without an explicit, separately-flagged decision and a printed warning
  at the point of opt-in. Preferring rootless Docker when the host is
  eligible (kernel/user-namespace support present) further reduces the
  attack surface versus even `sudo docker`, since the daemon itself does
  not run as root in that mode.
- Performance: `sudo docker` adds a `sudo` prompt/cache check per
  invocation for interactive use, which is minor friction, not a
  performance cost; rootless Docker has its own, generally small,
  performance trade-offs (network/storage driver differences) that are
  Docker's concern, not OMES's, once selected.
- Maintainability: the `containers` module has one clear default path to
  test (`sudo docker`) plus one well-scoped opt-in branch
  (`--allow-docker-group`) plus a preference check for rootless
  eligibility — three well-defined states rather than an open-ended
  configuration space.
- Scalability: policy is per-host/per-invocation, so it scales
  independently of how many hosts run OMES.
- Compatibility: Docker's own documentation already describes rootless
  mode's eligibility requirements; the module's `module_check` can detect
  eligibility (kernel version, `newuidmap`/`newgidmap`, cgroup v2)
  before recommending it, consistent with the "no mutation during check"
  rule.
- Operational complexity: `sudo docker` requires the operator to
  understand they need `sudo` for Docker commands, which is standard
  Docker guidance and not an OMES-specific burden; rootless mode has its
  own, separately documented operational quirks (storage location,
  systemd `--user` service) that the module surfaces rather than hides.
- Long-term implications: keeps OMES's default posture aligned with "no
  destructive defaults" even as Docker's own tooling or defaults change;
  group opt-in remains available for operators who have made an informed
  choice, without OMES ever making that choice on their behalf.

### Option B: Default straight to `docker` group membership for convenience

- Security: worst option — grants root-equivalent access as a default
  side effect of installing Docker, with no separate warning or opt-in
  gesture, directly contradicting the brief's "no destructive defaults"
  principle and the research baseline's explicit caution against exactly
  this default.
- Performance: marginally more convenient (no `sudo` needed for `docker`
  commands), which is the entire argument for this option and is not
  worth the security regression.
- Maintainability: simpler module logic (one path only), but simplicity
  achieved by removing a safety decision point is not a benefit here.
- Scalability: no material difference.
- Compatibility: no material difference.
- Operational complexity: superficially lower for the operator day-to-day
  (no `sudo` prefix), at the cost of a much larger blast radius if that
  user's session or credentials are ever compromised.
- Long-term implications: normalizes root-equivalent group membership as
  "just how OMES sets up Docker," which is exactly the kind of default
  the research baseline identifies as a business/security risk (a
  security incident from agent/Docker access is listed as a main
  business risk in `docs/research-and-implementation-plan.md` Section
  5.5).

### Option C: Support only `sudo docker`, no rootless path, no group option at all

- Security: safe default preserved, but removes a genuinely more secure
  option (rootless) from operators who could benefit from it, and removes
  the escape hatch (group opt-in) some operators legitimately need for
  tooling that cannot invoke `sudo` per call (e.g. certain IDE/tooling
  integrations) — pushing those operators to work around OMES entirely
  (manually adding themselves to the group outside OMES's visibility/
  warning), which is a worse outcome than an OMES-mediated, warned opt-in.
- Performance: no material difference from Option A's `sudo docker` leg.
- Maintainability: simplest to implement, but at the cost of not serving
  a known-better security posture (rootless) that Docker itself
  officially supports and documents.
- Scalability: no material difference.
- Compatibility: no material difference — rootless eligibility varies by
  host, which is exactly why Option A treats it as a *preference when
  eligible*, not a requirement.
- Operational complexity: forces every containers user into `sudo docker`
  even on hosts fully capable of rootless, and does not solve the
  legitimate group-membership use case at all (only hides it from OMES).
- Long-term implications: rejected because it under-serves the "rootless
  when eligible" security improvement the research baseline explicitly
  recommends, without actually preventing operators from reaching for
  group membership on their own, unwarned, outside OMES.

## Decision

The `containers` module defaults to `sudo docker` usage. When
`module_check` detects the host is eligible for rootless Docker (kernel/
user-namespace/cgroup v2 support present), it prefers and recommends
rootless Docker over `sudo docker`. Adding the invoking user to the
`docker` group happens only when the operator passes
`--allow-docker-group`, and the module prints an explicit warning (that
`docker` group membership is root-equivalent) at the point that flag
triggers the change — the module does not add group membership silently
under any other condition.

## Consequences

- The `containers` module's `module_check` must implement rootless-
  eligibility detection as a read-only check (no mutation), consistent
  with the module contract (ADR-0003).
- `--allow-docker-group` is a documented global flag (per the brief's CLI
  contract) whose only effect inside `containers` is to authorize group
  membership; it must not silently change other module behavior.
- The group-membership warning text is user-facing documentation, not
  just a log line — it must clearly state the root-equivalence fact, not
  merely "this changes docker access."
- `containers` remains optional in the `server` profile
  (`docs/architecture.md` Section 5); a host that never opts into
  containers never faces this decision at all.
