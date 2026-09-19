"""lib/omes/py/agent/compose_preflight.py - rootless Docker preflight for
the Compose isolation backend (issue #96,
docs/agent-orchestration-roadmap.md section 2.2: "use rootless Docker
where feasible... never grant the agent Docker socket access by
default... never add the operator to the docker group implicitly").

This module is read-only: it only runs `docker context show`,
`docker context inspect --format ...`, `docker info --format ...`, and
`id -nG` (or the OMES_TEST fake-group hook). It never mutates the
docker daemon, group membership, or sudoers - and it never calls `sudo`
or `usermod`. `omes agent apply` for a compose-backend agent MUST call
`check()` here and refuse to proceed (before any mutation) if it does
not report `ok: True`.
"""
from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - fixed, read-only, argv-safe commands only
from typing import List, Optional

ROOTFUL_SOCKET_PATHS = ("/var/run/docker.sock", "/run/docker.sock")


def _run(cmd: List[str], timeout: float):
    if shutil.which(cmd[0]) is None:
        return -1, "", f"{cmd[0]} not found on PATH"
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, no shell, bounded timeout, read-only subcommands only
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return -2, "", f"{' '.join(cmd)} timed out after {timeout}s"
    except OSError as exc:
        return -1, "", str(exc)


def _user_groups() -> List[str]:
    if os.environ.get("OMES_TEST") == "1" and "OMES_FAKE_GROUPS" in os.environ:
        return os.environ["OMES_FAKE_GROUPS"].split(",") if os.environ["OMES_FAKE_GROUPS"] else []
    rc, out, _ = _run(["id", "-nG"], 5.0)
    if rc != 0:
        return []
    return out.split()


def _endpoint_is_rootful_socket(endpoint: str) -> bool:
    for socket_path in ROOTFUL_SOCKET_PATHS:
        if endpoint.endswith(socket_path):
            return True
    return False


def check(timeout: float = 10.0) -> dict:
    """Returns {"ok": bool, "errors": [str, ...], "detail": {...}}.

    Fails (ok=False) if any of:
      - `docker` is not on PATH;
      - the current context's security options do not report
        `name=rootless` (rootful daemon);
      - the current context's endpoint is the well-known rootful socket
        path (/var/run/docker.sock or /run/docker.sock), even if
        SecurityOptions were somehow spoofed/misreported;
      - the invoking user's only path to the reported access is `docker`
        group membership on a rootful daemon (this is never sufficient -
        OMES never adds a user to that group and never treats group
        membership as equivalent to a rootless daemon).
    """
    errors: List[str] = []
    detail: dict = {}

    if shutil.which("docker") is None:
        return {"ok": False, "errors": ["docker is not installed / not on PATH"], "detail": detail}

    ctx_rc, ctx_out, ctx_err = _run(["docker", "context", "show"], timeout)
    detail["context"] = ctx_out if ctx_rc == 0 else None
    if ctx_rc != 0:
        errors.append(f"could not determine the active docker context: {ctx_err or 'unknown error'}")

    endpoint_rc, endpoint_out, endpoint_err = _run(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"], timeout
    )
    detail["endpoint"] = endpoint_out if endpoint_rc == 0 else None
    if endpoint_rc != 0:
        errors.append(f"could not inspect the active docker context's endpoint: {endpoint_err or 'unknown error'}")
    elif _endpoint_is_rootful_socket(endpoint_out):
        errors.append(
            f"docker endpoint {endpoint_out!r} is the well-known rootful socket path - "
            "OMES refuses to deploy the compose backend against a rootful daemon"
        )

    info_rc, info_out, info_err = _run(["docker", "info", "--format", "{{.SecurityOptions}}"], timeout)
    detail["securityOptions"] = info_out if info_rc == 0 else None
    rootless_reported = info_rc == 0 and "name=rootless" in info_out
    if info_rc != 0:
        errors.append(f"could not read docker daemon security options: {info_err or 'unknown error'}")
    elif not rootless_reported:
        errors.append(
            "docker daemon does not report 'name=rootless' in its SecurityOptions - "
            "this backend requires a rootless Docker daemon (see docs/agent-deployment.md)"
        )

    groups = _user_groups()
    detail["groups"] = groups
    in_docker_group = "docker" in groups
    detail["inDockerGroup"] = in_docker_group
    if in_docker_group and not rootless_reported:
        errors.append(
            "the invoking user is in the 'docker' group and the daemon is not reported as rootless - "
            "OMES never treats 'docker' group membership as a substitute for a rootless daemon, "
            "and never adds users to that group itself"
        )

    return {"ok": not errors, "errors": errors, "detail": detail}


def require(timeout: float = 10.0) -> Optional[str]:
    """Convenience wrapper returning a single joined error string, or
    None if the preflight passed."""
    result = check(timeout)
    if result["ok"]:
        return None
    return "; ".join(result["errors"])
