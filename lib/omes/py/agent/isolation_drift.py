"""lib/omes/py/agent/isolation_drift.py - read-only declared-versus-running
isolation drift check for the rootless Compose backend (issue #276,
docs/multi-agent-control-patterns.md G7).

`compose.build_plan()` is the *declared* isolation posture of a
`backend: "compose"` deployment: pinned image, non-root `uid:gid`,
`cap_drop: [ALL]`, read-only rootfs, `no-new-privileges`, an optional
internal (no-egress) network, memory/cpu/pids limits, and only the
declared bind mounts (never a Docker socket). The renderer declares
nothing else: no `cap_add`, no `devices`, no `ipc`, no `privileged`, and no
seccomp or AppArmor override. For those fields the declaration is "Docker's
default", so an *undeclared override* is drift (`HostConfig.CapAdd`,
`HostConfig.Devices`, `HostConfig.IpcMode`, `HostConfig.Privileged`, and
`SecurityOpt` seccomp/apparmor/other entries). The default seccomp and
AppArmor profiles cannot be proven active from `docker inspect`, so those two
controls are reported under `skipped` as not declared; only an override is
detected. This module compares that
declaration with what the container engine reports for the *running*
container through two read-only commands, each a fixed argv list with a
bounded timeout (never a shell string):

    docker inspect --type container <container_name>
    docker network inspect <project>_<network>

It never starts, stops, restarts or changes anything and never calls
`sudo`. Authority: OMES/infrastructure for OS-level isolation evidence
only. Logical isolation (sessions, memory, worktrees) is Hermes-owned and
is deliberately not inspected here.

Status semantics (fail-closed):

- `ok`: every checked field was observed and matches the declaration.
- `drift`: at least one observed value differs from the declaration.
  Positive evidence of drift wins over missing evidence elsewhere.
- `unknown`: there is no usable evidence (docker missing, daemon
  unreachable, container absent, malformed inspect output), or no drift
  was seen but at least one field could not be observed. A field that is
  absent from the inspect output, or has an unexpected type, is
  unobserved (never assumed to be the safe value). `unknown` is never
  reported as `ok`.

Limits (Not implemented yet, tracked in #276): this proves the container
*configuration* the engine holds. It does not prove runtime egress
behavior (no packet probe), does not verify a per-destination allowlist
(none exists), and has only been exercised against shims, not against a
real rootless daemon.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import compose as compose_mod
from . import compose_preflight

STATUS_OK = "ok"
STATUS_DRIFT = "drift"
STATUS_UNKNOWN = "unknown"

SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

Runner = Callable[[List[str], float], Tuple[int, str, str]]

_MEM_RE = re.compile(r"^([0-9]+)([KMG])$")
_MEM_UNITS = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}


def _finding(field: str, declared: Any, observed: Any, severity: str) -> Dict[str, Any]:
    return {"field": field, "declared": declared, "observed": observed, "severity": severity}


def _memory_bytes(memory: str) -> Optional[int]:
    match = _MEM_RE.match(memory) if isinstance(memory, str) else None
    if not match:
        return None
    return int(match.group(1)) * _MEM_UNITS[match.group(2)]


def _nano_cpus(cpu: Any) -> Optional[int]:
    try:
        return int(round(float(cpu) * 1_000_000_000))
    except (TypeError, ValueError):
        return None


def _norm_cap(cap: Any) -> str:
    text = str(cap).upper()
    return text[4:] if text.startswith("CAP_") else text


def _user_is_root(user: Any) -> bool:
    text = str(user or "").strip()
    return text in ("", "0", "root") or text.startswith("0:") or text.startswith("root:")


def _no_new_privileges(security_opt: Any) -> bool:
    for opt in security_opt or []:
        key, _, value = str(opt).replace("=", ":", 1).partition(":")
        if key == "no-new-privileges" and value.lower() in ("", "true", "1"):
            return True
    return False


def _split_security_opt(opt: str) -> Tuple[str, str]:
    """Splits one `SecurityOpt` entry into (key, value). Docker reports
    both `key=value` and `key:value` spellings; the key ends at whichever
    separator comes first, so a value that itself contains `=` or `:` (an
    inline seccomp profile) is kept whole."""
    positions = [i for i in (opt.find("="), opt.find(":")) if i >= 0]
    if not positions:
        return opt, ""
    cut = min(positions)
    return opt[:cut], opt[cut + 1:]


def _str_list(value: Any) -> Optional[List[str]]:
    """`value` as a list of strings: None (Docker's null for "nothing set")
    is the empty list; anything that is not a list of strings is
    unparseable and returns None."""
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    return None


def _short(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _docker_socket_path(value: Any) -> bool:
    return isinstance(value, str) and "docker.sock" in value


def _parse_inspect(stdout: str) -> Optional[Dict[str, Any]]:
    """The single container object from `docker inspect` output, or None
    when the output is not exactly one JSON object inside a list."""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        return None
    return data[0]


def _compare_container(plan: Dict[str, Any], inspect: Dict[str, Any], findings: List[Dict[str, Any]], unverified: List[str], skipped: List[str]) -> None:
    config = inspect.get("Config")
    host = inspect.get("HostConfig")
    config = config if isinstance(config, dict) else None
    host = host if isinstance(host, dict) else None

    # image (the reference the container was created from)
    declared_image = plan["image"]
    if config is not None and "Image" in config:
        if config["Image"] != declared_image:
            findings.append(_finding("image", declared_image, config["Image"], SEVERITY_HIGH))
    else:
        unverified.append("image")

    # user
    declared_user = plan.get("user")
    if not declared_user:
        skipped.append("user: not declared in the manifest (image default applies)")
    elif config is not None and "User" in config:
        observed_user = config["User"]
        if observed_user != declared_user:
            severity = SEVERITY_CRITICAL if _user_is_root(observed_user) else SEVERITY_HIGH
            findings.append(_finding("user", declared_user, observed_user, severity))
    else:
        unverified.append("user")

    if host is None:
        unverified.extend(
            [
                "capDrop", "capAdd", "readOnlyRootfs", "noNewPrivileges", "securityOpt", "devices",
                "memory", "cpus", "pidsLimit", "privileged", "networkMode", "pidMode", "ipcMode",
            ]
        )
    else:
        _compare_host(plan, inspect, host, findings, unverified, skipped)

    _compare_mounts(plan, inspect, findings, unverified)


def _compare_host(plan: Dict[str, Any], inspect: Dict[str, Any], host: Dict[str, Any], findings: List[Dict[str, Any]], unverified: List[str], skipped: List[str]) -> None:
    # capDrop
    dropped = _str_list(host["CapDrop"]) if "CapDrop" in host else None
    if dropped is None:
        unverified.append("capDrop")
    else:
        declared_caps = sorted(_norm_cap(c) for c in plan["capDrop"])
        observed_caps = sorted(_norm_cap(c) for c in dropped)
        if declared_caps != observed_caps:
            findings.append(_finding("capDrop", declared_caps, observed_caps, SEVERITY_HIGH))

    # capAdd: the renderer never emits cap_add, so any added capability is
    # undeclared. Docker reports "nothing added" as null.
    added = _str_list(host["CapAdd"]) if "CapAdd" in host else None
    if added is None:
        unverified.append("capAdd")
    else:
        observed_added = sorted({_norm_cap(c) for c in added})
        if observed_added:
            severity = SEVERITY_CRITICAL if {"ALL", "SYS_ADMIN"} & set(observed_added) else SEVERITY_HIGH
            findings.append(_finding("capAdd", [], observed_added, severity))

    if "ReadonlyRootfs" in host:
        if bool(host["ReadonlyRootfs"]) != bool(plan["readOnlyRootfs"]):
            findings.append(_finding("readOnlyRootfs", bool(plan["readOnlyRootfs"]), bool(host["ReadonlyRootfs"]), SEVERITY_HIGH))
    else:
        unverified.append("readOnlyRootfs")

    _compare_security_opt(inspect, host, findings, unverified, skipped)

    # devices: the renderer never emits devices. Docker reports "none" as null.
    if "Devices" not in host or not (host["Devices"] is None or isinstance(host["Devices"], list)):
        unverified.append("devices")
    else:
        mapped: List[str] = []
        parseable = True
        for device in host["Devices"] or []:
            if isinstance(device, dict) and isinstance(device.get("PathOnHost"), str):
                mapped.append(f"{device['PathOnHost']}:{device.get('PathInContainer') or ''}:{device.get('CgroupPermissions') or ''}")
            else:
                parseable = False
        if mapped:
            findings.append(_finding("devices", [], sorted(mapped), SEVERITY_HIGH))
        if not parseable:
            unverified.append("devices")

    resources = plan.get("resources", {})
    declared_mem = _memory_bytes(resources.get("memory"))
    if declared_mem is not None:
        if "Memory" in host:
            observed_mem = host["Memory"] or 0
            if observed_mem != declared_mem:
                findings.append(_finding("memory", declared_mem, observed_mem, SEVERITY_MEDIUM))
        else:
            unverified.append("memory")
    declared_cpu = _nano_cpus(resources.get("cpu"))
    if declared_cpu is not None:
        if "NanoCpus" in host:
            observed_cpu = host["NanoCpus"] or 0
            if observed_cpu != declared_cpu:
                findings.append(_finding("cpus", declared_cpu, observed_cpu, SEVERITY_MEDIUM))
        else:
            unverified.append("cpus")
    if "pids" in resources:
        if "PidsLimit" in host:
            observed_pids = host["PidsLimit"]
            if observed_pids != resources["pids"]:
                findings.append(_finding("pidsLimit", resources["pids"], observed_pids, SEVERITY_MEDIUM))
        else:
            unverified.append("pidsLimit")

    # Settings the manifest schema can never declare. Any of these on a
    # running container is drift from the (absent) declaration. A missing
    # or wrongly typed value is unobserved, never assumed safe.
    privileged = host.get("Privileged")
    if not isinstance(privileged, bool):
        unverified.append("privileged")
    elif privileged:
        findings.append(_finding("privileged", False, True, SEVERITY_CRITICAL))
    for key, field in (("NetworkMode", "networkMode"), ("PidMode", "pidMode")):
        value = host.get(key)
        if not isinstance(value, str):
            unverified.append(field)
        elif value == "host":
            findings.append(_finding(field, "not host", "host", SEVERITY_CRITICAL))

    # ipcMode: undeclared, so Docker's private default is expected ("" is
    # the same default as reported by older engines). `host` shares the
    # host's IPC namespace; `container:<id>` joins another workload's;
    # `shareable` lets others join this one; `none` is stricter but still
    # undeclared.
    ipc = host.get("IpcMode")
    if not isinstance(ipc, str):
        unverified.append("ipcMode")
    elif ipc not in ("", "private"):
        if ipc == "host":
            severity = SEVERITY_CRITICAL
        elif ipc.startswith("container:"):
            severity = SEVERITY_HIGH
        else:
            severity = SEVERITY_LOW
        findings.append(_finding("ipcMode", "private", _short(ipc), severity))


def _compare_security_opt(inspect: Dict[str, Any], host: Dict[str, Any], findings: List[Dict[str, Any]], unverified: List[str], skipped: List[str]) -> None:
    """`no-new-privileges` is declared; seccomp and AppArmor are not (the
    declaration is Docker's default), so only an override is drift. The
    inspect output cannot prove the default profile is active, so both are
    reported under `skipped` as not declared rather than as verified."""
    skipped.append(
        "seccomp: not declared by the renderer; Docker's default profile is expected and only an override "
        "(unconfined or a custom profile) is reported; the active default is not visible in docker inspect"
    )
    skipped.append(
        "apparmor: not declared by the renderer; Docker's default profile is expected and only an override "
        "(unconfined or a custom profile) is reported; the active default is not visible in docker inspect"
    )
    opts = _str_list(host["SecurityOpt"]) if "SecurityOpt" in host else None
    if opts is None:
        unverified.extend(["securityOpt", "noNewPrivileges"])
        return

    if not _no_new_privileges(opts):
        findings.append(_finding("noNewPrivileges", True, False, SEVERITY_HIGH))

    seen: Dict[str, List[str]] = {"seccomp": [], "apparmor": []}
    other: List[str] = []
    for opt in opts:
        key, value = _split_security_opt(opt)
        if key in seen:
            seen[key].append(value)
        elif key != "no-new-privileges":
            other.append(_short(opt))

    if not seen["apparmor"] and inspect.get("AppArmorProfile") == "unconfined":
        seen["apparmor"].append("unconfined")

    for key in ("seccomp", "apparmor"):
        if not seen[key]:
            continue
        observed = "unconfined" if any(v == "unconfined" for v in seen[key]) else "custom profile"
        severity = SEVERITY_HIGH if observed == "unconfined" else SEVERITY_MEDIUM
        findings.append(_finding(f"securityOpt.{key}", "default profile (no override)", observed, severity))
    if other:
        findings.append(_finding("securityOpt.undeclared", "no-new-privileges only", sorted(other), SEVERITY_HIGH))


def _compare_mounts(plan: Dict[str, Any], inspect: Dict[str, Any], findings: List[Dict[str, Any]], unverified: List[str]) -> None:
    mounts = inspect.get("Mounts")
    if not isinstance(mounts, list):
        unverified.append("mounts")
        return
    declared: Dict[str, Tuple[str, bool]] = {
        v["containerPath"]: (v["hostPath"], not v.get("readOnly", False)) for v in plan.get("volumes", [])
    }
    seen = set()
    for mount in mounts:
        if not isinstance(mount, dict):
            continue
        source = mount.get("Source")
        destination = mount.get("Destination")
        if _docker_socket_path(source) or _docker_socket_path(destination):
            findings.append(_finding("mounts.dockerSocket", "no Docker socket mount", f"{source}:{destination}", SEVERITY_CRITICAL))
            continue
        if mount.get("Type") != "bind":
            continue
        seen.add(destination)
        observed_rw = bool(mount.get("RW", True))
        if destination not in declared or declared[destination][0] != source:
            findings.append(_finding("mounts.undeclared", "declared volumes only", f"{source}:{destination}", SEVERITY_HIGH))
        elif declared[destination][1] != observed_rw:
            severity = SEVERITY_HIGH if observed_rw else SEVERITY_MEDIUM
            findings.append(
                _finding(f"mounts.mode[{destination}]", "rw" if declared[destination][1] else "ro", "rw" if observed_rw else "ro", severity)
            )
    for destination, (host_path, _rw) in sorted(declared.items()):
        if destination not in seen:
            findings.append(_finding("mounts.missing", f"{host_path}:{destination}", None, SEVERITY_MEDIUM))


def _compare_network(plan: Dict[str, Any], inspect: Dict[str, Any], run: Runner, timeout: float, findings: List[Dict[str, Any]], unverified: List[str]) -> None:
    expected = f"{plan['project']}_{plan['network']}"
    settings = inspect.get("NetworkSettings")
    networks = settings.get("Networks") if isinstance(settings, dict) else None
    if not isinstance(networks, dict):
        unverified.extend(["network.attached", "network.internal"])
        return
    attached = sorted(str(k) for k in networks)
    if attached != [expected]:
        findings.append(_finding("network.attached", [expected], attached, SEVERITY_HIGH))
    if expected not in networks:
        unverified.append("network.internal")
        return

    rc, out, _err = run(["docker", "network", "inspect", expected], timeout)
    internal: Optional[bool] = None
    if rc == 0:
        try:
            data = json.loads(out)
            if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict) and isinstance(data[0].get("Internal"), bool):
                internal = data[0]["Internal"]
        except (json.JSONDecodeError, ValueError):
            internal = None
    if internal is None:
        unverified.append("network.internal")
        return
    declared_internal = compose_mod.network_is_internal(plan)
    if internal != declared_internal:
        # A network stricter than declared is still drift, but it removes
        # no protection, so it is reported at low severity.
        severity = SEVERITY_HIGH if declared_internal else SEVERITY_LOW
        findings.append(_finding("network.internal", declared_internal, internal, severity))


def check(plan: Dict[str, Any], timeout: float = 10.0, runner: Optional[Runner] = None) -> Dict[str, Any]:
    """Compares the declared compose `plan` (compose.build_plan) with the
    running container's `docker inspect` state. Read-only. `runner` is a
    test seam with the signature of compose_preflight._run
    (argv, timeout) -> (rc, stdout, stderr)."""
    run: Runner = runner or compose_preflight._run
    result: Dict[str, Any] = {
        "agent": plan.get("agent"),
        "backend": "compose",
        "container": plan["containerName"],
        "project": plan["project"],
        "network": plan["network"],
        "egress": plan.get("egress", compose_mod.DEFAULT_EGRESS),
        "readOnly": True,
        "status": STATUS_UNKNOWN,
        "findings": [],
        "unverified": [],
        "skipped": [],
        "reason": None,
    }

    rc, out, err = run(["docker", "inspect", "--type", "container", plan["containerName"]], timeout)
    if rc != 0:
        result["reason"] = f"could not inspect container {plan['containerName']!r}: {err or out or 'exit ' + str(rc)}"
        return result
    inspect = _parse_inspect(out)
    if inspect is None:
        result["reason"] = "docker inspect did not return exactly one JSON container object"
        return result

    state = inspect.get("State")
    result["running"] = bool(state.get("Running")) if isinstance(state, dict) and "Running" in state else None

    if result["running"] is False:
        # A stopped container's configuration is not evidence about the
        # running workload, and its network attachments are empty.
        result["reason"] = f"container {plan['containerName']!r} exists but is not running; nothing running to compare"
        return result

    findings: List[Dict[str, Any]] = []
    unverified: List[str] = []
    skipped: List[str] = []
    _compare_container(plan, inspect, findings, unverified, skipped)
    _compare_network(plan, inspect, run, timeout, findings, unverified)

    result["findings"] = findings
    result["unverified"] = sorted(set(unverified))
    result["skipped"] = skipped
    if findings:
        result["status"] = STATUS_DRIFT
    elif unverified:
        result["status"] = STATUS_UNKNOWN
        result["reason"] = "no drift observed, but some fields could not be verified: " + ", ".join(result["unverified"])
    else:
        result["status"] = STATUS_OK
    return result
