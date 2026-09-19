"""lib/omes/py/agent/health.py - per-agent health/readiness, reusing the
layered health model from lib/omes/py/health (issue #79) instead of
reimplementing it (issue #87 explicitly requires "health aggregation
reusing lib/omes/py/health").

lib/omes/py/health/hermes.py hardcodes the gateway unit name
"hermes-gateway", which is correct for the single shared gateway service
but wrong for a per-agent unit ("omes-agent-<name>.service"). Rather than
forking that file, this module loads it dynamically (the same
importlib.util technique tests/py/health/test_hermes.py already uses to
test it without a package __init__.py) and:

  - reuses `model.layer_result`/`model.build_result` verbatim for the
    result shape and ready/connected aggregation rules;
  - reuses `check_provider` and `check_channel` verbatim (they take no
    unit-name argument - provider/channel health is not gateway-unit
    specific);
  - reimplements only the gateway-unit check, parameterized by the
    per-agent unit name, following the exact same enabled/active/
    reachable signal shape `check_gateway` uses.

Health checks here are strictly read-only: only systemctl is-enabled/
is-active and the reused check_channel (getMe/getWebhookInfo only, never
getUpdates/setWebhook/deleteWebhook - see hermes.py's own docstring).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess  # nosec B404 - fixed, read-only, argv-safe commands only
import sys
from pathlib import Path
from typing import Optional

_HEALTH_DIR = Path(__file__).resolve().parents[1] / "health"


def _load(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, _HEALTH_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_model = _load("_omes_health_model", "model.py")
_hermes_health = _load("_omes_health_hermes", "hermes.py")


def _run(cmd: list, timeout: float):
    if shutil.which(cmd[0]) is None:
        return -1, "", f"{cmd[0]} not found on PATH"
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, no shell, bounded timeout
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -2, "", f"{' '.join(cmd)} timed out after {timeout}s"
    except OSError as exc:
        return -1, "", str(exc)


def check_agent_unit(unit: str, mode: str, timeout: float) -> dict:
    systemctl_cmd = ["systemctl"] + (["--user"] if mode == "user" else [])

    enabled_rc, _, _ = _run(systemctl_cmd + ["is-enabled", unit], timeout)
    active_rc, active_out, active_err = _run(systemctl_cmd + ["is-active", unit], timeout)
    enabled = enabled_rc == 0
    active = active_rc == 0

    status = _model.STATUS_PASS if (enabled and active) else _model.STATUS_FAIL
    return _model.layer_result(
        status,
        signals={"enabled": enabled, "active": active},
        proves=(
            "Proves the OMES-managed systemd unit for this agent is enabled and active. "
            "Does NOT prove the agent's messaging channel is connected - see the channel layer."
        ),
        remediation=None if status == _model.STATUS_PASS else f"check `systemctl {'--user ' if mode == 'user' else ''}status {unit}`",
        detail=(active_out or active_err or f"enabled={enabled} active={active}").strip(),
    )


def _timeout() -> float:
    raw = os.environ.get("OMES_HEALTH_TIMEOUT", "").strip()
    try:
        return float(raw) if raw else 10.0
    except ValueError:
        return 10.0


def run(unit: str, mode: str, hermes_home: str, timeout: Optional[float] = None) -> dict:
    t = timeout if timeout is not None else _timeout()
    layers = {
        "gateway": check_agent_unit(unit, mode, t),
        "provider": _hermes_health.check_provider(t),
        "channel": _hermes_health.check_channel(hermes_home, t),
    }
    return _model.build_result(layers)
