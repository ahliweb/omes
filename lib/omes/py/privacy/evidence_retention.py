#!/usr/bin/env python3
"""lib/omes/py/privacy/evidence_retention.py - opt-in retention and
rotation for AI-privacy posture evidence OMES itself persists (issue
#234).

`omes health ai-privacy` (issue #216) is a point-in-time report and
deliberately persists nothing by default (see
docs/ai-data-privacy-and-model-security.md section 11). When an operator
opts in with `omes health ai-privacy --persist`, this module writes ONLY
the already-bounded posture-evidence object
(contracts/ai-egress/v1/privacy-posture-evidence.schema.json) to an
OMES-owned evidence directory (`<state-dir>/ai-privacy-evidence/`) -
never a Hermes-internal path, and never anything wider than that one
closed schema.

Hard boundaries (do not weaken these):

- Nothing is persisted unless the operator explicitly opts in
  (`omes health ai-privacy --persist`). Default behavior is unchanged -
  see lib/omes/cmd/health.sh.
- Every record is re-validated against
  contracts/ai-egress/v1/privacy-posture-evidence.schema.json (via
  lib/omes/py/jobs/schema.py's dependency-free validator, which ALSO
  scans for well-known secret-value shapes and secret-like field names -
  issue #172/#218's `scan_for_raw_secrets`) immediately before writing.
  A record that fails validation for any reason - including an
  unexpected field that could smuggle free text - is REFUSED outright,
  never written partially or "sanitized and written anyway" (fail
  closed).
- Pruning only ever touches files matching this module's own naming
  pattern (`_FILENAME_PATTERN`) inside the resolved evidence directory.
  A directory entry that is a symlink is never deleted, regardless of
  its target - this module cannot tell a symlink an attacker planted
  apart from a legitimate one after the fact, and `persist()` never
  creates one (it writes via `tempfile.mkstemp` + `os.replace`, which is
  always a regular file). A resolved real path whose parent is not the
  resolved evidence directory is likewise refused.
- Directories are created mode 0700, files mode 0600 - the same
  convention as lib/omes/py/provenance/record.py.
- A non-positive or absurdly large retention value fails closed with a
  clear error rather than being silently clamped or rounded - see
  `validate_retention_config`.
- Pruning is idempotent (running it twice with the same arguments over
  the same directory is a no-op the second time) and supports
  `--dry-run` (reports what WOULD be removed without removing it).

This module never reads Hermes's `messages.db` or `.hermes/` directory,
never widens the persisted shape beyond the published posture-evidence
schema, and never executes anything it finds in the evidence directory.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import secrets
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PY_ROOT = os.path.dirname(_THIS_DIR)  # lib/omes/py
sys.path.insert(0, _PY_ROOT)
from jobs.schema import validate  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

#: Subdirectory of the OMES state directory this module owns exclusively.
EVIDENCE_DIR_NAME = "ai-privacy-evidence"

#: OMES's own naming pattern for a persisted evidence record. Pruning
#: refuses to touch any file that does not match this exactly - see
#: module docstring. The embedded timestamp is cosmetic/sortable only;
#: the AUTHORITATIVE age source is each record's own `persisted_at`
#: field (see `_read_persisted_at`), with filesystem mtime as a fallback
#: only when that field cannot be parsed.
_FILENAME_PATTERN = re.compile(r"^ai-privacy-evidence-\d{8}T\d{6}Z-[0-9a-f]{8}\.json$")

_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

#: Bounds on operator-configurable retention. A value outside these
#: bounds is refused rather than silently clamped - "fail closed" per
#: issue #234's requirement, never "do something different than asked".
MIN_MAX_AGE_SECONDS = 1
MAX_MAX_AGE_SECONDS = 10 * 365 * 24 * 60 * 60  # 10 years
MIN_MAX_COUNT = 1
MAX_MAX_COUNT = 100_000

#: Defaults used by lib/omes/cmd/health.sh when the operator does not
#: override them.
DEFAULT_MAX_AGE_DAYS = 90
DEFAULT_MAX_COUNT = 500

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[4]
    / "contracts"
    / "ai-egress"
    / "v1"
    / "privacy-posture-evidence.schema.json"
)


class RetentionConfigError(ValueError):
    """Raised by validate_retention_config for a non-positive/absurd value."""


def validate_retention_config(max_age_seconds: Any, max_count: Any) -> tuple:
    """Validates (max_age_seconds, max_count), returning them as
    `(int, int)`, or raising `RetentionConfigError` with a
    human-readable message. Fails closed: anything that is not a plain
    int, or is non-positive, or is outside the bounded sane range, is
    rejected outright rather than clamped or rounded into range."""
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int):
        raise RetentionConfigError(f"max_age_seconds must be an integer, got {max_age_seconds!r}")
    if not (MIN_MAX_AGE_SECONDS <= max_age_seconds <= MAX_MAX_AGE_SECONDS):
        raise RetentionConfigError(
            f"max_age_seconds must be between {MIN_MAX_AGE_SECONDS} and {MAX_MAX_AGE_SECONDS}, got {max_age_seconds}"
        )
    if isinstance(max_count, bool) or not isinstance(max_count, int):
        raise RetentionConfigError(f"max_count must be an integer, got {max_count!r}")
    if not (MIN_MAX_COUNT <= max_count <= MAX_MAX_COUNT):
        raise RetentionConfigError(f"max_count must be between {MIN_MAX_COUNT} and {MAX_MAX_COUNT}, got {max_count}")
    return max_age_seconds, max_count


def _load_schema() -> dict:
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _now_iso(now: Optional[datetime.datetime] = None) -> str:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _evidence_dir(state_dir: str) -> str:
    return os.path.join(state_dir, EVIDENCE_DIR_NAME)


def persist(state_dir: str, evidence: dict, now: Optional[datetime.datetime] = None) -> dict:
    """Validates `evidence` (expected to be exactly the object
    `lib/omes/py/privacy/posture_evidence.py`'s `evaluate()` returned)
    against the published posture-evidence schema - which ALSO runs the
    secret-value/secret-field-name scan - and, only if it passes, writes
    it to a new file in the OMES-owned evidence directory. Returns
    `{"ok": True, "path": ..., "persisted_at": ...}` or
    `{"ok": False, "error": ..., "details": [...]}`; never raises for a
    validation failure (that is an expected, reportable outcome, not a
    bug in this module)."""
    try:
        schema = _load_schema()
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"could not load posture-evidence schema: {exc}"}

    if not isinstance(evidence, dict):
        return {"ok": False, "error": "evidence must be a JSON object"}

    errors = validate(evidence, schema)
    if errors:
        return {
            "ok": False,
            "error": "refusing to persist: evidence failed schema/secret validation",
            "details": errors,
        }

    now = now or datetime.datetime.now(datetime.timezone.utc)
    persisted_at = _now_iso(now)
    record = {"schema_version": "v1", "persisted_at": persisted_at, "evidence": evidence}

    directory = _evidence_dir(state_dir)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)

    filename = f"ai-privacy-evidence-{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}.json"
    if not _FILENAME_PATTERN.match(filename):  # pragma: no cover - defensive, cannot happen
        return {"ok": False, "error": "internal error: generated filename does not match the expected pattern"}

    target = os.path.join(directory, filename)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".ai-privacy-evidence.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(record, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        os.replace(tmp_path, target)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {"ok": True, "path": target, "persisted_at": persisted_at}


def _resolve_evidence_dir(state_dir: str) -> Optional[str]:
    directory = _evidence_dir(state_dir)
    if not os.path.isdir(directory):
        return None
    return os.path.realpath(directory)


def _read_persisted_at(path: str) -> Optional[datetime.datetime]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("persisted_at")
    if not isinstance(value, str) or not _TIMESTAMP_PATTERN.match(value):
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def prune(
    state_dir: str,
    max_age_seconds: int,
    max_count: int,
    now: Optional[datetime.datetime] = None,
    dry_run: bool = False,
) -> dict:
    """Prunes persisted AI-privacy evidence records under
    `<state_dir>/ai-privacy-evidence/`. Confined strictly to that
    directory: only entries whose filename matches `_FILENAME_PATTERN`,
    that are regular files (never a symlink or anything else), and whose
    resolved real path's parent directory is the resolved evidence
    directory are ever candidates for deletion. Idempotent: pruning an
    already-pruned directory is a no-op, and a missing evidence
    directory is a no-op (never an error) rather than something to
    create.

    `now` is an optional fixed clock (a timezone-aware `datetime`), used
    by tests instead of sleeping to exercise expiry deterministically.
    Age is computed from each candidate's own recorded `persisted_at`
    field when it parses; a record this module cannot parse falls back
    to filesystem mtime rather than being treated as automatically
    ineligible for pruning."""
    max_age_seconds, max_count = validate_retention_config(max_age_seconds, max_count)
    now_dt = now or datetime.datetime.now(datetime.timezone.utc)
    now_ts = now_dt.timestamp()

    result: dict = {
        "ok": True,
        "dry_run": bool(dry_run),
        "evidence_dir": _evidence_dir(state_dir),
        "max_age_seconds": max_age_seconds,
        "max_count": max_count,
        "pruned": [],
        "kept": [],
        "skipped": [],
    }

    resolved_dir = _resolve_evidence_dir(state_dir)
    if resolved_dir is None:
        return result

    candidates = []
    with os.scandir(resolved_dir) as it:
        entries = list(it)

    for entry in entries:
        name = entry.name
        if entry.is_symlink():
            result["skipped"].append({"name": name, "reason": "symlink_refused"})
            continue
        if not _FILENAME_PATTERN.match(name):
            result["skipped"].append({"name": name, "reason": "unrecognized_name"})
            continue
        if not entry.is_file(follow_symlinks=False):
            result["skipped"].append({"name": name, "reason": "not_a_regular_file"})
            continue

        full_path = os.path.join(resolved_dir, name)
        real_path = os.path.realpath(full_path)
        if os.path.dirname(real_path) != resolved_dir:
            result["skipped"].append({"name": name, "reason": "escapes_evidence_dir"})
            continue

        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            result["skipped"].append({"name": name, "reason": "stat_failed"})
            continue

        age_seconds = now_ts - st.st_mtime
        persisted_at = _read_persisted_at(full_path)
        if persisted_at is not None:
            age_seconds = (now_dt - persisted_at).total_seconds()

        candidates.append({"name": name, "path": full_path, "age_seconds": age_seconds, "mtime": st.st_mtime})

    # Newest first, so max_count keeps the most recently persisted
    # records - an index at or beyond max_count is pruned regardless of
    # age, and a record older than max_age_seconds is pruned regardless
    # of its position.
    candidates.sort(key=lambda c: c["mtime"], reverse=True)

    to_prune = []
    to_keep = []
    for index, candidate in enumerate(candidates):
        expired = candidate["age_seconds"] > max_age_seconds
        over_count = index >= max_count
        if expired or over_count:
            to_prune.append(candidate)
        else:
            to_keep.append(candidate)

    for candidate in to_prune:
        if not dry_run:
            try:
                os.remove(candidate["path"])
            except OSError as exc:
                result["skipped"].append({"name": candidate["name"], "reason": f"delete_failed: {exc}"})
                result["ok"] = False
                continue
        result["pruned"].append(candidate["name"])

    for candidate in to_keep:
        result["kept"].append(candidate["name"])

    return result


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description="Persist or prune AI-privacy posture evidence (issue #234).")
    sub = parser.add_subparsers(dest="action", required=True)

    persist_p = sub.add_parser("persist", help="Validate and persist one evidence object (reads JSON from stdin).")
    persist_p.add_argument("--state-dir", required=True)

    prune_p = sub.add_parser("prune", help="Prune persisted evidence records.")
    prune_p.add_argument("--state-dir", required=True)
    prune_p.add_argument("--max-age-seconds", type=int, required=True)
    prune_p.add_argument("--max-count", type=int, required=True)
    prune_p.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)

    if args.action == "persist":
        try:
            raw = sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": f"invalid input JSON: {exc}"}))
            return EXIT_ERROR
        result = persist(args.state_dir, payload)
        print(json.dumps(result))
        return EXIT_OK if result.get("ok") else EXIT_ERROR

    try:
        result = prune(args.state_dir, args.max_age_seconds, args.max_count, dry_run=args.dry_run)
    except RetentionConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return EXIT_USAGE
    print(json.dumps(result))
    return EXIT_OK if result.get("ok") else EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
