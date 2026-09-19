"""lib/omes/py/agent/plan.py - pure computation of the apply plan from a
validated AgentDeployment manifest (issue #87).

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
    """Numeric resource-limit lines derived directly from spec.resources.
    Deliberately separate from modules/hermes-gateway/hardening.sh's
    security-hardening directives (NoNewPrivileges, ProtectSystem, etc.) -
    lib/omes/cmd/agent.sh sources that file and calls hardening_render()
    to reuse those directives verbatim rather than duplicating them here;
    this function only covers the per-agent numeric fields the manifest
    schema uniquely declares (memory/cpu/pids), which hardening_render()
    does not know about."""
    lines = [
        f"MemoryMax={resources['memory']}",
        f"MemoryHigh={resources['memory']}",
        f"CPUQuota={_cpu_quota_percent(resources['cpu'])}",
        f"TasksMax={resources['pids']}",
    ]
    return lines


def restart_lines(restart_policy: str) -> list:
    lines = [f"Restart={restart_policy}"]
    if restart_policy != "no":
        lines += ["RestartSec=5", "StartLimitIntervalSec=60", "StartLimitBurst=5"]
    return lines


def build_plan(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Computes the full apply plan. Contains no secret values - only
    secret NAMES (as references, e.g. `EnvironmentFile=` pointing at a
    path an operator manages, never a value)."""
    metadata = manifest["metadata"]
    spec = manifest["spec"]
    name = metadata["name"]
    service_mode = spec["serviceMode"]

    home = paths.base_home(service_mode)
    hermes_home = paths.hermes_home_for_agent(name, service_mode)
    dirs = systemd_dirs(service_mode, home)
    unit = unit_name(name)
    dropin_dir = str(dirs["dropin_dir"]).format(name=name)

    # Secret references are surfaced as *names* of an EnvironmentFile the
    # operator is expected to maintain outside any OMES-managed path
    # (never generated or written by this tool) - see docs/agent-
    # deployment.md "Secrets".
    secret_refs = list(spec.get("secrets", []))
    env_file_ref = str(hermes_home / ".env") if secret_refs else None

    return {
        "agent": name,
        "workspace": metadata["workspace"],
        "environment": metadata["environment"],
        "role": spec["role"],
        "profile": spec["profile"],
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
