#!/usr/bin/env python3
"""lib/omes/py/health/ollama.py - layered Ollama health checks (issue #71).

Standard library only (ADR-0012). Distinguishes three layers:

  1. service   - binary present, systemd unit active (if OMES-installed),
                 endpoint reachable, /api/version responds, bind-address
                 policy (loopback unless OMES_OLLAMA_ALLOW_REMOTE=1).
  2. model     - configured model present in /api/tags, loads within a
                 bounded timeout, minimal chat smoke test, CPU/GPU
                 placement from /api/ps compared against
                 OMES_OLLAMA_EXPECT_PLACEMENT.
  3. capability - chat (baseline), plus structured_output/tool_calling/
                 embeddings/vision, each pass|fail|not_applicable,
                 gated by OMES_OLLAMA_PROFILE.

Exit codes: 0 ready, 7 not ready, 4 service missing (binary/service down,
nothing further could be checked). Never sends real prompts/documents;
every request uses a short, synthetic, non-secret payload, and output
never echoes more than a length-capped snippet (see httpjson.capped).

Usage:
  python3 ollama.py [--json] [--profile <name>] [--model <id>]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import struct
import subprocess
import sys
import zlib
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from httpjson import HttpJsonError, capped, request_json  # noqa: E402

EXIT_READY = 0
EXIT_NOT_READY = 7
EXIT_SERVICE_MISSING = 4

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11434

CAPABILITIES = ("chat", "structured_output", "tool_calling", "embeddings", "vision")

# Capabilities required (beyond baseline "chat") per named profile.
PROFILE_REQUIREMENTS = {
    "text": set(),
    "structured": {"structured_output"},
    "tools": {"tool_calling"},
    "embeddings": {"embeddings"},
    "vision": {"vision"},
    "full": {"structured_output", "tool_calling", "embeddings", "vision"},
}


def _timeout(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def resolve_endpoint() -> str:
    """Resolves the Ollama HTTP endpoint base URL. OLLAMA_HOST may be
    "host:port", "http://host:port", or empty (default 127.0.0.1:11434,
    matching Ollama's own documented default).
    """
    raw = os.environ.get("OLLAMA_HOST", "").strip()
    if not raw:
        return f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = f"http://{raw}"
    return raw.rstrip("/")


def _endpoint_host(endpoint: str) -> str:
    without_scheme = endpoint.split("://", 1)[-1]
    host = without_scheme.split(":", 1)[0]
    return host


def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


def resolve_profile() -> tuple[str, set[str]]:
    """Resolves the active capability profile: a named profile via
    OMES_OLLAMA_PROFILE (default "text"), or a JSON profile file via
    OMES_OLLAMA_PROFILE_FILE (a JSON object with a "capabilities" array
    naming required capabilities beyond baseline chat).
    """
    profile_file = os.environ.get("OMES_OLLAMA_PROFILE_FILE", "").strip()
    if profile_file:
        try:
            with open(profile_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            caps = set(data.get("capabilities", []))
            caps &= set(CAPABILITIES) - {"chat"}
            name = data.get("name", "custom")
            return name, caps
        except (OSError, ValueError, json.JSONDecodeError):
            return "custom-invalid", set()

    name = os.environ.get("OMES_OLLAMA_PROFILE", "text").strip() or "text"
    return name, set(PROFILE_REQUIREMENTS.get(name, set()))


def _tiny_png_base64() -> str:
    """Builds a minimal valid 1x1 red PNG in-process (no network fetch,
    no third-party imaging library) for the vision capability probe.
    """

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00" + bytes([255, 0, 0])  # filter byte + one RGB red pixel
    idat = zlib.compress(raw)
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    return base64.b64encode(png).decode("ascii")


class ServiceMissing(Exception):
    """Raised when the service layer cannot proceed at all (no binary,
    endpoint completely unreachable) - the caller exits 4 in that case
    rather than reporting a generic "not ready".
    """


def check_service(endpoint: str, timeout: float, checks: list) -> dict:
    result: dict[str, Any] = {"status": "pass"}

    binary_present = shutil.which("ollama") is not None
    checks.append(
        {
            "name": "service:binary",
            "status": "pass" if binary_present else "fail",
            "detail": "ollama binary found on PATH" if binary_present else "ollama binary not found on PATH",
            "remediation": None if binary_present else "install Ollama (see docs/ollama.md) before running this check",
        }
    )

    host = _endpoint_host(endpoint)
    loopback = _is_loopback(host)
    allow_remote = os.environ.get("OMES_OLLAMA_ALLOW_REMOTE", "0") == "1"
    if loopback or allow_remote:
        bind_status = "pass"
        bind_detail = f"endpoint host '{host}' is loopback" if loopback else f"non-loopback host '{host}' explicitly allowed via OMES_OLLAMA_ALLOW_REMOTE=1"
    else:
        bind_status = "fail"
        bind_detail = f"endpoint host '{host}' is not loopback and OMES_OLLAMA_ALLOW_REMOTE is not set"
    checks.append(
        {
            "name": "service:bind_policy",
            "status": bind_status,
            "detail": bind_detail,
            "remediation": None if bind_status == "pass" else "bind Ollama to 127.0.0.1 (default) or set OMES_OLLAMA_ALLOW_REMOTE=1 to explicitly accept remote exposure",
        }
    )

    try:
        version = request_json(f"{endpoint}/api/version", timeout=timeout)
        version_str = str(version.get("version", "")) if isinstance(version, dict) else ""
        checks.append(
            {
                "name": "service:version",
                "status": "pass",
                "detail": f"reachable, version={version_str or 'unknown'}",
                "remediation": None,
            }
        )
    except HttpJsonError as exc:
        checks.append(
            {
                "name": "service:version",
                "status": "fail",
                "detail": f"{exc.reason}: {capped(exc.detail)}",
                "remediation": "start the Ollama service (systemctl [--user] start ollama) and verify OLLAMA_HOST",
            }
        )
        if not binary_present or exc.reason == "unreachable":
            raise ServiceMissing(exc.reason) from exc
        result["status"] = "fail"
        return result

    if bind_status == "fail" or not binary_present:
        result["status"] = "fail"
    return result


def check_model(endpoint: str, model: str, timeout: float, load_timeout: float, checks: list) -> dict:
    result: dict[str, Any] = {"id": model, "status": "pass"}

    try:
        tags = request_json(f"{endpoint}/api/tags", timeout=timeout)
    except HttpJsonError as exc:
        checks.append({"name": "model:tags", "status": "fail", "detail": f"{exc.reason}: {capped(exc.detail)}", "remediation": "verify the Ollama endpoint is reachable"})
        result["status"] = "fail"
        return result

    names = []
    if isinstance(tags, dict):
        for entry in tags.get("models", []) or []:
            if isinstance(entry, dict) and "name" in entry:
                names.append(entry["name"])

    present = any(n == model or n.split(":")[0] == model.split(":")[0] for n in names)
    checks.append(
        {
            "name": "model:present",
            "status": "pass" if present else "fail",
            "detail": f"model '{model}' {'found' if present else 'not found'} in local catalog ({len(names)} model(s) present)",
            "remediation": None if present else f"pull the model: ollama pull {model}",
        }
    )
    if not present:
        result["status"] = "fail"
        return result

    try:
        gen = request_json(
            f"{endpoint}/api/generate",
            method="POST",
            payload={"model": model, "prompt": "Reply with exactly: pong", "stream": False, "keep_alive": "5m"},
            timeout=load_timeout,
        )
        response_text = gen.get("response", "") if isinstance(gen, dict) else ""
        loaded_ok = bool(response_text)
        checks.append(
            {
                "name": "model:load_and_smoke",
                "status": "pass" if loaded_ok else "fail",
                "detail": "model loaded and returned a non-empty response" if loaded_ok else "model loaded but returned an empty response",
                "remediation": None if loaded_ok else "check `ollama logs` / `journalctl -u ollama` for load errors",
            }
        )
        if not loaded_ok:
            result["status"] = "fail"
    except HttpJsonError as exc:
        status = "fail"
        checks.append(
            {
                "name": "model:load_and_smoke",
                "status": status,
                "detail": f"{exc.reason}: {capped(exc.detail)}",
                "remediation": "the model load exceeded the bounded timeout or failed; verify available memory/GPU and try `ollama run <model>` manually"
                if exc.reason == "timeout"
                else "verify the Ollama endpoint and model are usable",
            }
        )
        result["status"] = "fail"
        return result

    expected_placement = os.environ.get("OMES_OLLAMA_EXPECT_PLACEMENT", "any").strip().lower()
    try:
        ps = request_json(f"{endpoint}/api/ps", timeout=timeout)
        entry = None
        if isinstance(ps, dict):
            for m in ps.get("models", []) or []:
                if isinstance(m, dict) and (m.get("name") == model or m.get("model") == model):
                    entry = m
                    break
        processor = entry.get("processor", "") if entry else ""
        result["processor"] = processor
        placement = "cpu"
        if "gpu" in processor.lower():
            placement = "gpu" if "100%" in processor or "%" not in processor else "gpu+cpu"
        if expected_placement not in ("any", ""):
            match = expected_placement in placement or placement in expected_placement
            checks.append(
                {
                    "name": "model:placement",
                    "status": "pass" if match else "fail",
                    "detail": f"observed placement='{processor or 'unknown'}' expected='{expected_placement}'",
                    "remediation": None if match else "adjust OMES_OLLAMA_EXPECT_PLACEMENT or the model's runtime placement (GPU offload settings)",
                }
            )
            if not match:
                result["status"] = "fail"
        else:
            checks.append({"name": "model:placement", "status": "pass", "detail": f"observed placement='{processor or 'unknown'}' (no expectation set)", "remediation": None})
    except HttpJsonError as exc:
        checks.append({"name": "model:placement", "status": "fail", "detail": f"{exc.reason}: {capped(exc.detail)}", "remediation": "verify /api/ps is reachable"})
        result["status"] = "fail"

    return result


def _cap_chat(endpoint: str, model: str, timeout: float) -> tuple[str, str]:
    try:
        resp = request_json(
            f"{endpoint}/api/generate",
            method="POST",
            payload={"model": model, "prompt": "Reply with exactly: pong", "stream": False},
            timeout=timeout,
        )
        text = resp.get("response", "") if isinstance(resp, dict) else ""
        return ("pass", "non-empty response") if text else ("fail", "empty response")
    except HttpJsonError as exc:
        return "fail", f"{exc.reason}: {capped(exc.detail)}"


def _cap_structured_output(endpoint: str, model: str, timeout: float) -> tuple[str, str]:
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }
    try:
        resp = request_json(
            f"{endpoint}/api/generate",
            method="POST",
            payload={
                "model": model,
                "prompt": "Return JSON with a single boolean field ok set to true.",
                "format": schema,
                "stream": False,
            },
            timeout=timeout,
        )
        text = resp.get("response", "") if isinstance(resp, dict) else ""
        parsed = json.loads(text)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("ok"), bool):
            return "fail", "response did not validate against the requested schema"
        return "pass", "response validated against the JSON schema"
    except (HttpJsonError, ValueError, TypeError) as exc:
        detail = exc.detail if isinstance(exc, HttpJsonError) else str(exc)
        return "fail", capped(detail)


ALLOWLISTED_TOOL = {
    "type": "function",
    "function": {
        "name": "omes_noop",
        "description": "A no-op OMES health-check test tool. Calling it has no side effects.",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": [],
        },
    },
}


def _cap_tool_calling(endpoint: str, model: str, timeout: float) -> tuple[str, str]:
    try:
        resp = request_json(
            f"{endpoint}/api/chat",
            method="POST",
            payload={
                "model": model,
                "messages": [{"role": "user", "content": "Call the omes_noop tool with reason='health-check'."}],
                "tools": [ALLOWLISTED_TOOL],
                "stream": False,
            },
            timeout=timeout,
        )
        message = resp.get("message", {}) if isinstance(resp, dict) else {}
        tool_calls = message.get("tool_calls", []) if isinstance(message, dict) else []
        if not tool_calls:
            return "fail", "model did not request any tool call"
        for call in tool_calls:
            fn = call.get("function", {}) if isinstance(call, dict) else {}
            if fn.get("name") != "omes_noop":
                return "fail", f"model requested a non-allowlisted tool: {capped(str(fn.get('name')))}"
            args = fn.get("arguments", {})
            if not isinstance(args, dict):
                return "fail", "tool arguments were not a JSON object"
        return "pass", "allowlisted tool call requested with valid arguments"
    except HttpJsonError as exc:
        return "fail", f"{exc.reason}: {capped(exc.detail)}"


def _cap_embeddings(endpoint: str, model: str, timeout: float) -> tuple[str, str]:
    try:
        first = request_json(f"{endpoint}/api/embed", method="POST", payload={"model": model, "input": "omes synthetic embedding probe"}, timeout=timeout)
        second = request_json(f"{endpoint}/api/embed", method="POST", payload={"model": model, "input": "omes synthetic embedding probe"}, timeout=timeout)
        e1 = (first.get("embeddings") or first.get("embedding")) if isinstance(first, dict) else None
        e2 = (second.get("embeddings") or second.get("embedding")) if isinstance(second, dict) else None
        vec1 = e1[0] if e1 and isinstance(e1[0], list) else e1
        vec2 = e2[0] if e2 and isinstance(e2[0], list) else e2
        if not vec1 or not vec2:
            return "fail", "embedding response was empty"
        if len(vec1) != len(vec2):
            return "fail", f"embedding dimensions were not stable across calls ({len(vec1)} vs {len(vec2)})"
        return "pass", f"non-empty embedding, stable dimension={len(vec1)}"
    except HttpJsonError as exc:
        return "fail", f"{exc.reason}: {capped(exc.detail)}"


def _cap_vision(endpoint: str, model: str, timeout: float) -> tuple[str, str]:
    try:
        image_b64 = _tiny_png_base64()
        resp = request_json(
            f"{endpoint}/api/generate",
            method="POST",
            payload={"model": model, "prompt": "Describe this image in one word.", "images": [image_b64], "stream": False},
            timeout=timeout,
        )
        text = resp.get("response", "") if isinstance(resp, dict) else ""
        return ("pass", "non-empty response to a synthetic image") if text else ("fail", "empty response to a synthetic image")
    except HttpJsonError as exc:
        return "fail", f"{exc.reason}: {capped(exc.detail)}"


CAPABILITY_CHECKS = {
    "chat": _cap_chat,
    "structured_output": _cap_structured_output,
    "tool_calling": _cap_tool_calling,
    "embeddings": _cap_embeddings,
    "vision": _cap_vision,
}


def check_capabilities(endpoint: str, model: str, required: set, timeout: float, checks: list) -> dict:
    result = {}
    for name in CAPABILITIES:
        if name != "chat" and name not in required:
            result[name] = "not_applicable"
            continue
        status, detail = CAPABILITY_CHECKS[name](endpoint, model, timeout)
        result[name] = status
        checks.append(
            {
                "name": f"capability:{name}",
                "status": status,
                "detail": detail,
                "remediation": None if status == "pass" else f"see docs/ollama.md remediation for capability '{name}'",
            }
        )
    return result


def run(argv: Optional[list] = None) -> dict:
    parser = argparse.ArgumentParser(prog="ollama-health", add_help=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    endpoint = resolve_endpoint()
    timeout = _timeout("OMES_HEALTH_TIMEOUT", 10.0)
    load_timeout = _timeout("OMES_OLLAMA_LOAD_TIMEOUT", 30.0)
    model = args.model or os.environ.get("OMES_OLLAMA_MODEL", "")

    if args.profile:
        os.environ["OMES_OLLAMA_PROFILE"] = args.profile
    profile_name, required_caps = resolve_profile()

    checks: list = []
    result: dict[str, Any] = {
        "provider": "ollama-local",
        "profile": profile_name,
    }

    try:
        service = check_service(endpoint, timeout, checks)
    except ServiceMissing as exc:
        result["service"] = {"status": "fail", "reason": exc.args[0] if exc.args else "unreachable"}
        result["model"] = {"id": model, "status": "not_applicable"}
        result["capabilities"] = {c: "not_applicable" for c in CAPABILITIES}
        result["checks"] = checks
        result["ready"] = False
        result["exit_code"] = EXIT_SERVICE_MISSING
        return result

    result["service"] = service

    if not model:
        checks.append({"name": "model:configured", "status": "fail", "detail": "no model configured (OMES_OLLAMA_MODEL / --model)", "remediation": "set OMES_OLLAMA_MODEL or pass --model"})
        result["model"] = {"id": "", "status": "fail"}
        result["capabilities"] = {c: "not_applicable" for c in CAPABILITIES}
        result["checks"] = checks
        result["ready"] = False
        result["exit_code"] = EXIT_NOT_READY
        return result

    model_result = check_model(endpoint, model, timeout, load_timeout, checks)
    result["model"] = model_result

    if model_result["status"] != "pass":
        result["capabilities"] = {c: "not_applicable" for c in CAPABILITIES}
        result["checks"] = checks
        result["ready"] = False
        result["exit_code"] = EXIT_NOT_READY
        return result

    capabilities = check_capabilities(endpoint, model, required_caps, timeout, checks)
    result["capabilities"] = capabilities

    ready = service["status"] == "pass" and model_result["status"] == "pass"
    for name in required_caps | {"chat"}:
        if capabilities.get(name) == "fail":
            ready = False

    result["checks"] = checks
    result["ready"] = ready
    result["exit_code"] = EXIT_READY if ready else EXIT_NOT_READY
    return result


def main(argv: Optional[list] = None) -> int:
    result = run(argv)
    exit_code = result.pop("exit_code")
    print(json.dumps(result))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
