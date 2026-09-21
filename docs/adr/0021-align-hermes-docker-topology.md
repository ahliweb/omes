# ADR-0021: Align Hermes Container Deployment with Official Docker Topology

- Status: Accepted
- Date: 2026-09-21
- Related issues: [#177](https://github.com/ahliweb/omes/issues/177), [#174](https://github.com/ahliweb/omes/issues/174), [#175](https://github.com/ahliweb/omes/issues/175), [#176](https://github.com/ahliweb/omes/issues/176), [#96](https://github.com/ahliweb/omes/issues/96)

## Context

In upstream Hermes Agent (`v2026.9.14`), official Docker container semantics are documented in `website/docs/user-guide/docker.md`:
- Official image: `ghcr.io/nousresearch/hermes-agent` pinned by sha256 digest;
- Persistent runtime data is mounted at `/opt/data`;
- Container entrypoint utilizes native s6 supervision for background gateway services and profiles;
- Profile services are supervised first-class entities;
- A single `shared` container hosting multiple profiles is the upstream-recommended default topology for efficiency;
- A `dedicated` container per profile is recommended when stronger isolation (CPU/memory cgroups, network segmentation, blast radius, compliance) is required.

Previously, OMES implemented its own per-agent container rendering in `lib/omes/py/agent/compose.py` (ADR-0013 / issue #96). While it successfully enforced rootless Docker preflight, capability dropping (`capDrop: ["ALL"]`), no-new-privileges, and localhost-only port binds, it:
1. Rendered a separate container per agent without supporting upstream's multi-profile shared container topology;
2. Did not conform to the canonical `/opt/data` persistent storage mount path;
3. Did not delegate in-container profile lifecycle and supervision to upstream s6/Hermes commands.

## Evaluation of Architectural Options

### Option 1: Universal Shared Container (Force all profiles into one container)
- **Pros**: Low host memory and container runtime overhead.
- **Cons**: Cannot enforce separate CPU/memory cgroup quotas, network segmentation, or blast radius per agent profile. Fails strict multi-tenant isolation compliance.

### Option 2: Universal Dedicated Container (Always one container per profile)
- **Pros**: Full container boundaries for every deployment.
- **Cons**: High container overhead when running numerous lightweight agent profiles; contradicts official Hermes upstream design where multi-profile shared container is the standard pattern.

### Option 3: Explicit Policy-Driven Topology (`shared` | `dedicated`) with `/opt/data` Contract & s6 Delegation (Chosen)
- **Pros**:
  - **Crisp Authority Separation**: Upstream Hermes owns in-container layout (`/opt/data`), s6 supervision, and profile lifecycle. OMES owns host-level security preflight (rootless Docker), image digest pinning, cgroup resource limits, network sandboxing (loopback-only binds, no docker.sock, capDrop ALL, no-new-privileges), and rollback verification.
  - **Explicit Topologies**:
    1. `shared`: Multi-profile efficiency supervised by upstream s6. Profiles maintain segregated data directories under `/opt/data`. Default for `isolationClass: standard` in RuntimeDeployment v2.
    2. `dedicated`: Isolated container per profile for dedicated CPU/memory limits, network segmentation, independent image pinning, and blast-radius control. Default for `isolationClass: rootless-container` and backward compatibility with AgentDeployment v1.
  - **Upstream Data Contract**: Persistent storage maps host state directories to `/opt/data` (or `/opt/data/<profile>`).
  - **Non-destructive Migration & Rollback**: Existing compose files and backups are preserved before modification; rollback points are preserved.
- **Evaluation Criteria**:
  1. **Advantages and Disadvantages**: Aligns with official Hermes Docker topology while retaining OMES host-level security gates.
  2. **Security**: Strongest containment: non-root user, rootless daemon check, read-only rootfs with minimal tmpfs (`/run`, `/tmp`), loopback-only ports, zero docker.sock exposure.
  3. **Performance**: High efficiency with `shared` mode reducing container overhead for multi-profile systems; full cgroup limits for `dedicated` mode.
  4. **Maintainability**: Low maintenance; delegates internal container supervision to upstream s6 rather than maintaining custom supervisors.
  5. **Scalability**: Enables scaling from single-process shared containers to multi-container dedicated deployments.
  6. **Accessibility & UI/UX**: Machine-readable status and plan outputs with clear topology reporting.
  7. **SEO & Web Impact**: Clear contract for Control Center container monitoring.
  8. **Compatibility**: Full backward compatibility for v1 manifests (`topology: dedicated` default) and seamless migration to v2 (`topology: shared | dedicated`).
  9. **Operational Complexity**: Low; automatic defaults based on isolation policy.
  10. **Long-term Technical Implications**: Prepares OMES for rootless multi-node or multi-tenant orchestration without container runtime fork.
  11. **Upstream-First Precedence (ADR-0017)**: `DELEGATE` in-container supervision and storage structure to Hermes official Docker semantics.

## Decision

1. **Schema Support**:
   - Both `contracts/agent/v1/agent-deployment.schema.json` and `contracts/agent/v2/runtime-deployment.schema.json` declare `compose.topology` with allowed values `["shared", "dedicated"]`.
   - V1 defaults to `dedicated` for strict backward compatibility.
   - V2 defaults to `dedicated` when `security.isolationClass` is `rootless-container`, and `shared` otherwise.
2. **Upstream Storage Alignment**:
   - Every compose deployment guarantees persistent storage mounted at `/opt/data`. If not explicitly specified in `volumes`, OMES automatically injects a volume mapping the host state directory to `/opt/data:rw`.
   - Containers with `readOnlyRootfs: true` configure standard s6 tmpfs mounts for `/run` and `/tmp`.
3. **Supervision and Lifecycle Delegation**:
   - In-container supervision is delegated to upstream s6; OMES never introduces a secondary in-container supervisor.
   - Profile start, stop, and restart in `shared` topology execute `hermes profile ...` inside the shared container, preventing sibling container restarts.
4. **Host Containment**:
   - Rootless daemon preflight remains mandatory.
   - Capability dropping (`capDrop: ["ALL"]`), `no-new-privileges: true`, non-root user, loopback-only port binds (`127.0.0.1:<port>:<port>`), and zero Docker socket access are strictly enforced.
