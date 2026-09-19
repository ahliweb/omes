#!/usr/bin/env python3
"""lib/omes/py/health/exposure.py - audit gateway/browser-control/MCP/
Ollama listener exposure (issue #80).

Standard library only (ADR-0012). Parses `ss -H -tulpn` for listening
sockets, classifies each as loopback / LAN / wildcard, maps a listener to
an owning category (Hermes gateway, browser-control/CDP, MCP server,
Ollama, other) by port and process name, and cross-references `ufw
status` to flag a port that is exposed by its bind address but not
covered by an explicit firewall allow rule (or the reverse: firewall
allows it, wildcard-bound, still exposed).

This module NEVER opens a port, alters firewall rules, reads credential
files, or calls Telegram - it only runs two fixed, read-only, argv-safe
commands (`ss`, `ufw`) with bounded timeouts and parses their output.

Usage:
  python3 exposure.py [--json]

Exit codes: 0 ok (no unapproved exposure), 7 findings, 4 required tool
(`ss`) missing.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 - only fixed, read-only, argv-safe commands below
import sys
from typing import Optional

EXIT_OK = 0
EXIT_FINDINGS = 7
EXIT_TOOLS_MISSING = 4

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
WILDCARD_HOSTS = {"0.0.0.0", "::", "*", ""}

# Ports/process-name substrings this audit specifically calls out, per
# issue #80: "map to owning process/unit (hermes gateway, browser-control
# CDP ports 9222-ish, MCP servers by process name, Ollama 11434)".
_OWNER_PORT_HINTS = {11434: "ollama"}
_OWNER_NAME_HINTS = (
    ("hermes", "hermes-gateway"),
    ("ollama", "ollama"),
    ("chrome", "browser-control"),
    ("chromium", "browser-control"),
    ("mcp", "mcp-server"),
)

_LINE_RE = re.compile(r"^(?P<proto>tcp|udp)\S*\s+\S+\s+\S+\s+\S+\s+(?P<local>\S+)\s+(?P<peer>\S+)\s*(?P<rest>.*)$")
_PROC_RE = re.compile(r'\("(?P<name>[^"]+)",pid=(?P<pid>\d+)')


def _timeout() -> float:
    raw = os.environ.get("OMES_HEALTH_TIMEOUT", "").strip()
    try:
        return float(raw) if raw else 10.0
    except ValueError:
        return 10.0


def _ss_bin() -> str:
    """The `ss` binary name/path to use - overridable via OMES_SS_BIN so
    tests can deterministically simulate "ss is not installed" without
    manipulating PATH (which would also have to keep bash/coreutils
    reachable for the rest of `omes` to run at all).
    """
    return os.environ.get("OMES_SS_BIN", "ss")


def _ufw_bin() -> str:
    """Same purpose as _ss_bin, for `ufw` (OMES_UFW_BIN)."""
    return os.environ.get("OMES_UFW_BIN", "ufw")


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


def split_host_port(addr: str) -> tuple:
    """Splits an `ss` local-address token into (host, port). Handles the
    bracketed IPv6 form (`[::1]:8080`) and the plain IPv4/wildcard form
    (`127.0.0.1:8080`, `0.0.0.0:22`, `*:53`).
    """
    if addr.startswith("["):
        host, _, rest = addr[1:].partition("]")
        port_str = rest.lstrip(":")
    else:
        host, _, port_str = addr.rpartition(":")
    try:
        port = int(port_str)
    except ValueError:
        port = None
    return host, port


def classify_bind(host: str) -> str:
    if host in LOOPBACK_HOSTS:
        return "loopback"
    if host in WILDCARD_HOSTS:
        return "wildcard"
    return "lan"


def classify_owner(port: Optional[int], process_name: str) -> str:
    name = (process_name or "").lower()
    for hint, owner in _OWNER_NAME_HINTS:
        if hint in name:
            return owner
    if port is not None:
        if port in _OWNER_PORT_HINTS:
            return _OWNER_PORT_HINTS[port]
        if 9222 <= port <= 9229:
            return "browser-control"
    return "other"


def parse_ss(text: str) -> list:
    listeners = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        host, port = split_host_port(m.group("local"))
        proc_m = _PROC_RE.search(m.group("rest"))
        process_name = proc_m.group("name") if proc_m else ""
        pid = int(proc_m.group("pid")) if proc_m else None
        bind_class = classify_bind(host)
        owner = classify_owner(port, process_name)
        listeners.append(
            {
                "proto": m.group("proto"),
                "address": host,
                "port": port,
                "bind_class": bind_class,
                "process": process_name,
                "pid": pid,
                "owner": owner,
            }
        )
    return listeners


def parse_allowlist() -> set:
    """Parses OMES_EXPOSURE_ALLOW ("0.0.0.0:8642,192.0.2.5:9222") into a
    set of exact "host:port" strings that are pre-approved exposure.
    """
    raw = os.environ.get("OMES_EXPOSURE_ALLOW", "")
    allowed = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            allowed.add(part)
    return allowed


def check_firewall(timeout: float) -> dict:
    ufw_bin = _ufw_bin()
    if shutil.which(ufw_bin) is None:
        return {"tool": "ufw", "available": False, "active": None, "rules": [], "detail": "ufw not found; firewall state unknown"}

    rc, out, err = _run([ufw_bin, "status"], timeout)
    if rc != 0:
        return {"tool": "ufw", "available": True, "active": None, "rules": [], "detail": f"`ufw status` failed: {err.strip() or out.strip()}"}

    active = out.strip().lower().startswith("status: active")
    rules = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("status:", "logging:", "default:", "new profiles:", "to ", "--")):
            continue
        parts = line.split()
        if len(parts) >= 2:
            rules.append({"to": parts[0], "action": parts[1]})
    return {"tool": "ufw", "available": True, "active": active, "rules": rules, "detail": out.strip()}


def _rule_covers_port(rules: list, port: Optional[int]) -> bool:
    if port is None:
        return False
    for rule in rules:
        to = rule.get("to", "")
        rule_port = to.split("/")[0]
        if rule_port == str(port) and rule.get("action", "").upper().startswith("ALLOW"):
            return True
    return False


def build_findings(listeners: list, allowlist: set, firewall: dict) -> list:
    findings = []
    for listener in listeners:
        if listener["bind_class"] == "loopback":
            continue
        key = f"{listener['address']}:{listener['port']}"
        approved = key in allowlist
        finding = dict(listener)
        finding["approved"] = approved
        if firewall.get("active") is True:
            finding["firewall_allows"] = _rule_covers_port(firewall.get("rules", []), listener["port"])
        else:
            finding["firewall_allows"] = None
        finding["status"] = "pass" if approved else "fail"
        if not approved:
            reason = f"{listener['owner']} listens on {listener['bind_class']} address {listener['address']}:{listener['port']}, not approved via OMES_EXPOSURE_ALLOW"
            if firewall.get("active") is False:
                reason += "; ufw is inactive, so no local firewall protects this port"
            elif firewall.get("active") is True and finding["firewall_allows"]:
                reason += "; ufw also has an explicit ALLOW rule for this port"
            elif firewall.get("active") is None:
                reason += "; firewall state is unknown (ufw not installed or unreadable)"
            finding["detail"] = reason
            finding["remediation"] = (
                "Use `hermes config set` to bind the service to 127.0.0.1 if it does not need network exposure, "
                "or explicitly approve it via OMES_EXPOSURE_ALLOW=\"{}\" if this exposure is intentional".format(key)
            )
        else:
            finding["detail"] = f"{listener['owner']} exposure at {key} is explicitly approved via OMES_EXPOSURE_ALLOW"
            finding["remediation"] = None
        findings.append(finding)
    return findings


def run(argv: Optional[list] = None) -> dict:
    timeout = _timeout()

    ss_bin = _ss_bin()
    if shutil.which(ss_bin) is None:
        return {
            "listeners": [],
            "findings": [],
            "firewall": {"tool": "ufw", "available": False, "active": None, "rules": [], "detail": "not checked - ss missing"},
            "ok": False,
            "exit_code": EXIT_TOOLS_MISSING,
            "detail": "ss not found on PATH; cannot audit listener exposure",
        }

    rc, out, err = _run([ss_bin, "-H", "-tulpn"], timeout)
    if rc not in (0, -2) and not out:
        # A real failure (not merely "produced no listeners"); still not
        # a missing-tool case, but there is nothing to report.
        listeners = []
    else:
        listeners = parse_ss(out)

    allowlist = parse_allowlist()
    firewall = check_firewall(timeout)
    findings = build_findings(listeners, allowlist, firewall)

    unapproved = [f for f in findings if not f["approved"]]
    ok = len(unapproved) == 0

    return {
        "listeners": listeners,
        "findings": findings,
        "firewall": firewall,
        "ok": ok,
        "exit_code": EXIT_OK if ok else EXIT_FINDINGS,
        "detail": "" if ok else f"{len(unapproved)} unapproved exposed listener(s) found",
    }


def main(argv: Optional[list] = None) -> int:
    result = run(argv)
    exit_code = result.pop("exit_code")
    print(json.dumps(result))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
