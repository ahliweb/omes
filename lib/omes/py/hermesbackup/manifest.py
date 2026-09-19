"""hermesbackup.manifest - MANIFEST/META read+write for a backup session.

MANIFEST is a JSON-lines file, one object per file:
    {"category": "config", "path": "config.yaml", "sha256": "...", "size": 123, "mode": 384}

`path` is relative to $HERMES_HOME (no leading slash) and doubles as the
tar member name in archive.tar, so the archive's on-disk layout mirrors
HERMES_HOME directly. `mode` is the POSIX permission bits (stat.S_IMODE),
recorded so restore can preserve permissions.

META is a single JSON object: hermes_version, hermes_home, classes,
timestamp, omes_version, include_secrets. No secret VALUES are ever
written to either file - only paths, sizes, and checksums.
"""
from __future__ import annotations

import json
from pathlib import Path

MANIFEST_FILENAME = "MANIFEST"
META_FILENAME = "META"
ARCHIVE_FILENAME = "archive.tar"


class ManifestError(Exception):
    """Raised for a missing, unreadable, or structurally corrupt MANIFEST/META."""


def write_manifest(dest_dir: Path, entries) -> None:
    path = dest_dir / MANIFEST_FILENAME
    with open(path, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, sort_keys=True))
            fh.write("\n")
    path.chmod(0o600)


def read_manifest(dest_dir: Path):
    path = dest_dir / MANIFEST_FILENAME
    if not path.is_file():
        raise ManifestError(f"MANIFEST not found: {path}")
    entries = []
    required = {"category", "path", "sha256", "size", "mode"}
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ManifestError(
                    f"MANIFEST line {lineno} is not valid JSON: {exc}"
                ) from exc
            if not isinstance(obj, dict) or not required.issubset(obj):
                raise ManifestError(
                    f"MANIFEST line {lineno} is missing required fields "
                    f"(need {sorted(required)})"
                )
            entries.append(obj)
    return entries


def write_meta(dest_dir: Path, meta: dict) -> None:
    path = dest_dir / META_FILENAME
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
        fh.write("\n")
    path.chmod(0o600)


def read_meta(dest_dir: Path) -> dict:
    path = dest_dir / META_FILENAME
    if not path.is_file():
        raise ManifestError(f"META not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"META is not valid JSON: {exc}") from exc
