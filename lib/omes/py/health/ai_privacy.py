#!/usr/bin/env python3
"""lib/omes/py/health/ai_privacy.py - `omes health ai-privacy` read-only AI
privacy posture/egress evidence collector (issue #216, ADR-0029).

Standard library only (ADR-0012). This module collects a bounded
observation through supported, read-only interfaces only - `hermes
--version` and `hermes config get <allowlisted key>` (the exact same
allowlist lib/omes/py/provenance/versions.py already vetted as non-secret,
imported from there rather than duplicated), plus this package's own
lib/omes/py/health/exposure.py listener/firewall audit (also reused, not
duplicated) - and then hands that observation to the pure evaluator in
lib/omes/py/privacy/posture_evidence.py. It never reads `$HERMES_HOME/.env`
or any file under `.hermes/`, never reads `messages.db`, and never prints a
prompt, response, transcript, or credential value: everything this module
collects is already one of posture_evidence's bounded observation fields.

The #215 "restricted local-only posture" integration point and the
operator's declared `expected_posture` are OMES's OWN state (read via
`state_get`, supplied by the bash caller on stdin) - never a Hermes
internal file - so this module stays on the correct side of the "OMES must
not read internal Hermes databases" boundary even when #215 has not landed
yet. When that state is absent, this module reports
`local_only_posture_source.available = false` rather than inventing a
value - see lib/omes/py/privacy/posture_evidence.py's module docstring for
why that must never read as "healthy".

Usage:
  python3 ai_privacy.py [--json] < state-facts.json

Exit codes: 0 status is PASS or WARN, 7 status is FAIL or BLOCKED.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess  # nosec B404 - only fixed, read-only, argv-safe commands below
import sys
from typing import Optional

_HEALTH_DIR = os.path.dirname(os.path.abspath(__file__))
_PY_ROOT = os.path.dirname(_HEALTH_DIR)  # lib/omes/py
sys.path.insert(0, _HEALTH_DIR)
sys.path.insert(0, _PY_ROOT)
import exposure  # noqa: E402
from privacy import posture_evidence  # noqa: E402
from provenance.versions import ALLOWED_HERMES_CONFIG_KEYS  # noqa: E402

EXIT_HEALTHY = 0
EXIT_NOT_HEALTHY = 7

#: Bounded, closed mapping of `model.provider` values to a destination
#: class. Any value not listed here yields "unknown" rather than a guess -
#: an operator running an unlisted provider is exactly the case that must
#: not be silently reported as either local or cloud.
_LOCAL_PROVIDERS = frozenset({"ollama", "local", "local-openai-compatible", "llama.cpp", "lmstudio", "vllm"})
_CLOUD_PROVIDERS = frozenset({
    "openai", "anthropic", "google", "gemini", "azure-openai", "azure", "bedrock",
    "cohere", "mistral", "groq", "together", "fireworks", "openrouter", "perplexity",
})

_BIND_CLASS_TO_LOCAL_ENDPOINT = {
    "loopback": "loopback",
    "lan": "private_network",
    "wildcard": "public",
}


def _timeout() -> float:
    raw = os.environ.get("OMES_HEALTH_TIMEOUT", "").strip()
    try:
        return float(raw) if raw else 10.0
    except ValueError:
        return 10.0


def _run(cmd: list, timeout: float) -> tuple:
    if shutil.which(cmd[0]) is None:
        return -1, "", f"{cmd[0]} not found on PATH"
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, no shell, bounded timeout, read-only
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -2, "", f"{' '.join(cmd)} timed out after {timeout}s"
    except OSError as exc:
        return -1, "", str(exc)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _collect_hermes_version(timeout: float) -> Optional[str]:
    if shutil.which("hermes") is None:
        return None
    rc, out, err = _run(["hermes", "--version"], timeout)
    if rc != 0:
        return None
    line = _first_line(out) or _first_line(err)
    return line or None


def _collect_provider_value(timeout: float) -> Optional[str]:
    """Reads `model.provider` only - the one already-vetted, non-secret
    key in ALLOWED_HERMES_CONFIG_KEYS this module needs. Reusing the same
    frozenset provenance/versions.py already vetted (rather than declaring
    a second, possibly-diverging allowlist) is deliberate."""
    if "model.provider" not in ALLOWED_HERMES_CONFIG_KEYS or shutil.which("hermes") is None:
        return None
    rc, out, _err = _run(["hermes", "config", "get", "model.provider"], timeout)
    if rc != 0:
        return None
    return _first_line(out) or None


def classify_destination(provider_value: Optional[str]) -> str:
    if not provider_value:
        return "unknown"
    key = provider_value.strip().lower()
    if key in _LOCAL_PROVIDERS:
        return "local"
    if key in _CLOUD_PROVIDERS:
        return "cloud"
    return "unknown"


def collect_local_endpoint_classification(destination_class: str, timeout: float) -> str:
    """Reuses lib/omes/py/health/exposure.py's existing `ss`/`ufw`-based
    listener audit rather than re-parsing socket state a second time. Looks
    for the Ollama-owned listener specifically (the only local-inference
    runtime OMES's health machinery already identifies by name/port); any
    other/no match degrades to "unknown" for a local destination, or
    "not_applicable" when the destination itself is not local."""
    if destination_class != "local":
        return "not_applicable"

    try:
        audit = exposure.run()
    except Exception:  # noqa: BLE001 - evidence collection must never raise
        return "unknown"

    for listener in audit.get("listeners", []):
        if listener.get("owner") == "ollama":
            return _BIND_CLASS_TO_LOCAL_ENDPOINT.get(listener.get("bind_class"), "unknown")
    return "unknown"


def collect_network_isolation_active(timeout: float) -> Optional[bool]:
    """Reuses exposure.check_firewall()'s ufw probe. `None` (unknown) when
    ufw is not installed or its state could not be determined - never
    guessed as active."""
    try:
        firewall = exposure.check_firewall(timeout)
    except Exception:  # noqa: BLE001 - evidence collection must never raise
        return None
    active = firewall.get("active")
    return active if isinstance(active, bool) else None


def build_observation(state_facts: dict, timeout: float) -> dict:
    provider_value = _collect_provider_value(timeout)
    destination_class = classify_destination(provider_value)
    local_endpoint_classification = collect_local_endpoint_classification(destination_class, timeout)
    network_isolation_active = collect_network_isolation_active(timeout)
    hermes_version = _collect_hermes_version(timeout)

    expected_posture = state_facts.get("expected_posture")
    local_only_source = state_facts.get("local_only_posture_source")

    observation: dict = {
        "policy_version": "v1",
        "destination_class": destination_class,
        "local_endpoint": {"classification": local_endpoint_classification},
        # Not implemented yet (tracked in #216 follow-on / #215): OMES has
        # no supported interface today to read whether Hermes cloud
        # fallback is enabled, so this is honestly reported as unknown
        # rather than guessed.
        "cloud_fallback_enabled": None,
        "network_isolation_active": network_isolation_active,
        "expected_posture": expected_posture if isinstance(expected_posture, str) else "unknown",
        "evidence_source": "hermes-cli:hermes config get" if provider_value else "omes-host:network-classification",
        "observed_at": _now_iso(),
        "evidence_age_seconds": 0,
    }
    if isinstance(local_only_source, dict):
        observation["local_only_posture_source"] = local_only_source
    if hermes_version:
        observation["hermes_version_reference"] = {"value": hermes_version, "source": "hermes-cli:hermes --version"}
    return observation


def run(argv: Optional[list] = None) -> dict:
    parser = argparse.ArgumentParser(prog="ai-privacy-health")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    _ = args.json  # the bash caller decides human vs JSON rendering; parsed here only for -h/--help symmetry with health.sh's other targets

    timeout = _timeout()

    state_facts: dict = {}
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    state_facts = parsed
        except (ValueError, json.JSONDecodeError):
            state_facts = {}

    observation = build_observation(state_facts, timeout)
    return posture_evidence.evaluate(observation, now=observation["observed_at"])


def main(argv: Optional[list] = None) -> int:
    result = run(argv)
    print(json.dumps(result))
    return EXIT_HEALTHY if result.get("status") in ("PASS", "WARN") else EXIT_NOT_HEALTHY


if __name__ == "__main__":
    sys.exit(main())
