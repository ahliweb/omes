"""lib/omes/py/health/hermes_adapter.py - Hermes runtime health adapter (issue #178).

Standard library only (ADR-0012). This adapter communicates with the supported
Hermes agent CLI (v2026.9.14) to consume authoritative runtime health signals
(`hermes doctor`, `hermes gateway status`, `hermes profile list`), delegating
runtime semantics to Hermes while ensuring bounded, read-only execution with
redacted outputs.

Every result emits authority="hermes" and attributes its source command.
If a command fails or times out, the result is reported as fail/degraded;
a healthy state is never guessed.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - fixed, read-only, argv-safe commands only
import sys
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from httpjson import capped  # noqa: E402
from model import (  # noqa: E402
    AUTHORITY_HERMES,
    STATUS_FAIL,
    STATUS_PASS,
    layer_result,
)


def _resolve_hermes_bin(custom_bin: Optional[str] = None) -> Optional[str]:
    if custom_bin:
        return custom_bin if (os.path.isfile(custom_bin) and os.access(custom_bin, os.X_OK)) else None
    return shutil.which("hermes")


def _run_cmd(cmd: list[str], timeout: float, env: Optional[dict] = None) -> tuple[int, str, str]:
    """Runs a fixed argv list with a bounded timeout and no shell execution.
    Returns (rc, stdout, stderr); rc is -1 for not found and -2 for timeout.
    """
    bin_path = cmd[0]
    if not (os.path.isabs(bin_path) and os.access(bin_path, os.X_OK)) and shutil.which(bin_path) is None:
        return -1, "", f"{bin_path} not found on PATH"
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, no shell, bounded timeout
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -2, "", f"{' '.join(cmd)} timed out after {timeout}s"
    except OSError as exc:
        return -1, "", str(exc)


def check_hermes_runtime(
    profile: Optional[str] = None,
    timeout: float = 10.0,
    hermes_bin: Optional[str] = None,
    runner: Optional[Callable] = None,
) -> dict:
    """Proves whether the Hermes binary runs and passes `hermes doctor`
    diagnostics. Authoritative runtime signal owned by Hermes.
    """
    bin_path = _resolve_hermes_bin(hermes_bin) or hermes_bin or "hermes"
    run_fn = runner or _run_cmd

    # Step 1: verify binary presence and version
    v_rc, v_out, v_err = run_fn([bin_path, "--version"], timeout)
    if v_rc != 0:
        return layer_result(
            STATUS_FAIL,
            signals={"enabled": v_rc != -1, "active": False, "ready": False},
            proves="Proves whether the `hermes` binary is present and runnable. Does NOT prove gateway or channels.",
            remediation="install Hermes (modules/hermes) or verify PATH" if v_rc == -1 else "run `hermes --version` manually to inspect failure",
            detail=capped(v_err or v_out or f"exit {v_rc}"),
            authority=AUTHORITY_HERMES,
            source="hermes --version",
        )

    # Step 2: run authoritative `hermes doctor`
    doc_cmd = [bin_path, "doctor"]
    if profile:
        doc_cmd.extend(["--profile", profile])

    doc_rc, doc_out, doc_err = run_fn(doc_cmd, timeout)
    doc_ok = doc_rc == 0
    status = STATUS_PASS if doc_ok else STATUS_FAIL

    return layer_result(
        status,
        signals={"enabled": True, "active": True, "ready": doc_ok},
        proves="Proves Hermes binary runs and native `hermes doctor` passed. Does NOT prove host firewall or network exposure.",
        remediation=None if doc_ok else f"run `{' '.join(doc_cmd)}` manually and address diagnostic findings",
        detail=capped(v_out.strip()) if doc_ok else capped(doc_err or doc_out or f"exit {doc_rc}"),
        authority=AUTHORITY_HERMES,
        source="hermes doctor",
    )


def check_hermes_gateway(
    profile: Optional[str] = None,
    timeout: float = 10.0,
    hermes_bin: Optional[str] = None,
    runner: Optional[Callable] = None,
) -> dict:
    """Proves gateway reachability and lifecycle status via Hermes native
    `hermes gateway status`.
    """
    bin_path = _resolve_hermes_bin(hermes_bin) or hermes_bin or "hermes"
    run_fn = runner or _run_cmd
    gw_cmd = [bin_path, "gateway", "status"]
    if profile:
        gw_cmd.extend(["--profile", profile])

    rc, out, err = run_fn(gw_cmd, timeout)
    ok = rc == 0
    status = STATUS_PASS if ok else STATUS_FAIL

    return layer_result(
        status,
        signals={"active": ok, "reachable": ok, "ready": ok},
        proves="Proves native Hermes gateway status indicates active and reachable service.",
        remediation=None if ok else f"run `{' '.join(gw_cmd)}` and inspect gateway error logs",
        detail=capped(out.strip() or err.strip() or f"exit {rc}"),
        authority=AUTHORITY_HERMES,
        source="hermes gateway status",
    )


def check_hermes_profiles(
    timeout: float = 10.0,
    hermes_bin: Optional[str] = None,
    runner: Optional[Callable] = None,
) -> dict:
    """Discovers and inspects profiles via `hermes profile list` to support
    both standalone and multiplexed topologies.
    """
    bin_path = _resolve_hermes_bin(hermes_bin) or hermes_bin or "hermes"
    run_fn = runner or _run_cmd
    cmd = [bin_path, "profile", "list"]
    rc, out, err = run_fn(cmd, timeout)
    ok = rc == 0
    status = STATUS_PASS if ok else STATUS_FAIL

    profiles: list[str] = []
    if ok and out:
        for line in out.splitlines():
            line_s = line.strip()
            if line_s and not line_s.startswith(("#", "-", "=")):
                if line_s.endswith(":") or line_s.lower().startswith(("available", "profiles", "profile list")):
                    continue
                # extract profile name token
                parts = line_s.split()
                if parts:
                    name = parts[1] if (parts[0] == "*" and len(parts) > 1) else parts[0].lstrip("*")
                    if name:
                        profiles.append(name)

    return layer_result(
        status,
        signals={"discovered": ok, "count": len(profiles)},
        proves="Proves Hermes profile configuration is discoverable via `hermes profile list`.",
        remediation=None if ok else "run `hermes profile list` to inspect profile errors",
        detail=f"profiles={','.join(profiles)}" if ok else capped(err or out or f"exit {rc}"),
        authority=AUTHORITY_HERMES,
        source="hermes profile list",
    )
