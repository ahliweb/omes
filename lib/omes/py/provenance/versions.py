#!/usr/bin/env python3
"""lib/omes/py/provenance/versions.py - runtime version/compatibility
evidence collection (issue #83).

Standard library only (ADR-0012). Collects OMES's own version/git ref,
OS/arch/kernel facts (supplied by the bash caller, which already has
them from lib/omes/detect.sh - this module never re-implements OS
detection), and the version of every optional runtime OMES cares about:
Hermes, the Hermes gateway mode, python3, node, a browser
(chromium/google-chrome), ffmpeg, the docker CLIENT only (never the
socket), Ollama, and a documented allowlist of NON-secret Hermes config
keys read via `hermes config get <key>`.

This module NEVER reads `$HERMES_HOME/.env`, never dumps
`os.environ`, and never puts a secret value in its output - only the
NAMES of config keys in `_ALLOWED_HERMES_CONFIG_KEYS` are read, and each
one is documented as non-secret in docs/compatibility-evidence.md.

Every collected fact is a small object:
  {"value": <str|None>, "source": <str>, "observed_at": <ISO8601 str>,
   "method": <str>, "reason": <str|None>}
"value" is None only when "reason" explains why (missing binary,
unexpected output format, etc.) - this module never guesses or
fabricates a version string.

Input: a single JSON object on stdin with host facts already gathered
by bash (see lib/omes/versions.sh):
  {"omes": {"version": str, "git_ref": str|null},
   "os": {"id": str, "version_id": str, "codename": str, "pretty": str,
          "kernel": str},
   "arch": str,
   "hermes_home": str,
   "gateway_mode": str|null}

Output: a single JSON object on stdout (see docs/compatibility-evidence.md
for the full contract) plus warnings for known-unsupported combinations.

Usage:
  python3 versions.py [--json-only] < host-facts.json

Exit codes: 0 always for a normal run (this is an evidence report, not a
pass/fail gate - docs/compatibility-evidence.md: "evidence is not a
guarantee"); 1 on an internal/unexpected error (e.g. invalid input JSON).
"""

from __future__ import annotations

import datetime
import json
import shutil
import subprocess  # nosec B404 - only fixed, read-only, argv-safe, timeout-bounded commands below
import sys
from typing import Optional

EXIT_OK = 0
EXIT_ERROR = 1

_TIMEOUT_SECONDS = 5.0

# Non-secret Hermes config keys OMES may read for evidence purposes,
# verified against https://hermes-agent.nousresearch.com/docs/user-guide/configuration.
# NEVER add a key here that could hold a credential/token/secret value -
# this allowlist is the single place that decides what `hermes config
# get` is allowed to be asked for on behalf of `omes health versions`.
# `fallback_model` and `fallback_providers` are the legacy scalar and the
# newer list-valued automatic-provider-fallback keys documented at
# https://hermes-agent.nousresearch.com/docs/user-guide/features/fallback-providers.
# Both name a provider/model selection, never a credential. OMES reads
# `fallback_providers` for PRESENCE ONLY (see
# lib/omes/py/health/ai_privacy.py): upstream does not document what
# `hermes config get` prints for a list-valued path, so its value is never
# parsed or interpreted - a non-empty value degrades a derived signal to
# "unknown" rather than being guessed at.
ALLOWED_HERMES_CONFIG_KEYS = (
    "model.provider",
    "model.name",
    "gateway.mode",
    "fallback_model",
    "fallback_providers",
)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fact(value: Optional[str], source: str, method: str, reason: Optional[str] = None) -> dict:
    return {
        "value": value,
        "source": source,
        "observed_at": _now(),
        "method": method,
        "reason": reason,
    }


def _run(cmd: list, timeout: float = _TIMEOUT_SECONDS):
    """Runs a fixed argv command with a bounded timeout. Returns
    (returncode, stdout, stderr); returncode -1 means "binary not found",
    -2 means "timed out". Never uses shell=True and never receives
    caller-controlled argv beyond a fixed command name.
    """
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


