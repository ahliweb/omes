#!/usr/bin/env python3
"""lib/omes/py/provenance/artifacts.py - model/runtime artifact
provenance/integrity evidence (issue #236, threat AI-06,
docs/ai-data-privacy-and-model-security.md section 10).

Threat AI-06 is that a local model is trusted merely because it is
local. #215 verifies the Hermes gateway's non-root identity and denies
outbound network access from it; it does not verify that the model
weights / runtime binaries the local inference server actually loads
are the ones the operator expects. This module closes that specific
gap by REUSING the existing supply-chain provenance/checksum model
(issue #84, lib/omes/py/provenance/{record,audit}.py) rather than
building a second, parallel evidence system:

- an operator explicitly DECLARES the artifact paths OMES should track
  (component name, absolute path, optional expected SHA-256 pin) as
  OMES's own state (`ai.model_artifacts.declared`, read via
  `state_get`/`OMES_AI_MODEL_ARTIFACTS` by the bash caller - see
  lib/omes/cmd/audit-provenance.sh) - this module NEVER scans
  `$HERMES_HOME`, `.hermes/`, or any Hermes-owned directory looking for
  model files itself (ADR-0017: OMES must not read internal Hermes
  state; a local inference server's model directory is not an OMES-
  owned interface either);
- `verify()` computes/re-verifies a SHA-256 digest for each declared
  artifact and writes a provenance record via
  lib/omes/py/provenance/record.py under the component name
  `model-artifact:<name>`, so the existing `omes audit provenance`
  reader, findings evaluator (checksum mismatch -> FAIL, missing
  artifact -> FAIL, unpinned -> WARN), and executable-review listing all
  apply to these records with NO code duplicated;
- `summarize()` is the cheap, hashing-free read path `omes health
  ai-privacy` uses on every invocation: it only stats/reads the small
  JSON provenance records this module already wrote, never the
  multi-gigabyte artifact bytes themselves.

Performance / frequency (see docs/provenance.md section 1b): hashing a
multi-GB model file on every health check would be prohibitively slow
and is unnecessary - the artifact is expected to change rarely.
`verify()` is therefore a deliberately separate, explicit,
operator/cron-triggered operation (`omes audit provenance
--verify-artifacts`), and even then it SKIPS re-hashing a declared
artifact whose (size, mtime) stat tuple is unchanged since the last
recorded verification (unless `--force` is passed) - see
`_needs_rehash()`. This is a documented, bounded trade-off: a crafted
same-size, same-mtime replacement would not be caught until the next
forced/`--force` run (see docs/provenance.md's residual-limits note).

This module NEVER executes a declared artifact, NEVER reads more than
the declared path's bytes for hashing, and NEVER prints a credential or
prompt value - only bounded metadata (component name, path, size,
mtime, sha256 digest, checksum status).
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import sys
from typing import Any, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _THIS_DIR)
import record as record_mod  # noqa: E402

EXIT_OK = 0
EXIT_FINDINGS = 7
EXIT_ERROR = 1

#: Prefix namespacing every model-artifact provenance record's component
#: name, so it can never collide with an apt package name, `hermes`
#: itself, or a `uv:`/`pipx:`-prefixed discovery record (docs/provenance.md
#: section 1a).
COMPONENT_PREFIX = "model-artifact:"

#: A declared artifact's operator-supplied component name must be a short
#: safe slug - it becomes part of a filename
#: (`<state-dir>/provenance/model-artifact:<name>.json`), so it is
#: validated the same way any other path-derived identifier in this
#: codebase is: a strict allowlisted character class, no `/`, no `..`.
_COMPONENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: A pinned expected digest, if supplied, must look like a lowercase
#: hex-encoded SHA-256 digest - never accepted as opaque free text.
_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")

#: Bounded reason vocabulary this module ever assigns - mirrors
#: lib/omes/py/privacy/posture_evidence.py's MODEL_ARTIFACT_REASONS
#: (kept as an independent copy rather than a cross-package import, per
#: this codebase's convention of not importing across lib/omes/py/<pkg>/
#: boundaries - see lib/omes/py/jobs/audit.py's module docstring for the
#: same convention).
REASON_CONSISTENT = "consistent"
REASON_CHECKSUM_MISMATCH = "checksum_mismatch"
REASON_MISSING_ARTIFACT = "missing_artifact"
REASON_UNVERIFIED_NO_PIN = "unverified_no_pin"
REASON_STALE = "stale"
REASON_NOT_DECLARED = "not_declared"

#: Default maximum age, in seconds, of the OLDEST declared artifact's last
#: verification before `summarize()` reports "stale" (WARN). 7 days:
#: hashing multi-GB artifacts is expensive, so the cadence is much looser
#: than the 24h default in posture_evidence.py's general evidence
#: staleness window, but a verification that has not run in over a week
#: is still treated as effectively unverified rather than trusted
#: indefinitely.
DEFAULT_MAX_ARTIFACT_AGE_SECONDS = 7 * 24 * 60 * 60


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _now_iso() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: Any) -> Optional[datetime.datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def _sha256_file(path: str, chunk_size: int = 1024 * 1024) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(chunk_size), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _component_for(name: str) -> str:
    return f"{COMPONENT_PREFIX}{name}"


def _provenance_path(state_dir: str, name: str) -> str:
    return os.path.join(state_dir, "provenance", f"{_component_for(name)}.json")


def _load_previous_record(state_dir: str, name: str) -> Optional[dict]:
    path = _provenance_path(state_dir, name)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _needs_rehash(previous: Optional[dict], size: int, mtime: float, force: bool) -> bool:
    """Returns True unless a previous record exists, its checksum was
    actually computed (not itself already a fail-closed "missing"/
    "unknown" state), and its recorded (size, mtime) stat snapshot
    matches the artifact's CURRENT stat exactly. See module docstring for
    the documented residual limit of this cache."""
    if force or previous is None:
        return True
    checksum = previous.get("checksum") or {}
    if checksum.get("status") not in ("verified", "unverified", "mismatch"):
        return True
    stat_snapshot = previous.get("artifact_stat") or {}
    return not (stat_snapshot.get("size") == size and stat_snapshot.get("mtime") == mtime)


def verify_one(entry: dict, state_dir: str, force: bool) -> dict:
    """Verifies one declared artifact entry
    ({"component": str, "path": str, "expected_sha256": str|None}) and
    writes/refreshes its provenance record. Returns a bounded result dict
    (never raises - a malformed entry or unreadable path is reported as a
    finding, not an exception, since this is a diagnostic, not a
    precondition for anything else)."""
    raw_name = entry.get("component") if isinstance(entry, dict) else None
    path = entry.get("path") if isinstance(entry, dict) else None
    expected = entry.get("expected_sha256") if isinstance(entry, dict) else None

    if not isinstance(raw_name, str) or not _COMPONENT_NAME_PATTERN.match(raw_name):
        return {
            "component": raw_name if isinstance(raw_name, str) else "unknown",
            "ok": False,
            "error": "invalid or missing component name",
        }
    if not isinstance(path, str) or not path.strip() or not os.path.isabs(path):
        return {"component": raw_name, "ok": False, "error": "path must be a non-empty absolute path"}

    if expected is not None and (not isinstance(expected, str) or not _SHA256_HEX_PATTERN.match(expected.lower())):
        return {"component": raw_name, "ok": False, "error": "expected_sha256 must be a 64-hex-char sha256 digest"}
    expected_norm = expected.lower() if isinstance(expected, str) else None

    exists = os.path.isfile(path) and not os.path.islink(path)
    hashed = False
    if not exists:
        actual = None
        status = "missing"
        stat_snapshot = None
    else:
        st = os.stat(path)
        size, mtime = st.st_size, st.st_mtime
        previous = _load_previous_record(state_dir, raw_name)
        if _needs_rehash(previous, size, mtime, force):
            actual = _sha256_file(path)
            hashed = True
        else:
            actual = ((previous or {}).get("checksum") or {}).get("actual")
        if actual is None:
            status = "unknown"
        elif expected_norm is None:
            status = "unverified"
        elif actual == expected_norm:
            status = "verified"
        else:
            status = "mismatch"
        stat_snapshot = {"size": size, "mtime": mtime}

    payload = {
        "installer_source_url": None,
        "resolved_version": None,
        "install_time": _now_iso(),
        "checksum": {
            "algorithm": "sha256",
            "expected": expected_norm,
            "actual": actual,
            "status": status,
        },
        "artifact_stat": stat_snapshot,
    }
    record = record_mod.build_record(_component_for(raw_name), "model-artifact", payload)
    try:
        record_mod.write_record(state_dir, _component_for(raw_name), record)
    except OSError as exc:
        return {"component": raw_name, "ok": False, "error": f"could not write provenance record: {exc}"}

    return {
        "component": raw_name,
        "path": path,
        "ok": status in ("verified", "unverified"),
        "checksum_status": status,
        "hashed": hashed,
        "recorded_at": record["recorded_at"],
    }


def verify(declared: list, state_dir: str, force: bool) -> dict:
    results = []
    findings = []
    if not isinstance(declared, list):
        declared = []
    for entry in declared:
        result = verify_one(entry, state_dir, force)
        results.append(result)
        if not result.get("ok", False):
            status = result.get("checksum_status")
            if status == "mismatch":
                findings.append(
                    {
                        "component": result.get("component"),
                        "severity": "FAIL",
                        "kind": "checksum_mismatch",
                        "detail": "declared model/runtime artifact checksum did not match the pinned expected digest - fail closed",
                    }
                )
            elif status == "missing":
                findings.append(
                    {
                        "component": result.get("component"),
                        "severity": "FAIL",
                        "kind": "artifact_missing",
                        "detail": "declared model/runtime artifact was not found on disk at verification time",
                    }
                )
            else:
                findings.append(
                    {
                        "component": result.get("component"),
                        "severity": "FAIL",
                        "kind": "invalid_declaration",
                        "detail": result.get("error") or "could not verify declared artifact",
                    }
                )
    return {"ok": len(findings) == 0, "checked_at": _now_iso(), "artifacts": results, "findings": findings}


def summarize(state_dir: str, now: Optional[datetime.datetime] = None, max_age_seconds: int = DEFAULT_MAX_ARTIFACT_AGE_SECONDS) -> dict:
    """Cheap, hashing-free read of every already-recorded
    `model-artifact:*` provenance record - the shape `omes health
    ai-privacy` embeds as `model_artifact_provenance_source` on EVERY
    invocation (never re-hashing artifact bytes). See module docstring
    for why hashing itself is a separate, explicit operation."""
    now = now or _now()
    prov_dir = os.path.join(state_dir, "provenance")
    records = []
    if os.path.isdir(prov_dir):
        for path in sorted(glob.glob(os.path.join(prov_dir, f"{COMPONENT_PREFIX}*.json"))):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    records.append(json.load(fh))
            except (OSError, ValueError):
                records.append(None)

    declared_count = len(records)
    if declared_count == 0:
        return {
            "available": False,
            "status": "unknown",
            "reason": REASON_NOT_DECLARED,
            "declared_count": 0,
            "verified_count": 0,
            "last_verified_at": None,
            "source": "model-artifact-provenance-source:unavailable",
        }

    verified_count = 0
    mismatch = False
    missing = False
    unverified = False
    unparsable = False
    oldest: Optional[datetime.datetime] = None
    for rec in records:
        if not isinstance(rec, dict):
            unparsable = True
            continue
        checksum = rec.get("checksum") or {}
        status = checksum.get("status")
        if status == "verified":
            verified_count += 1
        elif status == "mismatch":
            mismatch = True
        elif status == "missing":
            missing = True
        elif status == "unverified":
            unverified = True
        else:
            unparsable = True

        recorded_at = _parse_iso(rec.get("recorded_at"))
        if recorded_at is not None and (oldest is None or recorded_at < oldest):
            oldest = recorded_at

    stale = oldest is None or (now - oldest).total_seconds() > max_age_seconds

    if mismatch:
        status_out, reason = "fail", REASON_CHECKSUM_MISMATCH
    elif missing:
        status_out, reason = "fail", REASON_MISSING_ARTIFACT
    elif stale:
        status_out, reason = "warn", REASON_STALE
    elif unverified:
        status_out, reason = "warn", REASON_UNVERIFIED_NO_PIN
    elif unparsable:
        status_out, reason = "unknown", "unknown"
    else:
        status_out, reason = "pass", REASON_CONSISTENT

    return {
        "available": True,
        "status": status_out,
        "reason": reason,
        "declared_count": declared_count,
        "verified_count": verified_count,
        "last_verified_at": oldest.strftime("%Y-%m-%dT%H:%M:%SZ") if oldest else None,
        "source": "model-artifact-provenance-source:issue-236",
    }


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description="Verify or summarize declared model/runtime artifact provenance.")
    sub = parser.add_subparsers(dest="action", required=True)

    verify_p = sub.add_parser("verify", help="Hash and record declared artifacts (reads declared list from stdin JSON).")
    verify_p.add_argument("--state-dir", required=True)
    verify_p.add_argument("--force", action="store_true")

    summary_p = sub.add_parser("summarize", help="Print the cheap, hashing-free summary used by 'omes health ai-privacy'.")
    summary_p.add_argument("--state-dir", required=True)

    args = parser.parse_args(argv)

    if args.action == "verify":
        try:
            raw = sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": f"invalid input JSON: {exc}"}))
            return EXIT_ERROR
        declared = payload.get("declared") if isinstance(payload, dict) else None
        result = verify(declared or [], args.state_dir, args.force)
        print(json.dumps(result))
        return EXIT_OK if result["ok"] else EXIT_FINDINGS

    result = summarize(args.state_dir)
    print(json.dumps(result))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
