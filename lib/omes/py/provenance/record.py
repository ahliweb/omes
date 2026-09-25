#!/usr/bin/env python3
"""lib/omes/py/provenance/record.py - record a supply-chain provenance
entry for one component (issue #84).

Standard library only (ADR-0012). Writes
`<state-dir>/provenance/<component>.json` (dir mode 0700, file mode
0600) describing: the installer source URL, resolved version, checksum
status, install time, profile, and (optional) package-manager
provenance. This module NEVER writes a credential, token, or any
`.env` content into the record - callers (bash modules) are
responsible for only ever passing non-secret fields.

This is the "documented helper other modules may call" from issue #84:
any module that installs a component may call this script (directly,
or via the bash wrapper `provenance_record_component` in
lib/omes/cmd/audit-provenance.sh) to register a provenance record in
the same place `omes audit provenance` reads from.

Usage:
  python3 record.py --state-dir <dir> --component <name> [--profile <name>] < payload.json

payload.json (all optional except where noted):
  {
    "installer_source_url": "https://...",
    "resolved_version": "hermes 1.2.3",
    "install_time": "2026-09-19T01:00:00Z",
    "checksum": {
      "algorithm": "sha256",
      "expected": "<hex>"|null,
      "actual": "<hex>"|null,
      "status": "verified"|"pinned"|"unverified"|"unknown"|"locally-built"|"package_manager_verified"
    },
    "package_manager": {"name": "apt"|"uv"|"pipx", "package": "...", "version": "...", "origin": "..."|null}
  }

Exit codes: 0 on success, 1 on invalid input/usage.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import stat
import sys
import tempfile

EXIT_OK = 0
EXIT_ERROR = 1

VALID_CHECKSUM_STATUSES = {
    "verified",
    "pinned",
    "unverified",
    "unknown",
    "locally-built",
    "mismatch",
    # A package manager (apt/dpkg) verified the package's signature/hash
    # itself at install time; OMES did not independently re-verify a
    # pinned checksum, but this is not "unverified" either (issue #84's
    # apt/uv/pipx package-manager provenance gap).
    "package_manager_verified",
    # Issue #236: a declared model/runtime artifact path did not exist (or
    # was not a regular file) at verification time - distinct from
    # "mismatch" (the file exists but its digest disagrees with the
    # pinned expectation). `omes audit provenance` treats this the same
    # as a mismatch: FAIL, fail-closed.
    "missing",
}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _provenance_dir(state_dir: str) -> str:
    return os.path.join(state_dir, "provenance")


def build_record(component: str, profile: str, payload: dict) -> dict:
    checksum = payload.get("checksum") or {}
    status = checksum.get("status") or "unknown"
    if status not in VALID_CHECKSUM_STATUSES:
        status = "unknown"

    return {
        "component": component,
        "profile": profile or None,
        "installer_source_url": payload.get("installer_source_url") or None,
        "resolved_version": payload.get("resolved_version") or None,
        "install_time": payload.get("install_time") or _now(),
        "checksum": {
            "algorithm": checksum.get("algorithm") or None,
            "expected": checksum.get("expected") or None,
            "actual": checksum.get("actual") or None,
            "status": status,
        },
        "package_manager": payload.get("package_manager") or None,
        # Issue #236: optional {"size": int, "mtime": float} stat snapshot
        # of a declared model/runtime artifact at the time it was last
        # actually hashed - lib/omes/py/provenance/artifacts.py uses this
        # to skip re-hashing an unchanged multi-GB file on a later run.
        # Never set by any other caller; absent for every non-artifact
        # component.
        "artifact_stat": payload.get("artifact_stat") or None,
        "recorded_at": _now(),
    }


def write_record(state_dir: str, component: str, record: dict) -> str:
    prov_dir = _provenance_dir(state_dir)
    os.makedirs(prov_dir, mode=0o700, exist_ok=True)
    os.chmod(prov_dir, 0o700)

    target = os.path.join(prov_dir, f"{component}.json")
    fd, tmp_path = tempfile.mkstemp(dir=prov_dir, prefix=f".{component}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(record, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        os.replace(tmp_path, target)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return target


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description="Record a supply-chain provenance entry.")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--component", required=True)
    parser.add_argument("--profile", default="")
    args = parser.parse_args(argv)

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": f"invalid input JSON: {exc}"}))
        return EXIT_ERROR

    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "error": "input JSON must be an object"}))
        return EXIT_ERROR

    record = build_record(args.component, args.profile, payload)
    try:
        path = write_record(args.state_dir, args.component, record)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": f"could not write provenance record: {exc}"}))
        return EXIT_ERROR

    print(json.dumps({"ok": True, "path": path, "record": record}))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