def _binary_version(binary: str, args: list, method_label: str) -> dict:
    """Runs `<binary> <args>` and returns a fact whose value is the first
    non-empty line of stdout (falling back to stderr, since some tools -
    e.g. older ffmpeg builds - print version banners to stderr). Never
    returns a value that looks like an error message; a non-zero exit or
    empty output always produces value=None with a "reason".
    """
    rc, out, err = _run([binary, *args])
    if rc == -1:
        return _fact(None, binary, method_label, reason="binary not found on PATH")
    if rc == -2:
        return _fact(None, binary, method_label, reason="command timed out")
    line = _first_line(out) or _first_line(err)
    if rc != 0 or not line:
        return _fact(None, binary, method_label, reason=f"command exited {rc} with no usable output")
    return _fact(line, binary, method_label)


def collect_omes(input_omes: dict) -> dict:
    version = input_omes.get("version") or None
    git_ref = input_omes.get("git_ref") or None
    reason = None if version else "VERSION file unreadable"
    return {
        "version": _fact(version, "VERSION", "file read", reason=reason),
        "git_ref": _fact(git_ref, "git", "git rev-parse --short HEAD", reason=None if git_ref else "not a git checkout, or git unavailable"),
    }


def collect_os(input_os: dict) -> dict:
    os_id = input_os.get("id") or None
    version_id = input_os.get("version_id") or None
    codename = input_os.get("codename") or None
    kernel = input_os.get("kernel") or None
    return {
        "id": _fact(os_id, "/etc/os-release", "lib/omes/detect.sh detect_os", reason=None if os_id else "os-release unreadable"),
        "version_id": _fact(version_id, "/etc/os-release", "lib/omes/detect.sh detect_os", reason=None if version_id else "os-release unreadable"),
        "codename": _fact(codename, "/etc/os-release", "lib/omes/detect.sh detect_os", reason=None if codename else "no UBUNTU_CODENAME/VERSION_CODENAME"),
        "kernel": _fact(kernel, "uname -r", "lib/omes/versions.sh", reason=None if kernel else "uname unavailable"),
    }


def collect_hermes() -> dict:
    return _binary_version("hermes", ["--version"], "hermes --version")


def collect_gateway_mode(input_mode: Optional[str]) -> dict:
    return _fact(
        input_mode or None,
        "state",
        "state_get module.hermes-gateway.mode / module.hermes-gateway-system.mode",
        reason=None if input_mode else "hermes-gateway not applied in this profile",
    )


def collect_python3() -> dict:
    version = f"Python {sys.version.split()[0]}"
    return _fact(version, "sys.version", "in-process interpreter")


def collect_node() -> dict:
    return _binary_version("node", ["--version"], "node --version")


def collect_browser() -> dict:
    for binary in ("chromium", "chromium-browser", "google-chrome"):
        if shutil.which(binary):
            return _binary_version(binary, ["--version"], f"{binary} --version")
    return _fact(None, "chromium|google-chrome", "--version", reason="no supported browser binary found on PATH")


def collect_ffmpeg() -> dict:
    return _binary_version("ffmpeg", ["-version"], "ffmpeg -version")


def collect_docker_client() -> dict:
    """Client version ONLY - this never touches the docker socket (never
    calls `docker version` without --format/-v against the daemon;
    `docker --version` is a pure client-side CLI banner)."""
    return _binary_version("docker", ["--version"], "docker --version (client only, never the socket)")


def collect_ollama() -> dict:
    if not shutil.which("ollama"):
        return _fact(None, "ollama", "ollama --version", reason="ollama not installed")
    return _binary_version("ollama", ["--version"], "ollama --version")


