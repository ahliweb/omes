#!/usr/bin/env python3
"""lib/omes/py/health/hermes.py - layered agent health/readiness model for
Hermes deployments managed by OMES (issue #79).

Standard library only (ADR-0012). Layers, in order: host, runtime,
gateway, provider (Ollama, via lib/omes/py/health/ollama.py, when
configured - never a second provider implementation), channel (Telegram,
read-only getMe/getWebhookInfo only, via
modules/hermes-gateway/telegram-allowlist.sh's existing curl -K pattern -
never getUpdates/setWebhook/deleteWebhook). This module does not
reimplement Hermes internals: it only runs Hermes's own read-only CLI
commands (`hermes --version`, `hermes doctor`, `hermes gateway status`)
and systemd's own status commands, and parses their output
conservatively (exit code first, output text only as a secondary,
best-effort signal - never assumed to be machine-stable).

Usage:
  python3 hermes.py [--json] [--target agent|gateway] [--mode user|system]

Host-layer facts (systemd presence, disk/mem) are read as a JSON object
on stdin (produced by the bash caller from lib/omes/detect.sh, which this
module does not re-implement); an empty/absent stdin degrades that layer
to "not evaluated" rather than failing the whole check.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404 - only fixed, read-only, argv-safe commands below
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from httpjson import HttpJsonError, capped, request_json  # noqa: E402
from model import (  # noqa: E402
    STATUS_FAIL,
    STATUS_NOT_APPLICABLE,
    STATUS_PASS,
    build_result,
    layer_result,
)

EXIT_READY = 0
EXIT_NOT_READY = 7


def _timeout() -> float:
    raw = os.environ.get("OMES_HEALTH_TIMEOUT", "").strip()
    try:
        return float(raw) if raw else 10.0
    except ValueError:
        return 10.0


def _run(cmd: list, timeout: float) -> tuple:
    """Runs a fixed argv list (never shell=True, never operator-controlled
    strings interpolated into a shell) with a bounded timeout. Returns
    (rc, stdout, stderr); rc is -1 for "binary not found" and -2 for
    "timed out", so callers can distinguish those from a real non-zero
    exit.
    """
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


def check_host(host_facts: Optional[dict]) -> dict:
    if not host_facts:
        return layer_result(
            STATUS_NOT_APPLICABLE,
            proves="No host facts were supplied; this layer was not evaluated.",
            detail="pass a JSON object with systemd_present/disk_free_mb/mem_mb on stdin to evaluate this layer",
        )

    systemd_present = bool(host_facts.get("systemd_present"))
    disk_free_mb = host_facts.get("disk_free_mb")
    mem_mb = host_facts.get("mem_mb")

    disk_min = int(os.environ.get("OMES_HEALTH_DISK_MIN_MB", "512"))
    mem_min = int(os.environ.get("OMES_HEALTH_MEM_MIN_MB", "256"))

    problems = []
    if not systemd_present:
        problems.append("systemd not detected")
    if isinstance(disk_free_mb, (int, float)) and disk_free_mb < disk_min:
        problems.append(f"free disk {disk_free_mb}MB below threshold {disk_min}MB")
    if isinstance(mem_mb, (int, float)) and mem_mb < mem_min:
        problems.append(f"total memory {mem_mb}MB below threshold {mem_min}MB")

    status = STATUS_FAIL if problems else STATUS_PASS
    return layer_result(
        status,
        signals={"enabled": systemd_present, "active": status == STATUS_PASS},
        proves="Proves the host has systemd and meets minimum disk/memory thresholds. Does NOT prove Hermes itself is installed or running.",
        remediation=None if status == STATUS_PASS else "free disk space / add memory, or adjust OMES_HEALTH_DISK_MIN_MB / OMES_HEALTH_MEM_MIN_MB",
        detail="; ".join(problems) if problems else "systemd present, disk/memory above threshold",
    )


def check_runtime(timeout: float) -> dict:
    rc, out, err = _run(["hermes", "--version"], timeout)
    if rc != 0:
        return layer_result(
            STATUS_FAIL,
            signals={"enabled": rc != -1, "active": False},
            proves="Proves whether the `hermes` binary is present and runnable. Does NOT prove the gateway or any channel is connected.",
            remediation="install Hermes (modules/hermes) or verify PATH" if rc == -1 else "run `hermes --version` manually to see the failure",
            detail=capped(err or out or f"exit {rc}"),
        )

    doctor_rc, doctor_out, doctor_err = _run(["hermes", "doctor"], timeout)
    doctor_ok = doctor_rc == 0
    return layer_result(
        STATUS_PASS if doctor_ok else STATUS_FAIL,
        signals={"enabled": True, "active": True, "ready": doctor_ok},
        proves="Proves the Hermes binary runs and `hermes doctor` (Hermes's own self-check) passed. Does NOT prove the gateway service or any messaging channel is connected.",
        remediation=None if doctor_ok else "run `hermes doctor` manually and address what it reports",
        detail=capped(out.strip()) if doctor_ok else capped(doctor_err or doctor_out or f"exit {doctor_rc}"),
    )


def check_gateway(mode: str, timeout: float) -> dict:
    unit = "hermes-gateway"
    systemctl_cmd = ["systemctl"] + (["--user"] if mode == "user" else [])

    enabled_rc, _, _ = _run(systemctl_cmd + ["is-enabled", unit], timeout)
    active_rc, _, _ = _run(systemctl_cmd + ["is-active", unit], timeout)
    enabled = enabled_rc == 0
    active = active_rc == 0

    status_rc, status_out, status_err = _run(["hermes", "gateway", "status"], timeout)
    reachable = status_rc == 0

    api_status = None
    api_url = os.environ.get("OMES_HERMES_GATEWAY_HEALTH_URL", "").strip()
    if api_url:
        host = api_url.split("://", 1)[-1].split(":", 1)[0].split("/", 1)[0]
        if host not in ("127.0.0.1", "localhost", "::1"):
            # issue #79 requires a "127.0.0.1 if configured" endpoint;
            # refuse to call anything else rather than silently trusting
            # an operator-supplied non-loopback URL.
            api_status = False
        else:
            # A bearer token, if configured, is read from a file
            # reference only (OMES_HERMES_GATEWAY_HEALTH_TOKEN_FILE) -
            # never accepted on argv - and never placed in any log line.
            # Reading it here is reserved for a future authenticated
            # request; the current probe is an unauthenticated GET,
            # matching "an API health endpoint... read via GET with
            # timeout" from the issue.
            try:
                request_json(api_url, timeout=timeout)
                api_status = True
            except HttpJsonError:
                api_status = False

    ready = active and reachable and (api_status is not False)
    status = STATUS_PASS if ready else STATUS_FAIL
    return layer_result(
        status,
        signals={"enabled": enabled, "active": active, "reachable": reachable, "ready": ready},
        proves="Proves the gateway systemd unit is enabled/active and `hermes gateway status` succeeded (plus the API health endpoint, if configured). Does NOT by itself prove a messaging channel is connected - see the channel layer.",
        remediation=None if status == STATUS_PASS else f"check `systemctl {'--user ' if mode == 'user' else ''}status {unit}` and `hermes gateway status`",
        detail=capped(status_out.strip() or status_err.strip()) if (status_out or status_err) else f"enabled={enabled} active={active}",
    )


def check_provider(timeout: float) -> dict:
    ollama_enabled = os.environ.get("OMES_OLLAMA_ENABLED", "0") == "1"
    ollama_model = os.environ.get("OMES_OLLAMA_MODEL", "").strip()
    if not ollama_enabled and not (shutil.which("ollama") and ollama_model):
        return layer_result(
            STATUS_NOT_APPLICABLE,
            proves="No provider is configured for this deployment.",
            detail="set OMES_OLLAMA_ENABLED=1 (or install ollama and set OMES_OLLAMA_MODEL) to evaluate this layer",
        )

    import ollama as ollama_health  # local import: keeps `hermes.py --help` fast, and avoids a hard dependency when no provider is configured

    result = ollama_health.run([])
    ready = bool(result.get("ready"))
    return layer_result(
        STATUS_PASS if ready else STATUS_FAIL,
        signals={"ready": ready},
        proves="Proves the configured Ollama provider (service, model, required capabilities) is healthy. See `omes health ollama` for the full layered breakdown.",
        remediation=None if ready else "run `omes health ollama` for the detailed failure",
        detail=f"provider={result.get('provider')} service={result.get('service', {}).get('status')} model={result.get('model', {}).get('status')}",
    )


def check_channel(hermes_home: str, timeout: float) -> dict:
    env_file = os.path.join(hermes_home, ".env")
    has_token = False
    try:
        with open(env_file, "r", encoding="utf-8") as fh:
            has_token = any(line.startswith("TELEGRAM_BOT_TOKEN=") and line.strip() != "TELEGRAM_BOT_TOKEN=" for line in fh)
    except OSError:
        has_token = False

    if not has_token:
        return layer_result(
            STATUS_NOT_APPLICABLE,
            proves="No Telegram channel is configured for this deployment.",
            detail="set TELEGRAM_BOT_TOKEN in $HERMES_HOME/.env to evaluate this layer",
        )

    script = os.environ.get(
        "OMES_TELEGRAM_ALLOWLIST_SCRIPT",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "modules", "hermes-gateway", "telegram-allowlist.sh"),
    )
    env = dict(os.environ)
    env["OMES_HERMES_HOME"] = hermes_home
    rc, out, err = 1, "", ""
    if shutil.which(script) or os.access(script, os.X_OK):
        try:
            proc = subprocess.run(  # nosec B603 - fixed script path, no shell, bounded timeout
                [script, "health"], capture_output=True, text=True, timeout=timeout, env=env, check=False
            )
            rc, out, err = proc.returncode, proc.stdout, proc.stderr
        except (subprocess.TimeoutExpired, OSError) as exc:
            rc, out, err = -2, "", str(exc)
    else:
        return layer_result(
            STATUS_FAIL,
            proves="Could not locate the telegram-allowlist.sh health probe.",
            remediation=f"verify {script} exists and is executable",
            detail="script not found or not executable",
        )

    try:
        parsed = json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        parsed = {}

    reachable = bool(parsed.get("reachable"))
    connected = bool(parsed.get("connected"))
    status = STATUS_PASS if (rc == 0 and connected) else STATUS_FAIL
    return layer_result(
        status,
        signals={"enabled": True, "reachable": reachable, "connected": connected},
        proves=(
            "Proves the Telegram bot token is valid and the Telegram API is reachable "
            "(getMe/getWebhookInfo only). Does NOT prove a running polling gateway is actually "
            "processing updates - getUpdates is never called (would steal updates from a live "
            "gateway). See docs/hermes-integration.md 'Health and readiness'."
        ),
        remediation=None if status == STATUS_PASS else "verify TELEGRAM_BOT_TOKEN and network access to api.telegram.org; see docs/telegram-security.md",
        detail=capped(parsed.get("detail", "") or err or out),
    )


def run(argv: Optional[list] = None) -> dict:
    parser = argparse.ArgumentParser(prog="hermes-health")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--target", choices=["agent", "gateway"], default="agent")
    parser.add_argument("--mode", choices=["user", "system"], default="user")
    parser.add_argument("--hermes-home", default=None)
    args = parser.parse_args(argv)

    timeout = _timeout()
    hermes_home = args.hermes_home or os.environ.get("HERMES_HOME") or os.path.join(os.path.expanduser("~"), ".hermes")

    host_facts = None
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                host_facts = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            host_facts = None

    layers = {}
    if args.target == "agent":
        layers["host"] = check_host(host_facts)
        layers["runtime"] = check_runtime(timeout)
    layers["gateway"] = check_gateway(args.mode, timeout)
    layers["provider"] = check_provider(timeout)
    layers["channel"] = check_channel(hermes_home, timeout)

    return build_result(layers)


def main(argv: Optional[list] = None) -> int:
    result = run(argv)
    print(json.dumps(result))
    return EXIT_READY if result["ready"] else EXIT_NOT_READY


if __name__ == "__main__":
    sys.exit(main())
