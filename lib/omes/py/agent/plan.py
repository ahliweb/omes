"""lib/omes/py/agent/plan.py - pure computation of the apply plan from a
validated AgentDeployment (v1) or RuntimeDeployment (v2) manifest (issues #87, #174).

No I/O: every path returned here is a *planned* path; lib/omes/cmd/agent.sh
and lib/omes/py/agent/cli.py are responsible for actually reading/writing
them. Keeping this pure makes `omes agent plan`/`--dry-run` trivially
correct (it is definitionally the same code path as apply) and makes the
function directly unit-testable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from . import paths

UNIT_PREFIX = "omes-agent-"
UNIT_SUFFIX = ".service"


# resources.cpu is a core-count string like "1.0"; systemd CPUQuota= wants
# a percentage. 1 core == 100%.
def _cpu_quota_percent(cpu: str) -> str:
    try:
        cores = float(cpu)
    except ValueError:
        return "100%"
    return f"{int(round(cores * 100))}%"


def unit_name(agent_name: str) -> str:
    """The default/fallback unit-name computation. Kept here (and kept
    equal to what lib/omes/runtime.sh's `runtime_agent_service_unit`
    prints) so this module stays pure and independently unit-testable
    with no subprocess call; `build_plan`'s `unit_name_override` is how a
    caller that already resolved the name via
    lib/omes/py/agent/runtime_bridge.py (the source-of-truth path) feeds
    it in instead of this fallback - see docs/agent-deployment.md
    section 7a."""
    return f"{UNIT_PREFIX}{agent_name}{UNIT_SUFFIX}"


def dropin_name() -> str:
    return "10-omes-agent-resources.conf"


def systemd_dirs(service_mode: str, home: Path) -> Dict[str, Path]:
    """Where the unit file and its drop-in directory live, per scope.
    User scope follows `systemctl --user`'s search path
    ($HOME/.config/systemd/user); system scope follows
    /etc/systemd/system, matching modules/hermes-gateway-system's
    convention (and its OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR test hook,
    mirrored here as OMES_AGENT_SYSTEM_UNIT_DIR)."""
    import os

    if service_mode == "user":
        unit_dir = home / ".config" / "systemd" / "user"
    else:
        unit_dir = Path(os.environ.get("OMES_AGENT_SYSTEM_UNIT_DIR", "/etc/systemd/system"))
    return {
        "unit_dir": unit_dir,
        "dropin_dir": unit_dir / f"{UNIT_PREFIX}{{name}}{UNIT_SUFFIX}.d",
    }


def resource_dropin_lines(resources: Dict[str, Any]) -> list:
    """Numeric resource-limit lines derived directly from resources.
    Deliberately separate from modules/hermes-gateway/hardening.sh's
    security-hardening directives (NoNewPrivileges, ProtectSystem, etc.)."""
    lines = []
    if "memory" in resources:
        lines.append(f"MemoryMax={resources['memory']}")
        lines.append(f"MemoryHigh={resources['memory']}")
    if "cpu" in resources:
        lines.append(f"CPUQuota={_cpu_quota_percent(resources['cpu'])}")
    if "pids" in resources:
        lines.append(f"TasksMax={resources['pids']}")
    return lines


def restart_lines(restart_policy: str) -> list:
    lines = [f"Restart={restart_policy}"]
    if restart_policy != "no":
        lines += ["RestartSec=5", "StartLimitIntervalSec=60", "StartLimitBurst=5"]
    return lines


def build_plan(manifest: Dict[str, Any], unit_name_override: "str | None" = None) -> Dict[str, Any]:
    """Computes the full apply plan. Contains no secret values - only
    secret NAMES (as references, e.g. `EnvironmentFile=` pointing at a
    path an operator manages, never a value).

    Supports both v1 (AgentDeployment) and v2 (RuntimeDeployment) manifests.
    For v2, runtime behavior (role, skills, storage, secrets) is delegated to
    Hermes profiles, and host deployment concerns remain strictly OMES-owned."""
    api_version = manifest.get("apiVersion", "omes.ahliweb.com/v1")
    metadata = manifest["metadata"]
    name = metadata["name"]

    if api_version == "omes.ahliweb.com/v2":
        runtime = manifest.get("runtime", {})
        placement = manifest.get("placement", {})
        profile_ref = runtime.get("profileRef", name)
        backend = placement.get("backend", "native")
        service_mode = placement.get("serviceScope", "user")
        restart_policy = placement.get("restartPolicy", "always")
        resources = dict(manifest.get("resources", {"memory": "1G", "cpu": "1.0", "pids": 128}))
        health = dict(manifest.get("health", {"adapter": "hermes-native"}))
        security = dict(manifest.get("security", {"hardeningProfile": "strict", "exposurePolicy": "loopback", "isolationClass": "standard"}))
        recovery = dict(manifest.get("recovery", {"policy": "production"}))
        compose_spec = dict(manifest["compose"]) if "compose" in manifest else None

        home = paths.base_home(service_mode)
        hermes_home = paths.hermes_home_for_agent(name, service_mode)
        dirs = systemd_dirs(service_mode, home)
        unit = unit_name_override or unit_name(name)
        dropin_dir = str(dirs["dropin_dir"]).format(name=name)

        return {
            "apiVersion": "omes.ahliweb.com/v2",
            "agent": name,
            "workspace": metadata.get("workspace"),
            "environment": metadata.get("environment", "production"),
            "runtime": {
                "kind": runtime.get("kind", "hermes"),
                "profileRef": profile_ref,
            },
            "profile": profile_ref,
            "profileRef": profile_ref,
            "backend": backend,
            "serviceMode": service_mode,
            "unit": {
                "name": unit,
                "dir": str(dirs["unit_dir"]),
                "path": str(dirs["unit_dir"] / unit),
                "dropin_dir": dropin_dir,
                "dropin_path": f"{dropin_dir}/{dropin_name()}",
            },
            "hermesHome": str(hermes_home),
            "restartPolicy": restart_policy,
            "resources": resources,
            "resourceDropinLines": resource_dropin_lines(resources) + restart_lines(restart_policy),
            "health": health,
            "security": security,
            "recovery": recovery,
            "compose": compose_spec,
            "storage": {
                "memory": "delegated",
                "sessions": "delegated",
                "skills": "delegated",
            },
            "role": None,
            "capabilities": [],
            "deny": [],
            "secretReferences": [],
            "environmentFileReference": None,
            "backupClasses": ["config", "skills"],
        }

    # v1 fallback
    spec = manifest["spec"]
    service_mode = spec["serviceMode"]
    backend = spec.get("backend", "systemd")

    home = paths.base_home(service_mode)
    hermes_home = paths.hermes_home_for_agent(name, service_mode)
    dirs = systemd_dirs(service_mode, home)
    unit = unit_name_override or unit_name(name)
    dropin_dir = str(dirs["dropin_dir"]).format(name=name)

    secret_refs = list(spec.get("secrets", []))
    env_file_ref = str(hermes_home / ".env") if secret_refs else None

    return {
        "apiVersion": "omes.ahliweb.com/v1",
        "agent": name,
        "workspace": metadata.get("workspace"),
        "environment": metadata.get("environment"),
        "role": spec.get("role"),
        "profile": spec.get("profile"),
        "profileRef": spec.get("profile"),
        "backend": backend,
        "serviceMode": service_mode,
        "unit": {
            "name": unit,
            "dir": str(dirs["unit_dir"]),
            "path": str(dirs["unit_dir"] / unit),
            "dropin_dir": dropin_dir,
            "dropin_path": f"{dropin_dir}/{dropin_name()}",
        },
        "hermesHome": str(hermes_home),
        "restartPolicy": spec["restartPolicy"],
        "resources": dict(spec["resources"]),
        "resourceDropinLines": resource_dropin_lines(spec["resources"]) + restart_lines(spec["restartPolicy"]),
        "health": dict(spec["health"]),
        "storage": dict(spec["storage"]),
        "capabilities": list(spec.get("capabilities", [])),
        "deny": list(spec.get("deny", [])),
        "secretReferences": secret_refs,
        "environmentFileReference": env_file_ref,
        "backupClasses": ["config", "skills"],
    }
