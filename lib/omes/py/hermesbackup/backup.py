"""hermesbackup.backup - create/list/verify/restore orchestration.

Stdlib only (ADR-0012). Recovery classes under ADR-0020 (issue #176):
  1. portable-profile -> hermes profile export/import (credentials excluded upstream)
  2. full-runtime-dr -> hermes backup/import (sensitive; credentials included upstream)
  3. omes-host -> managed-path backup/restore for files/services OMES owns

Legacy archive format (tar.gz + MANIFEST) remains fully readable and restorable.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import archive, classes as classes_mod, manifest, paths


class BackupError(Exception):
    pass


def _now_ts() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
    return h.hexdigest()


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


def create_native(
    hermes_home: Path,
    recovery_class: str = "portable-profile",
    profile: str = "default",
    allow_sensitive_credentials: bool = False,
    dry_run: bool = False,
    dest_root: Path | None = None,
) -> Dict[str, Any]:
    """Creates a native Hermes backup session using supported upstream commands:
    - portable-profile -> `hermes profile export` (credentials excluded upstream)
    - full-runtime-dr -> `hermes backup` (sensitive, requires allow_sensitive_credentials)
    """
    dest_root = dest_root or paths.backups_root()
    classes_mod.validate_recovery_class(recovery_class)

    if recovery_class == "full-runtime-dr" and not allow_sensitive_credentials:
        raise BackupError(
            "full-runtime-dr backup includes credentials by upstream definition and requires "
            "--allow-sensitive-credentials (or --include-secrets)"
        )

    fmt = "native-hermes-profile" if recovery_class == "portable-profile" else "native-hermes-runtime"
    sensitive = (recovery_class == "full-runtime-dr")

    if dry_run:
        return {
            "dry_run": True,
            "format": fmt,
            "recovery_class": recovery_class,
            "profile": profile if recovery_class == "portable-profile" else None,
            "sensitive": sensitive,
            "hermes_home": str(hermes_home),
        }

    session_dir = _new_session_dir(dest_root)

    env = dict(os.environ)
    env["HERMES_HOME"] = str(hermes_home)

    if recovery_class == "portable-profile":
        artifact_name = f"{profile}.hermes-profile.tar.gz"
        artifact_path = session_dir / artifact_name
        cmd = ["hermes", "profile", "export", profile, "--output", str(artifact_path)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            shutil.rmtree(session_dir, ignore_errors=True)
            raise BackupError(f"failed to run hermes profile export: {exc}") from exc

        if proc.returncode != 0:
            shutil.rmtree(session_dir, ignore_errors=True)
            raise BackupError(f"hermes profile export failed: {proc.stderr or proc.stdout}")
    else:
        # full-runtime-dr
        artifact_name = "hermes-backup.tar.gz"
        artifact_path = session_dir / artifact_name
        cmd = ["hermes", "backup", "--output", str(artifact_path)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            shutil.rmtree(session_dir, ignore_errors=True)
            raise BackupError(f"failed to run hermes backup: {exc}") from exc

        if proc.returncode != 0:
            shutil.rmtree(session_dir, ignore_errors=True)
            raise BackupError(f"hermes backup failed: {proc.stderr or proc.stdout}")

        try:
            os.chmod(artifact_path, 0o600)
        except OSError:
            pass

    if not artifact_path.is_file():
        shutil.rmtree(session_dir, ignore_errors=True)
        raise BackupError(f"expected backup artifact not created: {artifact_path}")

    checksum = sha256_file(artifact_path)
    size = artifact_path.stat().st_size
    mode = stat.S_IMODE(artifact_path.stat().st_mode)

    meta = {
        "format": fmt,
        "recovery_class": recovery_class,
        "artifact": artifact_name,
        "sha256": checksum,
        "artifact_sha256": checksum,
        "artifact_size": size,
        "profile": profile if recovery_class == "portable-profile" else None,
        "sensitive": sensitive,
        "hermes_home": str(hermes_home),
        "hermes_version": _hermes_version(),
        "omes_version": _omes_version(),
        "timestamp": session_dir.name,
    }
    manifest.write_meta(session_dir, meta)

    manifest.write_manifest(
        session_dir,
        [
            {
                "category": "artifact",
                "path": artifact_name,
                "sha256": checksum,
                "size": size,
                "mode": mode,
            }
        ],
    )

    return {
        "dry_run": False,
        "timestamp": session_dir.name,
        "path": str(session_dir),
        "format": fmt,
        "recovery_class": recovery_class,
        "profile": profile if recovery_class == "portable-profile" else None,
        "artifact": artifact_name,
        "sha256": checksum,
        "artifact_sha256": checksum,
        "sensitive": sensitive,
        "meta": meta,
    }


def create(
    hermes_home: Path,
    class_names: list[str] | tuple[str, ...] | None = None,
    include_secrets: bool = False,
    dry_run: bool = False,
    dest_root: Path | None = None,
    recovery_class: str | None = None,
    profile: str = "default",
    allow_sensitive_credentials: bool = False,
    classes: list[str] | tuple[str, ...] | None = None,
):
    """Creates one backup session.

    If recovery_class is specified, or if class_names is empty/None without explicit
    legacy classes, delegates to create_native (ADR-0020).
    If legacy class_names are provided (e.g. ['config', 'skills']), or if recovery_class
    is 'omes-host', preserves legacy archive creation for backward compatibility.
    """
    dest_root = dest_root or paths.backups_root()
    if classes is not None and class_names is None:
        class_names = classes

    if recovery_class:
        classes_mod.validate_recovery_class(recovery_class)
        if recovery_class in ("portable-profile", "full-runtime-dr"):
            allow_sens = allow_sensitive_credentials or include_secrets
            return create_native(
                hermes_home,
                recovery_class=recovery_class,
                profile=profile,
                allow_sensitive_credentials=allow_sens,
                dry_run=dry_run,
                dest_root=dest_root,
            )
        # omes-host
        return create_legacy(
            hermes_home,
            class_names=class_names or ("config", "skills"),
            include_secrets=include_secrets,
            dry_run=dry_run,
            dest_root=dest_root,
        )

    if class_names:
        if len(class_names) == 1 and class_names[0] in classes_mod.RECOVERY_CLASSES:
            return create(
                hermes_home,
                recovery_class=class_names[0],
                include_secrets=include_secrets,
                dry_run=dry_run,
                dest_root=dest_root,
                profile=profile,
                allow_sensitive_credentials=allow_sensitive_credentials,
            )
        return create_legacy(
            hermes_home,
            class_names=class_names,
            include_secrets=include_secrets,
            dry_run=dry_run,
            dest_root=dest_root,
        )

    # Default to portable-profile native backup under ADR-0020
    allow_sens = allow_sensitive_credentials or include_secrets
    return create_native(
        hermes_home,
        recovery_class="portable-profile",
        profile=profile,
        allow_sensitive_credentials=allow_sens,
        dry_run=dry_run,
        dest_root=dest_root,
    )


def create_legacy(
    hermes_home: Path,
    class_names: list[str] | tuple[str, ...] | None = None,
    include_secrets: bool = False,
    dry_run: bool = False,
    dest_root: Path | None = None,
):
    """Creates a legacy OMES archive session with tar.gz + MANIFEST."""
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
            "format": "legacy-omes",
            "classes": list(resolved_classes),
            "hermes_home": str(hermes_home),
            "entries": preview,
            "entry_count": len(preview),
        }

    session_dir = _new_session_dir(dest_root)
    entries = archive.build_archive(hermes_home, resolved_classes, session_dir)
    manifest.write_manifest(session_dir, entries)

    meta = {
        "format": "legacy-omes",
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
        "format": "legacy-omes",
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

        fmt = meta.get("format", "legacy-omes")
        sessions.append(
            {
                "timestamp": entry.name,
                "format": fmt,
                "recovery_class": meta.get("recovery_class", "legacy-classes" if "classes" in meta else "unknown"),
                "classes": meta.get("classes", []),
                "profile": meta.get("profile"),
                "artifact": meta.get("artifact"),
                "artifact_sha256": meta.get("artifact_sha256"),
                "artifact_size": meta.get("artifact_size"),
                "hermes_home": meta.get("hermes_home"),
                "hermes_version": meta.get("hermes_version"),
                "include_secrets": bool(meta.get("include_secrets", False) or meta.get("sensitive", False)),
                "sensitive": bool(meta.get("sensitive", False) or meta.get("include_secrets", False)),
            }
        )
    return sessions


def verify(timestamp: str, dest_root: Path | None = None):
    dest_root = dest_root or paths.backups_root()
    session_dir = dest_root / timestamp
    if not session_dir.is_dir():
        raise BackupError(f"backup session not found: {timestamp}")

    meta = manifest.read_meta(session_dir)
    fmt = meta.get("format", "legacy-omes")

    if fmt.startswith("native-hermes-"):
        artifact_name = meta.get("artifact")
        if not artifact_name:
            raise BackupError(f"native backup metadata missing artifact reference: {timestamp}")
        artifact_path = session_dir / artifact_name
        if not artifact_path.is_file():
            raise BackupError(f"artifact not found for session {timestamp}: {artifact_path}")
        if artifact_name.endswith((".tar.gz", ".tgz", ".tar")):
            try:
                with tarfile.open(artifact_path, "r") as tar:
                    tar.getmembers()
            except (tarfile.TarError, OSError) as exc:
                raise BackupError(f"corrupt archive for session {timestamp}: {exc}") from exc

        actual_sha256 = sha256_file(artifact_path)
        expected_sha256 = meta.get("artifact_sha256", "") or meta.get("sha256", "")
        ok = (actual_sha256 == expected_sha256)
        problems = [] if ok else [f"sha256 mismatch (checksum mismatch) for {artifact_name}: expected {expected_sha256}, got {actual_sha256}"]

        return {
            "timestamp": timestamp,
            "ok": ok,
            "format": fmt,
            "recovery_class": meta.get("recovery_class"),
            "problems": problems,
            "artifact": artifact_name,
            "sha256": actual_sha256,
            "meta": meta,
        }

    # Legacy OMES archive verification
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
        "format": "legacy-omes",
        "problems": problems,
        "entry_count": len(entries),
        "meta": meta,
    }


def restore(
    timestamp: str,
    target_hermes_home: Path,
    class_names=None,
    dry_run: bool = False,
    restore_secrets: bool = False,
    force_home: bool = False,
    dest_root: Path | None = None,
    backups_dest_root: Path | None = None,
    allow_sensitive_credentials: bool = False,
    profile: str | None = None,
):
    dest_root = dest_root or paths.backups_root()
    session_dir = dest_root / timestamp
    if not session_dir.is_dir():
        raise BackupError(f"backup session not found: {timestamp}")

    meta = manifest.read_meta(session_dir)
    fmt = meta.get("format", "legacy-omes")

    if fmt.startswith("native-hermes-"):
        # Pre-verification: check artifact integrity
        v = verify(timestamp, dest_root)
        if not v["ok"]:
            raise BackupError(f"refusing to restore corrupt backup {timestamp}: {v['problems']}")

        is_sensitive = meta.get("sensitive", False) or (meta.get("recovery_class") == "full-runtime-dr")
        if is_sensitive and not (restore_secrets or allow_sensitive_credentials):
            raise BackupError(
                "refusing to restore sensitive credentials from full-runtime-dr backup without "
                "--allow-sensitive-credentials (or --restore-secrets)"
            )

        backed_up_home = meta.get("hermes_home")
        if backed_up_home and str(target_hermes_home) != backed_up_home and not force_home:
            raise BackupError(
                f"refusing to restore backup of '{backed_up_home}' into a different "
                f"HERMES_HOME '{target_hermes_home}' without --force-home"
            )

        if dry_run:
            return {
                "timestamp": timestamp,
                "format": fmt,
                "recovery_class": meta.get("recovery_class"),
                "artifact": meta.get("artifact"),
                "target_home": str(target_hermes_home),
                "sensitive": is_sensitive,
                "dry_run": True,
            }

        # Create pre-restore recovery point before host mutation
        target_hermes_home.mkdir(parents=True, exist_ok=True)
        pre_restore = create(
            target_hermes_home,
            recovery_class="portable-profile" if fmt == "native-hermes-profile" else "full-runtime-dr",
            profile=profile or meta.get("profile", "default"),
            allow_sensitive_credentials=True,
            dest_root=backups_dest_root or dest_root,
        )

        artifact_path = session_dir / meta["artifact"]
        env = dict(os.environ)
        env["HERMES_HOME"] = str(target_hermes_home)

        if fmt == "native-hermes-profile":
            prof = profile or meta.get("profile", "default")
            cmd = ["hermes", "profile", "import", str(artifact_path)]
            if prof:
                cmd.extend(["--profile", prof])
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env, check=False)
            except (OSError, subprocess.SubprocessError) as exc:
                raise BackupError(f"hermes profile import execution failed: {exc}") from exc
            if proc.returncode != 0:
                raise BackupError(f"hermes profile import failed: {proc.stderr or proc.stdout}")
        else:
            # native-hermes-runtime
            cmd = ["hermes", "import", str(artifact_path)]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env, check=False)
            except (OSError, subprocess.SubprocessError) as exc:
                raise BackupError(f"hermes import execution failed: {exc}") from exc
            if proc.returncode != 0:
                raise BackupError(f"hermes import failed: {proc.stderr or proc.stdout}")

        # Post-restore health verification
        health_ok = True
        health_output = ""
        try:
            health_proc = subprocess.run(["hermes", "doctor"], capture_output=True, text=True, timeout=15, env=env, check=False)
            health_ok = (health_proc.returncode == 0)
            health_output = (health_proc.stdout or health_proc.stderr or "").strip()
        except (OSError, subprocess.SubprocessError):
            health_ok = False

        if not health_ok:
            raise BackupError(f"post-restore Hermes health check failed: {health_output or 'hermes doctor failed'}")

        return {
            "timestamp": timestamp,
            "format": fmt,
            "recovery_class": meta.get("recovery_class"),
            "restored_artifact": meta.get("artifact"),
            "pre_restore_backup": pre_restore.get("timestamp"),
            "health_verified": True,
            "dry_run": False,
        }

    # Legacy OMES restore flow
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
            "format": "legacy-omes",
            "would_restore": [e["path"] for e in selected],
            "skipped_secrets": skipped_secrets,
            "dry_run": True,
        }

    # Pre-restore backup: protect whatever currently lives at the target paths
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
        "format": "legacy-omes",
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
