"""lib/omes/py/agent/compose.py - rootless Docker Compose isolation
backend (issue #96, docs/agent-orchestration-roadmap.md section 2.2).

This module owns everything specific to `spec.backend: "compose"`:

- semantic validation of `spec.compose` beyond what the JSON Schema's
  regex/enum keywords alone can express (path containment, digest-only
  images, no Docker-socket mounts, no privileged/host-PID/host-network,
  localhost-only port binds) - called from manifest.py's
  `_semantic_errors` so both backends share one validation entry point;
- pure computation of the compose plan (mirrors plan.py's `build_plan`
  for the systemd backend - no I/O here);
- deterministic rendering of the `compose.yaml` file as a plain string
  template. No PyYAML (ADR-0012: stdlib only) - the rendered format is
  intentionally flat and stable (one map key per line, two-space nesting)
  so a re-render of the same manifest byte-for-byte reproduces the same
  file, which is what makes `apply` idempotent for this backend.

Nothing in this module ever accepts or renders a secret VALUE - secrets
are referenced only via `env_file:` pointing at an operator-owned,
0600 file (`<hermesHome>/.env`), never inlined into the compose file or
into `environment:`.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from . import paths

IMAGE_DIGEST_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]*@sha256:[0-9a-f]{64}$")
PROJECT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
NETWORK_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
USER_RE = re.compile(r"^([0-9]+):([0-9]+)$")
PORT_RE = re.compile(r"^127\.0\.0\.1:([0-9]{1,5}):([0-9]{1,5})$")

DEFAULT_CAP_DROP = ["ALL"]
DEFAULT_READ_ONLY_ROOTFS = True

# Never accepted as a host-side bind mount target, under any path -
# defence in depth even though the schema does not expose a "privileged"/
# "pid"/"network_mode" field at all (additionalProperties: false already
# blocks those keys outright).
_FORBIDDEN_HOST_PATH_FRAGMENTS = ("docker.sock", "/var/run/docker.sock", "/run/docker.sock")
_FORBIDDEN_NETWORK_VALUES = ("host", "none")


def default_project(agent_name: str) -> str:
    return f"omes-agent-{agent_name}"


def default_network(agent_name: str) -> str:
    return f"omes-agent-{agent_name}-net"


def _validate_image(image: Any) -> List[str]:
    errors = []
    if not isinstance(image, str) or not IMAGE_DIGEST_RE.match(image):
        errors.append(
            "spec.compose.image: must be pinned by digest, e.g. "
            "'registry.example.com/agents/researcher@sha256:<64 hex>' - "
            "a tag-only reference (e.g. ':latest') is rejected"
        )
    return errors


def _validate_user(user: Any) -> List[str]:
    if user is None:
        return []
    errors = []
    match = USER_RE.match(user) if isinstance(user, str) else None
    if not match:
        errors.append("spec.compose.user: must be a non-root 'uid:gid' pair")
        return errors
    uid, gid = int(match.group(1)), int(match.group(2))
    if uid == 0 or gid == 0:
        errors.append("spec.compose.user: uid and gid must both be non-zero (non-root)")
    return errors


def _validate_cap_drop(cap_drop: Any) -> List[str]:
    if cap_drop is None:
        return []
    if list(cap_drop) != DEFAULT_CAP_DROP:
        return [
            "spec.compose.capDrop: must be exactly ['ALL'] when set - this backend never "
            "re-adds capabilities (omit the field to get the ['ALL'] default)"
        ]
    return []


def _validate_network(network: Any) -> List[str]:
    if network is None:
        return []
    errors = []
    if not isinstance(network, str) or not NETWORK_RE.match(network):
        errors.append("spec.compose.network: invalid network name")
        return errors
    if network in _FORBIDDEN_NETWORK_VALUES:
        errors.append(f"spec.compose.network: {network!r} is not allowed (no host/none network mode)")
    return errors


def _validate_topology(topology: Any) -> List[str]:
    if topology is None:
        return []
    if topology not in ("shared", "dedicated"):
        return ["spec.compose.topology: must be either 'shared' or 'dedicated'"]
    return []


def _agent_root_containment(agent_name: str) -> Path:
    """The only host directory tree bind-mount hostPaths may live under -
    this agent's own OMES-managed state directory (never another agent's,
    never an arbitrary host path)."""
    return paths.agent_state_dir(agent_name).resolve()


def _allowed_host_roots(agent_name: str) -> List[Path]:
    roots = [paths.agent_state_dir(agent_name).resolve()]
    if agent_name:
        roots.extend([
            paths.hermes_home_for_agent(agent_name, "user").resolve(),
            paths.hermes_home_for_agent(agent_name, "system").resolve(),
            (paths.state_dir() / "shared-hermes").resolve(),
        ])
    return roots


def _validate_host_path(host_path: Any, agent_root: Path, agent_name: str = "") -> List[str]:
    errors = []
    if not isinstance(host_path, str) or not host_path:
        return ["spec.compose.volumes[].hostPath: must be a non-empty string"]
    if ":" in host_path:
        errors.append(f"spec.compose.volumes[].hostPath {host_path!r}: must not contain ':'")
    for fragment in _FORBIDDEN_HOST_PATH_FRAGMENTS:
        if fragment in host_path:
            errors.append(f"spec.compose.volumes[].hostPath {host_path!r}: Docker socket mounts are never allowed")
            break
    if not host_path.startswith("/"):
        errors.append(f"spec.compose.volumes[].hostPath {host_path!r}: must be an absolute path")
        return errors
    # Pure lexical containment check (no filesystem access, no symlink
    # resolution against a real filesystem - this runs at validation
    # time against a manifest that may describe a path that does not
    # exist yet on this host). ".." is rejected outright rather than
    # normalized away, so a traversal attempt cannot slip through.
    if ".." in Path(host_path).parts:
        errors.append(f"spec.compose.volumes[].hostPath {host_path!r}: must not contain '..'")
        return errors
    normalized = Path(host_path)
    allowed_roots = _allowed_host_roots(agent_name) if agent_name else [agent_root]
    is_contained = False
    for root in allowed_roots:
        try:
            normalized.relative_to(root)
            is_contained = True
            break
        except ValueError:
            pass
    if not is_contained:
        errors.append(
            f"spec.compose.volumes[].hostPath {host_path!r}: must be under this agent's own "
            f"state directory ({agent_root}) - absolute host paths outside it are rejected"
        )
    return errors


def _validate_container_path(container_path: Any) -> List[str]:
    if not isinstance(container_path, str) or not container_path.startswith("/"):
        return ["spec.compose.volumes[].containerPath: must be an absolute path"]
    if ":" in container_path:
        return [f"spec.compose.volumes[].containerPath {container_path!r}: must not contain ':'"]
    return []


def _validate_volumes(volumes: Any, agent_name: str) -> List[str]:
    if volumes is None:
        return []
    errors: List[str] = []
    agent_root = _agent_root_containment(agent_name)
    for volume in volumes:
        if not isinstance(volume, dict):
            errors.append("spec.compose.volumes[]: must be an object")
            continue
        errors.extend(_validate_host_path(volume.get("hostPath"), agent_root, agent_name))
        errors.extend(_validate_container_path(volume.get("containerPath")))
    return errors


def _validate_ports(ports: Any) -> List[str]:
    if ports is None:
        return []
    errors = []
    for port in ports:
        if not isinstance(port, str) or not PORT_RE.match(port):
            errors.append(f"spec.compose.ports[] {port!r}: only '127.0.0.1:<port>:<port>' binds are allowed")
    return errors


def validate_compose_spec(compose_spec: Dict[str, Any], agent_name: str) -> List[str]:
    """Semantic validation of `spec.compose` beyond the JSON Schema
    (issue #96). Returns a list of human-readable errors (empty means
    valid). Assumes `compose_spec` is already a dict and `agent_name`
    already passed manifest.py's NAME_RE check (manifest.py only calls
    this after both are true)."""
    errors: List[str] = []
    errors.extend(_validate_topology(compose_spec.get("topology")))
    errors.extend(_validate_image(compose_spec.get("image")))
    project = compose_spec.get("project")
    if project is not None and not PROJECT_RE.match(project):
        errors.append("spec.compose.project: invalid project name")
    errors.extend(_validate_network(compose_spec.get("network")))
    errors.extend(_validate_user(compose_spec.get("user")))
    errors.extend(_validate_cap_drop(compose_spec.get("capDrop")))
    errors.extend(_validate_volumes(compose_spec.get("volumes"), agent_name))
    errors.extend(_validate_ports(compose_spec.get("ports")))
    return errors


# --------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------

def _memory_to_compose(memory: str) -> str:
    """'1G'/'512M'/'1024K' -> compose's lowercase-suffix mem_limit form
    ('1g'/'512m'/'1024k'). The schema already restricts the input to
    `^[0-9]+[KMG]$`."""
    return memory[:-1] + memory[-1].lower()


_RESTART_MAP = {"always": "always", "on-failure": "on-failure", "no": "no"}


def build_plan(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Computes the full compose apply plan. No secret values - only
    secret NAMES (an `env_file` path an operator maintains, never
    generated or written by this tool)."""
    metadata = manifest["metadata"]
    name = metadata["name"]
    api_version = manifest.get("apiVersion", "omes.ahliweb.com/v1")
    if api_version == "omes.ahliweb.com/v2":
        compose_spec = manifest.get("compose", {})
        placement = manifest.get("placement", {})
        service_mode = placement.get("serviceScope", "user")
        restart_policy = placement.get("restartPolicy", "always")
        runtime = manifest.get("runtime", {})
        profile = runtime.get("profileRef", name)
        resources = dict(manifest.get("resources", {"memory": "1G", "cpu": "1.0", "pids": 128}))
        health = dict(manifest.get("health", {"adapter": "hermes-native"}))
        secret_refs = []
        env_file_ref = None
        role = None
        storage = {"memory": "delegated", "sessions": "delegated", "skills": "delegated"}
        topology = compose_spec.get("topology")
        if not topology:
            isolation_class = manifest.get("security", {}).get("isolationClass")
            topology = "shared" if isolation_class == "standard" else "dedicated"
    else:
        spec = manifest["spec"]
        compose_spec = spec["compose"]
        service_mode = spec["serviceMode"]
        restart_policy = spec["restartPolicy"]
        profile = spec["profile"]
        resources = dict(spec["resources"])
        health = dict(spec["health"])
        secret_refs = list(spec.get("secrets", []))
        hermes_home_tmp = paths.hermes_home_for_agent(name, service_mode)
        env_file_ref = str(hermes_home_tmp / ".env") if secret_refs else None
        role = spec["role"]
        storage = dict(spec["storage"])
        topology = compose_spec.get("topology", "dedicated")

    hermes_home = paths.hermes_home_for_agent(name, service_mode)
    agent_state_dir = paths.agent_state_dir(name)

    if topology == "shared":
        project = compose_spec.get("project") or "omes-shared-hermes"
        network = compose_spec.get("network") or "omes-shared-hermes-net"
        service_name = "hermes"
        container_name = f"{project}-hermes"
        compose_dir = paths.state_dir() / "shared-hermes"
        default_data_host_path = str(paths.state_dir() / "shared-hermes" / "data")
    else:
        project = compose_spec.get("project") or default_project(name)
        network = compose_spec.get("network") or default_network(name)
        service_name = name
        container_name = f"{project}-{name}"
        compose_dir = agent_state_dir / "compose"
        default_data_host_path = str(agent_state_dir / "data")

    cap_drop = list(compose_spec.get("capDrop") or DEFAULT_CAP_DROP)
    read_only_rootfs = compose_spec.get("readOnlyRootfs", DEFAULT_READ_ONLY_ROOTFS)
    volumes = [dict(v) for v in compose_spec.get("volumes", [])]
    if not any(v.get("containerPath") == "/opt/data" for v in volumes):
        volumes.append({"hostPath": default_data_host_path, "containerPath": "/opt/data", "readOnly": False})

    ports = list(compose_spec.get("ports", []))

    compose_file = compose_dir / "compose.yaml"

    return {
        "apiVersion": api_version,
        "agent": name,
        "workspace": metadata.get("workspace"),
        "environment": metadata.get("environment", "production"),
        "role": role,
        "profile": profile,
        "serviceMode": service_mode,
        "backend": "compose",
        "topology": topology,
        "image": compose_spec["image"],
        "project": project,
        "network": network,
        "user": compose_spec.get("user"),
        "capDrop": cap_drop,
        "readOnlyRootfs": bool(read_only_rootfs),
        "volumes": volumes,
        "ports": ports,
        "hermesHome": str(hermes_home),
        "restartPolicy": restart_policy,
        "composeRestart": _RESTART_MAP.get(restart_policy, "no"),
        "resources": resources,
        "health": health,
        "storage": storage,
        "secretReferences": secret_refs,
        "environmentFileReference": env_file_ref,
        "backupClasses": ["config", "skills"],
        "composeDir": str(compose_dir),
        "composeFile": str(compose_file),
        "serviceName": service_name,
        "containerName": container_name,
    }


