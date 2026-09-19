"""hermesbackup.backup - create/list/verify/restore orchestration.

Stdlib only (ADR-0012). Retention follows the same OMES_BACKUP_KEEP
semantics as lib/omes/backup.sh's backup_prune (default 10).
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from . import archive, classes as classes_mod, manifest, paths


class BackupError(Exception):
    pass


def _now_ts() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _hermes_version() -> str:
    try:
        out = subprocess.run(
            ["hermes", "--version"], capture_output=True, text=True, timeout=10, check=False
        )
        text = (out.stdout or out.stderr or "").strip()
        return text or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _omes_version() -> str:
    root = os.environ.get("OMES_ROOT")
    if root:
        version_file = Path(root) / "VERSION"
        try:
            return version_file.read_text(encoding="utf-8").strip() or "unknown"
        except OSError:
            pass
    return "unknown"


def _new_session_dir(dest_root: Path) -> Path:
    dest_root.mkdir(parents=True, exist_ok=True)
    os.chmod(dest_root, 0o700)
    ts = _now_ts()
    session_dir = dest_root / ts
    suffix = 1
    while session_dir.exists():
        session_dir = dest_root / f"{ts}-{suffix}"
        suffix += 1
    session_dir.mkdir(parents=True)
    os.chmod(session_dir, 0o700)
    return session_dir


def create(
    hermes_home: Path,
    class_names,
    include_secrets: bool,
    dry_run: bool,
    dest_root: Path | None = None,
):
    """Creates one backup session. Returns a result dict.

    include_secrets must be True AND "secrets" must be explicitly in
    class_names for secret files to be included - this function never
    adds "secrets" implicitly.
    """
    dest_root = dest_root or paths.backups_root()
    resolved_classes = classes_mod.resolve_classes(class_names)

    if "secrets" in resolved_classes and not include_secrets:
        raise BackupError(
            "class 'secrets' requires --include-secrets (secrets are never backed up by default)"
        )

    if dry_run:
        preview = archive.preview_classes(hermes_home, resolved_classes)
        return {
            "dry_run": True,
            "classes": list(resolved_classes),
            "hermes_home": str(hermes_home),
            "entries": preview,
            "entry_count": len(preview),
        }

    session_dir = _new_session_dir(dest_root)
    entries = archive.build_archive(hermes_home, resolved_classes, session_dir)
    manifest.write_manifest(session_dir, entries)

    meta = {
        "hermes_version": _hermes_version(),
        "hermes_home": str(hermes_home),
        "classes": list(resolved_classes),
        "include_secrets": bool(include_secrets and "secrets" in resolved_classes),
        "timestamp": session_dir.name,
        "omes_version": _omes_version(),
    }
    manifest.write_meta(session_dir, meta)

    return {
        "dry_run": False,
        "timestamp": session_dir.name,
        "path": str(session_dir),
        "classes": list(resolved_classes),
        "entry_count": len(entries),
        "meta": meta,
    }


def list_sessions(dest_root: Path | None = None):
    dest_root = dest_root or paths.backups_root()
    if not dest_root.is_dir():
        return []
    sessions = []
    for entry in sorted(dest_root.iterdir()):
        if not entry.is_dir():
            continue
        try:
            meta = manifest.read_meta(entry)
        except manifest.ManifestError as exc:
            sessions.append({"timestamp": entry.name, "error": str(exc)})
            continue
        sessions.append(
            {
                "timestamp": entry.name,
                "classes": meta.get("classes", []),
                "hermes_home": meta.get("hermes_home"),
                "hermes_version": meta.get("hermes_version"),
                "include_secrets": meta.get("include_secrets", False),
            }
        )
    return sessions


def verify(timestamp: str, dest_root: Path | None = None):
    dest_root = dest_root or paths.backups_root()
    session_dir = dest_root / timestamp
    if not session_dir.is_dir():
        raise BackupError(f"backup session not found: {timestamp}")

    meta = manifest.read_meta(session_dir)
    entries = manifest.read_manifest(session_dir)

    archive_path = session_dir / manifest.ARCHIVE_FILENAME
    if not archive_path.is_file():
        raise BackupError(f"archive not found for session {timestamp}: {archive_path}")

    try:
        ok, problems = archive.verify_archive(session_dir, entries)
    except tarfile.TarError as exc:
        raise BackupError(f"corrupt archive for session {timestamp}: {exc}") from exc

    return {
        "timestamp": timestamp,
        "ok": ok,
        "problems": problems,
        "entry_count": len(entries),
        "meta": meta,
    }


def restore(
    timestamp: str,
    target_hermes_home: Path,
    class_names,
    dry_run: bool,
    restore_secrets: bool,
    force_home: bool,
    dest_root: Path | None = None,
    backups_dest_root: Path | None = None,
):
    dest_root = dest_root or paths.backups_root()
    session_dir = dest_root / timestamp
    if not session_dir.is_dir():
        raise BackupError(f"backup session not found: {timestamp}")

    meta = manifest.read_meta(session_dir)
    entries = manifest.read_manifest(session_dir)

    backed_up_home = meta.get("hermes_home")
    if backed_up_home and str(target_hermes_home) != backed_up_home and not force_home:
        raise BackupError(
            f"refusing to restore backup of '{backed_up_home}' into a different "
            f"HERMES_HOME '{target_hermes_home}' without --force-home"
        )

    requested = classes_mod.resolve_classes(class_names) if class_names else tuple(
        c for c in meta.get("classes", []) if c != "secrets" or restore_secrets
    )

    selected = [e for e in entries if e["category"] in requested]
    if not selected:
        return {"timestamp": timestamp, "restored": [], "skipped_secrets": True, "dry_run": dry_run}

    skipped_secrets = False
    if not restore_secrets:
        before = len(selected)
        selected = [e for e in selected if e["category"] != "secrets"]
        skipped_secrets = before != len(selected)

    ok, problems = archive.verify_archive(session_dir, selected)
    if not ok:
        raise BackupError(
            f"refusing to restore: checksum mismatch for {len(problems)} file(s): {problems[:5]}"
        )

    if dry_run:
        return {
            "timestamp": timestamp,
            "would_restore": [e["path"] for e in selected],
            "skipped_secrets": skipped_secrets,
            "dry_run": True,
        }

    # Pre-restore backup: protect whatever currently lives at the target
    # paths before overwriting anything (issue #82 acceptance criterion).
    pre_restore = create(
        target_hermes_home,
        list({e["category"] for e in selected}),
        include_secrets=restore_secrets,
        dry_run=False,
        dest_root=backups_dest_root or dest_root,
    )

    restored = archive.extract_entries(session_dir, selected, target_hermes_home)

    return {
        "timestamp": timestamp,
        "restored": restored,
        "skipped_secrets": skipped_secrets,
        "pre_restore_backup": pre_restore.get("timestamp"),
        "dry_run": False,
    }


def prune(keep: int | None = None, dest_root: Path | None = None):
    dest_root = dest_root or paths.backups_root()
    if keep is None:
        keep = int(os.environ.get("OMES_BACKUP_KEEP", "10"))
    if not dest_root.is_dir():
        return []
    names = sorted(p.name for p in dest_root.iterdir() if p.is_dir())
    if len(names) <= keep:
        return []
    to_remove = names[: len(names) - keep]
    for name in to_remove:
        shutil.rmtree(dest_root / name, ignore_errors=True)
    return to_remove
