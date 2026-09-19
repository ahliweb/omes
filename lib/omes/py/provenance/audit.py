#!/usr/bin/env python3
"""lib/omes/py/provenance/audit.py - supply-chain provenance audit
(issue #84): `omes audit provenance [--profile <name>] [--json]`.

Standard library only (ADR-0012). Reads every
`<state-dir>/provenance/<component>.json` record written by
lib/omes/py/provenance/record.py (or the bash helper that wraps it),
evaluates each for a checksum mismatch (FAIL, fail-closed), a mutable
installer-source reference (`main`/`latest` in the URL - WARN), and
missing required metadata (WARN); it also lists (never executes)
executable files found under managed skill/plugin/MCP paths, reporting
mode/size/sha256 for operator review.

This module NEVER executes a file it discovers, NEVER reads a
credential/secret file, and NEVER dumps `os.environ`.

Usage:
  python3 audit.py [--state-dir <dir>] [--profile <name>] [--hermes-home <dir>] [--json]

Exit codes: 0 clean (no findings), 7 findings present.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import stat
import sys
from typing import Optional

EXIT_OK = 0
EXIT_FINDINGS = 7

REQUIRED_FIELDS = ("component", "installer_source_url", "resolved_version", "install_time")

# Reference substrings that identify a mutable (non-pinned) URL - a
# branch/tag that can change after the fact rather than an immutable
# release asset or commit SHA.
_MUTABLE_URL_HINTS = ("/main/", "/master/", "/latest/", "@latest", "/HEAD/")

# Relative sub-paths under $HERMES_HOME that hold operator-facing,
# potentially-executable managed content (skills, plugins, MCP server
# entry points). This module only ever STATs and hashes files here -
# it never invokes them.
_MANAGED_EXECUTABLE_SUBDIRS = ("skills", "plugins", "mcp")


def _provenance_dir(state_dir: str) -> str:
    return os.path.join(state_dir, "provenance")


def load_records(state_dir: str) -> list:
    prov_dir = _provenance_dir(state_dir)
    records = []
    if not os.path.isdir(prov_dir):
        return records
    for path in sorted(glob.glob(os.path.join(prov_dir, "*.json"))):
        component = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            records.append({"component": component, "path": path, "data": data, "error": None})
        except (OSError, ValueError) as exc:
            records.append({"component": component, "path": path, "data": None, "error": str(exc)})
    return records


def _is_mutable_url(url: Optional[str]) -> bool:
    if not url:
        return False
    return any(hint in url for hint in _MUTABLE_URL_HINTS)


def evaluate_record(component: str, data: Optional[dict], load_error: Optional[str]) -> list:
    """Returns a list of finding dicts for one component's provenance
    record. Never returns more than one finding per distinct problem
    type for a given component.
    """
    findings = []

    if load_error is not None:
        findings.append(
            {
                "component": component,
                "severity": "FAIL",
                "kind": "missing_metadata",
                "detail": f"provenance record could not be read/parsed: {load_error}",
            }
        )
        return findings

    if data is None:
        findings.append(
            {
                "component": component,
                "severity": "FAIL",
                "kind": "missing_metadata",
                "detail": "provenance record is empty",
            }
        )
        return findings

    missing = [f for f in REQUIRED_FIELDS if not data.get(f)]
    if missing:
        findings.append(
            {
                "component": component,
                "severity": "WARN",
                "kind": "missing_metadata",
                "detail": f"missing fields: {', '.join(missing)}",
            }
        )

    checksum = data.get("checksum") or {}
    expected = checksum.get("expected")
    actual = checksum.get("actual")
    status = checksum.get("status") or "unknown"

    if expected and actual and expected != actual:
        findings.append(
            {
                "component": component,
                "severity": "FAIL",
                "kind": "checksum_mismatch",
                "detail": f"recorded checksum mismatch (expected {expected}, actual {actual}) - fail closed",
            }
        )
    elif status not in ("verified", "pinned", "locally-built"):
        findings.append(
            {
                "component": component,
                "severity": "WARN",
                "kind": "checksum_unverified",
                "detail": f"checksum status is '{status}' (no verified pin was used at install time)",
            }
        )

    if _is_mutable_url(data.get("installer_source_url")):
        findings.append(
            {
                "component": component,
                "severity": "WARN",
                "kind": "mutable_url",
                "detail": f"installer source references a mutable ref: {data.get('installer_source_url')}",
            }
        )

    return findings


def _sha256_file(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def review_managed_executables(hermes_home: Optional[str]) -> list:
    """Lists (mode/size/sha256), but NEVER executes, executable files
    under $HERMES_HOME/{skills,plugins,mcp}/**. Read-only os.stat/hash
    only.
    """
    review = []
    if not hermes_home:
        return review

    for sub in _MANAGED_EXECUTABLE_SUBDIRS:
        base = os.path.join(hermes_home, sub)
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for name in files:
                path = os.path.join(root, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if not (st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)):
                    continue
                review.append(
                    {
                        "path": path,
                        "mode": oct(stat.S_IMODE(st.st_mode)),
                        "size": st.st_size,
                        "sha256": _sha256_file(path),
                    }
                )
    return review


def audit(state_dir: str, profile: Optional[str], hermes_home: Optional[str]) -> dict:
    records = load_records(state_dir)
    findings = []
    components = []

    for rec in records:
        component = rec["component"]
        data = rec["data"] or {}
        if profile and data.get("profile") and data.get("profile") != profile:
            continue
        components.append(
            {
                "component": component,
                "profile": data.get("profile"),
                "resolved_version": data.get("resolved_version"),
                "checksum_status": (data.get("checksum") or {}).get("status"),
                "installer_source_url": data.get("installer_source_url"),
            }
        )
        findings.extend(evaluate_record(component, rec["data"], rec["error"]))

    review = review_managed_executables(hermes_home)

    return {
        "ok": len(findings) == 0,
        "profile": profile,
        "components": components,
        "findings": findings,
        "review": review,
    }


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description="Audit supply-chain provenance records.")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--hermes-home", default=None)
    args = parser.parse_args(argv)

    result = audit(args.state_dir, args.profile, args.hermes_home)
    print(json.dumps(result))
    return EXIT_OK if result["ok"] else EXIT_FINDINGS


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