# --------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------

def _yaml_str(value: str) -> str:
    """Renders `value` as a double-quoted YAML scalar. Only used for
    values this module fully controls (never raw operator input passed
    straight through) - image digests, port strings, project/service/
    network names already schema/regex-constrained, and paths already
    validated by validate_compose_spec. Still escapes backslash/quote
    defensively so nothing here can break the flat line format."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_compose_yaml(plan: Dict[str, Any]) -> str:
    """Renders a deterministic, flat compose.yaml as a plain string
    template (no PyYAML - ADR-0012). Same plan in -> byte-identical file
    out, every time; this is what makes re-apply a true no-op when
    nothing changed."""
    service = plan["serviceName"]
    lines: List[str] = ["services:", f"  {service}:"]
    lines.append(f"    image: {_yaml_str(plan['image'])}")
    lines.append(f"    container_name: {_yaml_str(plan['containerName'])}")
    if plan.get("user"):
        lines.append(f"    user: {_yaml_str(plan['user'])}")
    lines.append(f"    read_only: {'true' if plan['readOnlyRootfs'] else 'false'}")
    lines.append("    cap_drop:")
    for cap in plan["capDrop"]:
        lines.append(f"      - {_yaml_str(cap)}")
    lines.append("    security_opt:")
    lines.append("      - \"no-new-privileges:true\"")
    lines.append("    networks:")
    lines.append(f"      - {_yaml_str(plan['network'])}")

    if plan.get("environmentFileReference"):
        lines.append("    env_file:")
        lines.append(f"      - {_yaml_str(plan['environmentFileReference'])}")

    if plan["volumes"]:
        lines.append("    volumes:")
        for volume in plan["volumes"]:
            mode = "ro" if volume.get("readOnly") else "rw"
            spec_str = f"{volume['hostPath']}:{volume['containerPath']}:{mode}"
            lines.append(f"      - {_yaml_str(spec_str)}")

    if plan["readOnlyRootfs"]:
        lines.append("    tmpfs:")
        lines.append("      - \"/run:rw,noexec,nosuid,size=64k\"")
        lines.append("      - \"/tmp:rw,noexec,nosuid,size=64k\"")

    if plan["ports"]:
        lines.append("    ports:")
        for port in plan["ports"]:
            lines.append(f"      - {_yaml_str(port)}")

    resources = plan["resources"]
    lines.append(f"    mem_limit: {_yaml_str(_memory_to_compose(resources['memory']))}")
    lines.append(f"    mem_reservation: {_yaml_str(_memory_to_compose(resources['memory']))}")
    lines.append(f"    cpus: {_yaml_str(str(resources['cpu']))}")
    lines.append(f"    pids_limit: {resources['pids']}")
    lines.append(f"    restart: {_yaml_str(plan['composeRestart'])}")

    lines.append("networks:")
    lines.append(f"  {plan['network']}:")
    lines.append("    driver: bridge")
    lines.append("    internal: false")

    return "\n".join(lines) + "\n"


def plan_summary(plan: Dict[str, Any]) -> Dict[str, Any]:
    """The subset of `plan` that `omes agent plan`/`--dry-run` prints -
    image digest, project, network, volumes, limits, and env references
    by NAME only (never a secret value)."""
    return {
        "backend": "compose",
        "topology": plan.get("topology", "dedicated"),
        "image": plan["image"],
        "project": plan["project"],
        "network": plan["network"],
        "user": plan.get("user"),
        "capDrop": plan["capDrop"],
        "readOnlyRootfs": plan["readOnlyRootfs"],
        "volumes": plan["volumes"],
        "ports": plan["ports"],
        "resources": plan["resources"],
        "restartPolicy": plan["restartPolicy"],
        "secretReferences": plan["secretReferences"],
        "environmentFileReference": plan["environmentFileReference"],
        "composeFile": plan["composeFile"],
        "health": plan["health"],
    }
