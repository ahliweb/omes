"""lib/omes/py/agent/compose_health.py - health/readiness for
`spec.backend: "compose"` agents (issue #96 follow-up: "containerized
Hermes health should reuse lib/omes/py/health/hermes.py's provider and
channel layers").

lib/omes/py/health/hermes.py's `check_provider`/`check_channel` were
written assuming a process running on the *host* (a host-reachable
Ollama endpoint for `check_provider`; a `$HERMES_HOME/.env` file on the
host filesystem for `check_channel`). For a containerized Hermes agent:

- `check_channel` still works completely unmodified: `spec.compose`'s
  `env_file:` reference is a *host* path (`docker compose` reads it from
  the host filesystem when creating the container - see
  docs/agent-deployment.md section 9.5), so the existing host-side
  `.env` lookup already reflects the container's configured secrets.
  This module calls it verbatim - genuine reuse, not a reimplementation.
- `check_provider`'s Ollama endpoint may only be reachable from *inside*
  the container's own network namespace (an isolated compose network
  does not automatically share the host's loopback interface). This
  module therefore runs an equivalent, read-only reachability probe
  *through* `docker compose exec -T <service> ...` - reusing the same
  layer_result/build_result contract and the same
  OMES_OLLAMA_ENABLED/"is a provider configured at all" gate
  `check_provider` uses, so both backends report the provider layer in
  the same shape, without literally forking check_provider's HTTP-call
  internals (those assume a host-reachable `httpjson` import path that
  does not apply once we are asking "is it reachable from inside this
  container").
- The container/service layer (was OMES's own thing already, not part of
  hermes.py) and the manifest's own health command are also run through
  `docker compose exec -T`.

Every check here is read-only and bounded by a timeout (`docker compose
exec -T <service> true` first, to establish reachability at all). A
container that cannot be exec'd into (compose project down, `docker` CLI
missing, service not found) marks the affected layer `not_applicable`
rather than failing the whole health report - the container/gateway
layer is the one exception: a container that is simply not running IS a
real failure, not "not evaluated" (mirrors `check_gateway`'s semantics
for the systemd backend, which also reports a real `fail` for a unit
that is not active rather than downgrading it to not_applicable).

Never calls Telegram `getUpdates`/`setWebhook`/`deleteWebhook` (inherited
from hermes.py; see AGENTS.md section 2 and docs/hermes-integration.md).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess  # nosec B404 - fixed argv, no shell, bounded timeout
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_HEALTH_DIR = Path(__file__).resolve().parents[1] / "health"


def _load(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, _HEALTH_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_model = _load("_omes_compose_health_model", "model.py")
_hermes_health = _load("_omes_compose_health_hermes", "hermes.py")


def _exec(project: str, compose_file: str, service: str, cmd: list, timeout: float):
    """Runs a fixed argv list inside the compose service's container via
    `docker compose exec -T` (never shell=True; the trailing `cmd` is
    already a fixed list, never a caller-built shell string). Returns
    (rc, stdout, stderr); rc is -1 for "docker not found", -2 for
    "timed out", mirroring lib/omes/py/health/hermes.py's own `_run`
    convention so callers can tell those apart from a real non-zero
    container exit code."""
    if shutil.which("docker") is None:
        return -1, "", "docker not found on PATH"
    full = ["docker", "compose", "-p", project, "-f", str(compose_file), "exec", "-T", service] + cmd
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, no shell, bounded timeout
            full, capture_output=True, text=True, timeout=timeout, check=False
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -2, "", f"docker compose exec timed out after {timeout}s"
    except OSError as exc:
        return -1, "", str(exc)


def container_reachable(project: str, compose_file: str, service: str, timeout: float) -> bool:
    rc, _, _ = _exec(project, compose_file, service, ["true"], timeout)
    return rc == 0


def check_container(project: str, compose_file: str, service: str, timeout: float) -> dict:
    """The compose-backend equivalent of hermes.py's `check_gateway`:
    proves the container is running (and healthy, if it declares a
    Docker HEALTHCHECK) via `docker compose ps --format json`. This is
    OMES's own container-state check (not part of hermes.py - there is
    no systemd unit to ask here), reported in the same layer_result
    shape so `omes agent health` looks identical across backends."""
    if shutil.which("docker") is None:
        return _model.layer_result(
            _model.STATUS_FAIL,
            proves="Proves whether the agent's container is running via `docker compose ps`.",
            remediation="install docker / docker compose",
            detail="docker not found on PATH",
            authority=_model.AUTHORITY_OMES_HOST,
            source="compose",
        )
    proc_cmd = ["docker", "compose", "-p", project, "-f", str(compose_file), "ps", "--format", "json"]
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, no shell, bounded timeout
            proc_cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return _model.layer_result(
            _model.STATUS_FAIL,
            proves="Proves whether the agent's container is running via `docker compose ps`.",
            remediation="check `docker compose ps` manually",
            detail=str(exc),
            authority=_model.AUTHORITY_OMES_HOST,
            source="compose",
        )

    running = False
    detail = proc.stdout.strip()
    if proc.returncode == 0 and detail:
        try:
            for line in detail.splitlines():
                entry = json.loads(line)
                state = str(entry.get("State", "")).lower()
                health = str(entry.get("Health", "")).lower()
                if state == "running" and health in ("", "healthy"):
                    running = True
        except json.JSONDecodeError:
            pass

    status = _model.STATUS_PASS if running else _model.STATUS_FAIL
    return _model.layer_result(
        status,
        signals={"enabled": True, "active": running},
        proves="Proves the agent's container is running (and reports a healthy Docker HEALTHCHECK, if any) via `docker compose ps`. Does NOT prove a messaging channel is connected - see the channel layer.",
        remediation=None if status == _model.STATUS_PASS else "run `docker compose ps` and `docker compose logs` for this project",
        detail=detail or f"exit {proc.returncode}",
        authority=_model.AUTHORITY_OMES_HOST,
        source="compose",
    )


def check_runtime(project: str, compose_file: str, service: str, timeout: float) -> dict:
    """`hermes --version` / `hermes doctor`, run *inside* the container
    via `docker compose exec -T` - unlike the systemd backend, the
    `hermes` binary lives in the container's filesystem, never the
    host's, so this cannot reuse hermes.py's `check_runtime` unmodified
    (it shells out to a bare `hermes` on the *host* PATH)."""
    if not container_reachable(project, compose_file, service, timeout):
        return _model.layer_result(
            _model.STATUS_NOT_APPLICABLE,
            proves="Not evaluated: the container was not reachable via `docker compose exec -T`.",
            detail="container is not running or `docker compose exec` failed; see the container layer",
            authority=_model.AUTHORITY_HERMES,
            source="hermes doctor",
        )

    rc, out, err = _exec(project, compose_file, service, ["hermes", "--version"], timeout)
    if rc != 0:
        return _model.layer_result(
            _model.STATUS_FAIL,
            signals={"enabled": rc != -1, "active": False},
            proves="Proves whether the `hermes` binary is present and runnable inside the agent's container. Does NOT prove the channel is connected.",
            remediation="verify the agent image installs Hermes on PATH",
            detail=(err or out or f"exit {rc}").strip(),
            authority=_model.AUTHORITY_HERMES,
            source="hermes --version",
        )

    doctor_rc, doctor_out, doctor_err = _exec(project, compose_file, service, ["hermes", "doctor"], timeout)
    doctor_ok = doctor_rc == 0
    return _model.layer_result(
        _model.STATUS_PASS if doctor_ok else _model.STATUS_FAIL,
        signals={"enabled": True, "active": True, "ready": doctor_ok},
        proves="Proves the Hermes binary runs and `hermes doctor` passed, evaluated inside the agent's own container via `docker compose exec -T`.",
        remediation=None if doctor_ok else "run `docker compose exec -T <service> hermes doctor` manually",
        detail=(out.strip() if doctor_ok else (doctor_err or doctor_out or f"exit {doctor_rc}").strip()),
        authority=_model.AUTHORITY_HERMES,
        source="hermes doctor",
    )


def check_provider(project: str, compose_file: str, service: str, timeout: float) -> dict:
    """Reuses hermes.py's `check_provider` gating rule (a provider must
    be explicitly configured via OMES_OLLAMA_ENABLED to be evaluated at
    all - see hermes.py's own docstring) but performs the reachability
    probe *through* `docker compose exec -T`, since a compose agent's
    network namespace - not the host's - is what actually needs to reach
    the configured Ollama endpoint."""
    ollama_enabled = os.environ.get("OMES_OLLAMA_ENABLED", "0") == "1"
    if not ollama_enabled:
        return _model.layer_result(
            _model.STATUS_NOT_APPLICABLE,
            proves="No provider is configured for this deployment.",
            detail="set OMES_OLLAMA_ENABLED=1 to evaluate this layer",
            authority=_model.AUTHORITY_EXTERNAL_PROVIDER,
            source="ollama",
        )

    if not container_reachable(project, compose_file, service, timeout):
        return _model.layer_result(
            _model.STATUS_NOT_APPLICABLE,
            proves="Not evaluated: the provider layer requires probing from inside the container's own network namespace, and the container was not reachable via `docker compose exec -T`.",
            detail="container is not running or `docker compose exec` failed; see the container layer",
            authority=_model.AUTHORITY_EXTERNAL_PROVIDER,
            source="ollama",
        )

    endpoint = os.environ.get("OMES_OLLAMA_ENDPOINT", "http://127.0.0.1:11434").strip()
    # A fixed, non-interpolated shell snippet: only OMES's own
    # already-validated `endpoint` value (never operator/container-
    # controlled data) is substituted, and it is passed as a single argv
    # element to `sh -c`, never concatenated into a larger shell string.
    probe = f'command -v curl >/dev/null 2>&1 && curl -sf --max-time {int(timeout)} "{endpoint}/api/version"'
    rc, out, err = _exec(project, compose_file, service, ["sh", "-c", probe], timeout)
    ready = rc == 0
    return _model.layer_result(
        _model.STATUS_PASS if ready else _model.STATUS_FAIL,
        signals={"ready": ready},
        proves="Proves the configured Ollama provider is reachable from inside the agent's own container (docker compose exec -T). Does NOT prove a specific model is loaded - see `omes health ollama` for the full layered breakdown on the host.",
        remediation=None if ready else "verify OMES_OLLAMA_ENDPOINT/network reachability from inside the container (curl, or the container's DNS/route to the Ollama host)",
        detail=(out or err or f"exit {rc}").strip(),
        authority=_model.AUTHORITY_EXTERNAL_PROVIDER,
        source="ollama",
    )


def check_channel(hermes_home: str, timeout: float) -> dict:
    """Reuses hermes.py's `check_channel` completely unmodified: the
    compose backend's `env_file:` reference is a host path
    (docs/agent-deployment.md section 9.5), so the existing host-side
    `.env` lookup already reflects what the container was started with -
    no `docker compose exec` round-trip is needed for this layer."""
    return _hermes_health.check_channel(hermes_home, timeout)


def check_health_command(project: str, compose_file: str, service: str, command: str, timeout: float, container_ok: bool) -> dict:
    """Runs the manifest's own `spec.health.command` inside the container
    via `docker compose exec -T` (read-only by the manifest author's own
    contract - docs/agent-deployment.md section 2)."""
    if not command:
        return _model.layer_result(
            _model.STATUS_NOT_APPLICABLE,
            proves="No spec.health.command was declared for this agent.",
            detail="set spec.health.command in the manifest to evaluate this layer",
            authority=_model.AUTHORITY_OMES_HOST,
            source="spec.health.command",
        )
    if not container_ok:
        return _model.layer_result(
            _model.STATUS_NOT_APPLICABLE,
            proves="Not evaluated: the container is not running - see the container layer.",
            detail="container is not running or unhealthy",
            authority=_model.AUTHORITY_OMES_HOST,
            source="spec.health.command",
        )
    rc, out, err = _exec(project, compose_file, service, ["sh", "-c", command], timeout)
    ok = rc == 0
    return _model.layer_result(
        _model.STATUS_PASS if ok else _model.STATUS_FAIL,
        signals={"ready": ok},
        proves="Proves the manifest's own spec.health.command exits 0 inside the agent's container.",
        remediation=None if ok else "run the command manually via `docker compose exec -T <service> ...` to see the failure",
        detail=(out or err or f"exit {rc}").strip(),
        authority=_model.AUTHORITY_OMES_HOST,
        source="spec.health.command",
    )


def run(plan: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
    """Builds the full layered health result for a `backend: "compose"`
    agent from its computed plan (see lib/omes/py/agent/compose.py's
    `build_plan`). Same overall shape
    (`{"layers": {...}, "ready": bool, "connected": bool}`) as
    lib/omes/py/health/hermes.py's `run`, via the same
    `model.build_result` aggregation rules, so `omes agent health` looks
    the same across the systemd and compose backends."""
    project = plan["project"]
    compose_file = plan["composeFile"]
    service = plan["serviceName"]

    container_layer = check_container(project, compose_file, service, timeout)
    layers = {
        "gateway": container_layer,
        "runtime": check_runtime(project, compose_file, service, timeout),
        "provider": check_provider(project, compose_file, service, timeout),
        "channel": check_channel(plan["hermesHome"], timeout),
        "healthCommand": check_health_command(
            project,
            compose_file,
            service,
            plan.get("health", {}).get("command", ""),
            timeout,
            container_ok=container_layer["status"] == _model.STATUS_PASS,
        ),
    }
    return _model.build_result(layers)