def collect_provider_config(hermes_home: str) -> dict:
    """Reads NON-secret Hermes config keys via `hermes config get <key>`,
    one at a time, from the documented allowlist. NEVER reads
    `$HERMES_HOME/.env`. If `hermes config get` is not a recognized
    subcommand (verified against upstream docs; see module docstring),
    every key is marked not_available with that reason instead of
    guessing at a different invocation.
    """
    result = {}
    if not shutil.which("hermes"):
        for key in ALLOWED_HERMES_CONFIG_KEYS:
            result[key] = _fact(None, "hermes config get", key, reason="hermes binary not found on PATH")
        return result

    for key in ALLOWED_HERMES_CONFIG_KEYS:
        rc, out, err = _run(["hermes", "config", "get", key], timeout=_TIMEOUT_SECONDS)
        if rc == -1:
            result[key] = _fact(None, "hermes config get", key, reason="hermes binary not found on PATH")
            continue
        if rc == -2:
            result[key] = _fact(None, "hermes config get", key, reason="command timed out")
            continue
        combined_err = (err or "").lower()
        if rc != 0 and ("unknown command" in combined_err or "unrecognized" in combined_err or "no such command" in combined_err):
            result[key] = _fact(None, "hermes config get", key, reason="not_available: 'hermes config get' subcommand not recognized by this Hermes build")
            continue
        line = _first_line(out)
        if rc != 0 or not line:
            result[key] = _fact(None, "hermes config get", key, reason=f"command exited {rc} with no usable output")
            continue
        result[key] = _fact(line, "hermes config get", key)
    return result


def _value_of(fact: dict) -> Optional[str]:
    return fact.get("value")


def build_warnings(components: dict) -> list:
    warnings = []

    os_id = _value_of(components["os"]["id"])
    os_version = _value_of(components["os"]["version_id"])
    if os_id and os_version:
        supported = (
            os_id == "ubuntu"
            and (
                os_version in ("26.04", "24.04", "22.04")
                or os_version.startswith(("26.04.", "24.04.", "22.04."))
            )
        ) or (os_id == "linuxmint" and os_version.startswith("22"))
        if not supported:
            warnings.append(f"unsupported OS/version combination for OMES: {os_id} {os_version} (see docs/compatibility-evidence.md)")

    python_value = _value_of(components["python3"])
    if python_value:
        try:
            parts = python_value.split()[1].split(".")
            major, minor = int(parts[0]), int(parts[1])
            if (major, minor) < (3, 10):
                warnings.append(f"python3 {python_value} is older than 3.10, required by graphify (issue #50)")
        except (IndexError, ValueError):
            pass

    gateway_mode = _value_of(components["gateway_mode"])
    hermes_value = _value_of(components["hermes"])
    if gateway_mode and not hermes_value:
        warnings.append("gateway_mode is set in state but 'hermes --version' failed - hermes may be broken or missing")

    return warnings


def collect(input_data: dict) -> dict:
    components = {
        "omes": collect_omes(input_data.get("omes", {})),
        "os": collect_os(input_data.get("os", {})),
        "arch": _fact(input_data.get("arch") or None, "lib/omes/detect.sh detect_arch", "uname -m", reason=None if input_data.get("arch") else "arch detection failed"),
        "hermes": collect_hermes(),
        "gateway_mode": collect_gateway_mode(input_data.get("gateway_mode")),
        "python3": collect_python3(),
        "node": collect_node(),
        "browser": collect_browser(),
        "ffmpeg": collect_ffmpeg(),
        "docker": collect_docker_client(),
        "ollama": collect_ollama(),
        "provider_config": collect_provider_config(input_data.get("hermes_home", "")),
    }
    warnings = build_warnings(components)
    return {
        "ok": True,
        "generated_at": _now(),
        "components": components,
        "warnings": warnings,
    }


def main(argv: list) -> int:
    try:
        raw = sys.stdin.read()
        input_data = json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": f"invalid input JSON: {exc}"}))
        return EXIT_ERROR

    try:
        result = collect(input_data)
    except Exception as exc:  # noqa: BLE001 - this is a top-level CLI boundary
        print(json.dumps({"ok": False, "error": f"internal error: {exc}"}))
        return EXIT_ERROR

    print(json.dumps(result))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
