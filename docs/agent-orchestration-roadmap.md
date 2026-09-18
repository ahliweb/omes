# OMES Agent Orchestration Roadmap

> Status: proposed design; MVP implementation is tracked in [issue #87](https://github.com/ahliweb/omes/issues/87).
>
> This document describes the staged deployment boundary for generic and specialist agents. It does not claim that the commands, manifest format, or backends below are implemented on the current branch.

## 1. Decision summary

OMES will not become a second agent runtime, chat router, memory engine, skill marketplace, or full PaaS. Hermes Agent remains the first supported agent runtime and owns agent reasoning, messaging, memory, skills, model/provider routing, delegation, and approvals.

OMES owns the operational boundary:

- deployment and lifecycle;
- host and service policy;
- profile and state isolation;
- readiness and health evidence;
- resource limits;
- backup and rollback classification;
- version and provenance records;
- placement backend selection.

The target is a small, auditable control layer that can manage generic and specialist agent deployments without forcing Coolify, Docker, Redis, PostgreSQL, Kubernetes, or a web dashboard onto the MVP.

## 2. Staged implementation

```text
MVP
  Native OMES + Hermes + systemd
       |
       v
Isolation phase
  OMES + Hermes + rootless Docker Compose
       |
       v
Multi-server phase
  OMES + optional Coolify adapter
       |
       v
Large-scale evaluation
  Nomad/Kubernetes only when measured requirements justify the complexity
```

### 2.1 MVP: native OMES + Hermes + systemd

The MVP is the default and required implementation path.

It should provide:

- a versioned agent deployment manifest;
- `generic` and `specialist` roles;
- Hermes as the only supported runtime;
- profile-safe `HERMES_HOME` isolation;
- user-scoped systemd services by default;
- explicit system-scoped deployment only under the existing non-root service-user policy;
- check → plan → backup → mutate → verify lifecycle;
- idempotent apply and explicit rollback;
- bounded health/readiness checks;
- resource limits and restart policy;
- secret references rather than secret values;
- version/provenance metadata;
- JSON output suitable for Hermes and external monitoring.

A future CLI shape is:

```text
omes agent list
omes agent check <name>
omes agent plan <name>
omes agent apply <name> [--dry-run] [--yes]
omes agent status <name> [--json]
omes agent health <name> [--json]
omes agent restart <name>
omes agent logs <name>
omes agent rollback <name>
```

These commands are proposed only. They must not be documented as implemented until issue #87 lands.

### 2.2 Isolation phase: rootless Docker Compose

This phase is for agents that need stronger filesystem, dependency, browser, or network isolation than a systemd service can provide.

The backend should:

- use rootless Docker where feasible;
- generate or manage a bounded Compose project per agent deployment;
- never grant the agent Docker socket access by default;
- never add the operator to the `docker` group implicitly;
- isolate volumes, networks, sessions, memory, browser profiles, and credentials;
- preserve OMES dry-run, backup, verify, and rollback behavior;
- remain optional for Hermes deployments that work well under systemd.

This phase must not be implemented by silently turning the entire OMES server profile into a Docker platform.

### 2.3 Multi-server phase: optional Coolify adapter

Coolify is an external deployment backend, not an OMES core dependency.

An adapter may delegate explicitly selected container workloads to a Coolify instance:

```yaml
spec:
  backend: coolify
  coolify:
    instance: coolify-production
    project: agent-platform
    environment: production
    resource: researcher-worker
```

OMES remains the source of truth for:

- logical agent identity;
- role and capability policy;
- secret references;
- data classification;
- health contract;
- backup policy;
- runtime compatibility;
- desired deployment intent.

Coolify remains the source of truth for the delegated resource's:

- external resource ID;
- target server;
- container deployment status;
- build/deployment history;
- proxy/domain configuration;
- platform-specific logs.

The adapter must be idempotent, least-privilege, and explicit about rollback boundaries. It must not embed the Coolify Laravel/PostgreSQL/Redis/realtime control-plane stack in OMES.

### 2.4 Large-scale phase: evaluate Nomad/Kubernetes only with evidence

Nomad or Kubernetes should be considered only if measured requirements demonstrate a need for capabilities such as:

- multi-node scheduling;
- high availability;
- rolling deployments at meaningful scale;
- autoscaling;
- service discovery across nodes;
- workload placement and rescheduling;
- multi-region or multi-tenant operation.

The existence of generic and specialist agents alone is not sufficient evidence for adopting a cluster orchestrator.

## 3. Agent model

### 3.1 Generic agent

A generic agent is a reusable deployment template, such as a researcher, reviewer, monitor, or worker. It has a bounded runtime, capability set, resource policy, and state scope.

### 3.2 Specialist agent

A specialist agent is a deployment instance with a domain-specific role and explicit policy. Specialization belongs in Hermes profile/skill/policy configuration; OMES only deploys and governs the operational boundary.

A specialist must declare, directly or through references:

- role and purpose;
- allowed capabilities/tools;
- denied capabilities/tools;
- model/provider reference;
- memory and session scope;
- approval requirements;
- resource class;
- output contract;
- health check;
- backup classification.

## 4. Proposed manifest boundary

The exact schema is part of issue #87. The following is illustrative only:

```yaml
apiVersion: omes.ahliweb.com/v1
kind: AgentDeployment
metadata:
  name: researcher
  workspace: ahliweb
  environment: production
spec:
  runtime: hermes
  profile: researcher
  role: specialist
  backend: systemd
  serviceMode: user
  restartPolicy: always
  capabilities:
    - web-search
    - document-read
  deny:
    - docker-control
    - firewall-write
  resources:
    memory: 1G
    cpu: "1.0"
    pids: 128
  health:
    command: omes agent health researcher
    timeout: 10s
  storage:
    memory: private
    sessions: isolated
    skills: managed
  secrets:
    - provider-primary
```

The manifest must never contain API keys, Telegram tokens, OAuth tokens, cookies, or plaintext secret material.

## 5. Lifecycle contract

Every backend should converge toward the same lifecycle:

```text
declared
  -> preflighted
  -> planned
  -> backed-up
  -> applied
  -> verified
  -> ready
  -> healthy
```

Failure states must be explicit:

```text
degraded | failed | rolled-back
```

The MVP must preserve the existing OMES contracts:

- preflight before mutation;
- dry-run before apply;
- backup before overwriting managed paths;
- root/user scope separation;
- explicit state rather than unsafe filesystem inference;
- stable JSON and exit-code behavior;
- offline restore and rollback where applicable.

## 6. Isolation and security rules

The default deployment must not share between agents:

- `HERMES_HOME`;
- provider credentials;
- Telegram tokens;
- sessions or transcripts;
- memory directories;
- browser profiles;
- writable host paths;
- Docker socket access;
- unrestricted network access.

Agent deployment must reject unsafe names and paths, including path traversal and service identifiers that can alter unrelated systemd units.

Systemd hardening and resource limits are coordinated with [#81](https://github.com/ahliweb/omes/issues/81). Health/readiness is coordinated with [#79](https://github.com/ahliweb/omes/issues/79). Backup categories are coordinated with [#82](https://github.com/ahliweb/omes/issues/82). Version/provenance are coordinated with [#83](https://github.com/ahliweb/omes/issues/83) and [#84](https://github.com/ahliweb/omes/issues/84).

## 7. Relationship to existing architecture

This roadmap extends the existing module architecture; it does not replace `omes install` profiles or the Hermes gateway integration.

- [#11](https://github.com/ahliweb/omes/issues/11) remains responsible for Hermes installation/profile integration.
- [#12](https://github.com/ahliweb/omes/issues/12) remains responsible for user/system gateway services.
- [#14](https://github.com/ahliweb/omes/issues/14) remains the existing operational CLI foundation.
- [#85](https://github.com/ahliweb/omes/issues/85) defines the runtime-neutral boundary.
- [#87](https://github.com/ahliweb/omes/issues/87) implements the first agent deployment lifecycle using the existing Hermes and systemd foundations.
- [#86](https://github.com/ahliweb/omes/issues/86) keeps deployment documentation and resource descriptions synchronized.

## 8. Non-goals

OMES will not, as part of this roadmap by itself:

- become Coolify;
- embed the Coolify control-plane stack;
- implement a second chat or agent reasoning runtime;
- implement a new memory database or skill marketplace;
- require Docker for the server profile MVP;
- adopt Nomad/Kubernetes without measured requirements;
- expose agent control APIs publicly by default;
- share secrets or state between generic and specialist agents implicitly.

## 9. Sources

- [Coolify: How Coolify works](https://coolify.io/docs/core/how-coolify-works)
- [Coolify API overview](https://coolify.io/docs/api/overview)
- [Coolify instance backup boundaries](https://coolify.io/docs/core/backup-and-recovery/instance-backup)
- [Coolify source repository](https://github.com/coollabsio/coolify)
- [OMES architecture](architecture.md)
- [OMES scope](scope.md)
- [OMES Hermes integration](hermes-integration.md)
- [OMES security baseline](security.md)
- [OMES MVP implementation issue #87](https://github.com/ahliweb/omes/issues/87)
