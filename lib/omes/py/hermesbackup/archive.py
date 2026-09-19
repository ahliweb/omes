"""hermesbackup.archive - walk HERMES_HOME classes, hash, and tar them.

Stdlib only: hashlib, tarfile, os, stat.
"""
from __future__ import annotations

import hashlib
import os
import stat
import tarfile
from pathlib import Path

from . import classes as classes_mod

_HASH_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def iter_class_entries(hermes_home: Path, class_name: str):
    """Yields (relative_path, absolute_path) for every regular file under
    the class's declared paths that actually exists. Silently skips
    declared paths that do not exist on this install (documented
    upstream paths are not guaranteed present, e.g. a fresh install has
    no cron/ yet)."""
    for rel_root in classes_mod.CLASS_PATHS[class_name]:
        abs_root = hermes_home / rel_root
        if abs_root.is_file():
            yield rel_root, abs_root
        elif abs_root.is_dir():
            for dirpath, _dirnames, filenames in os.walk(abs_root):
                for name in filenames:
                    abs_path = Path(dirpath) / name
                    rel_path = abs_path.relative_to(hermes_home)
                    yield str(rel_path), abs_path
        # else: declared path does not exist - skip.


def preview_classes(hermes_home: Path, class_names):
    """Dry-run preview: paths + sizes only, via os.stat - NEVER opens a
    file's content, so a --dry-run never reads secret contents (issue #82
    acceptance criterion), even when --include-secrets is also passed."""
    preview = []
    for class_name in class_names:
        for rel_path, abs_path in iter_class_entries(hermes_home, class_name):
            try:
                size = abs_path.stat().st_size
            except OSError:
                size = None
            preview.append(
                {"category": class_name, "path": rel_path, "size": size}
            )
    return preview


def build_archive(hermes_home: Path, class_names, dest_dir: Path):
    """Hashes and tars every file in the given classes. Returns the list
    of MANIFEST entry dicts (category, path, sha256, size, mode)."""
    entries = []
    archive_path = dest_dir / "archive.tar"
    with tarfile.open(archive_path, "w") as tar:
        for class_name in class_names:
            for rel_path, abs_path in iter_class_entries(hermes_home, class_name):
                st = abs_path.stat()
                digest = sha256_file(abs_path)
                entries.append(
                    {
                        "category": class_name,
                        "path": rel_path,
                        "sha256": digest,
                        "size": st.st_size,
                        "mode": stat.S_IMODE(st.st_mode),
                    }
                )
                tar.add(abs_path, arcname=rel_path, recursive=False)
    archive_path.chmod(0o600)
    return entries


def verify_archive(dest_dir: Path, entries):
    """Re-hashes every archive member against its MANIFEST entry.
    Returns (ok: bool, problems: list[str]) - never raises for a data
    mismatch (that is the expected corruption-detection outcome), but
    does raise ManifestError-compatible tarfile errors for a truly
    unreadable/corrupt tar container (caller should catch tarfile errors
    separately and treat them as corruption too)."""
    archive_path = dest_dir / "archive.tar"
    problems = []
    with tarfile.open(archive_path, "r") as tar:
        members = {m.name: m for m in tar.getmembers()}
        for entry in entries:
            member = members.get(entry["path"])
            if member is None:
                problems.append(f"missing from archive: {entry['path']}")
                continue
            fh = tar.extractfile(member)
            if fh is None:
                problems.append(f"unreadable archive member: {entry['path']}")
                continue
            h = hashlib.sha256()
            while True:
                chunk = fh.read(_HASH_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
            if h.hexdigest() != entry["sha256"]:
                problems.append(f"checksum mismatch: {entry['path']}")
    return (len(problems) == 0, problems)


def extract_entries(dest_dir: Path, entries, hermes_home: Path):
    """Extracts the given MANIFEST entries from archive.tar into
    hermes_home, preserving permissions from the manifest's recorded
    mode. Caller is responsible for checksum verification beforehand and
    for deciding which entries to pass (class/secret filtering)."""
    archive_path = dest_dir / "archive.tar"
    restored = []
    with tarfile.open(archive_path, "r") as tar:
        members = {m.name: m for m in tar.getmembers()}
        for entry in entries:
            member = members.get(entry["path"])
            if member is None:
                continue
            dest_path = hermes_home / entry["path"]
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            fh = tar.extractfile(member)
            if fh is None:
                continue
            with open(dest_path, "wb") as out:
                out.write(fh.read())
            os.chmod(dest_path, entry["mode"])
            restored.append(entry["path"])
    return restored
