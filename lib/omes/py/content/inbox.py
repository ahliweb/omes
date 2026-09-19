"""Inbox watcher and artifact lifecycle (issue #64).

Detects new files in content/inbox by content hash (sha256) + size + mtime
stability, creates job records, and moves settled files into
content/processing/<job-id>/source.<ext>. See docs/content-distribution.md
section 3-4. Stdlib only (ADR-0012).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import mimetypes
import os
import shutil
import time
from pathlib import Path
from typing import Any

from . import jobs, paths

DEFAULT_SETTLE_SECONDS = 5.0
_PENDING_FILE_NAME = "scan-pending.json"
_HASH_CHUNK = 1024 * 1024


class LockContentionError(Exception):
    pass


@contextlib.contextmanager
def scan_lock(root: Path | None = None):
    """Prevents concurrent scans. A stale lock (owning pid no longer alive)
    is reclaimed automatically; a live lock raises LockContentionError."""
    lock_path = paths.scan_lock_path(root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(lock_path.parent, 0o700)

    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(str(os.getpid()))
            break
        except FileExistsError:
            try:
                existing = lock_path.read_text(encoding="utf-8").strip()
                owner_pid = int(existing) if existing else -1
            except (OSError, ValueError):
                owner_pid = -1
            if owner_pid > 0 and _pid_alive(owner_pid):
                raise LockContentionError(
                    f"content scan already running (pid {owner_pid}); lock at {lock_path}"
                )
            # Stale lock: reclaim it.
            with contextlib.suppress(FileNotFoundError):
                lock_path.unlink()
            continue
    try:
        yield lock_path
    finally:
        with contextlib.suppress(FileNotFoundError):
            lock_path.unlink()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _iter_candidate_files(inbox: Path):
    """Yields regular, non-symlink, non-dotfile files directly reachable
    under `inbox`, recursively, never following symlinked directories."""
    if not inbox.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(inbox, followlinks=False):
        # Prune dotfile/hidden directories in-place.
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            full = Path(dirpath) / name
            if full.is_symlink():
                continue
            if not full.is_file():
                continue
            yield full


def _load_pending(root: Path | None) -> dict[str, Any]:
    p = paths.state_dir(root) / _PENDING_FILE_NAME
    if not p.is_file():
        return {}
    try:
        with p.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_pending(pending: dict[str, Any], root: Path | None) -> None:
    p = paths.state_dir(root) / _PENDING_FILE_NAME
    jobs.atomic_write_json(p, pending)
    os.chmod(p, 0o600)


def _existing_hashes(root: Path | None) -> dict[str, str]:
    """Maps sha256 -> job_id for all known (non-duplicate) job records."""
    out: dict[str, str] = {}
    for record in jobs.list_jobs(root):
        src = record.get("source", {})
        if src.get("duplicate_of"):
            continue
        sha = src.get("sha256")
        if sha and sha not in out:
            out[sha] = record["job_id"]
    return out


def _atomic_move_into_processing(src: Path, job_id: str, root: Path | None) -> str:
    """Copies `src` into content/processing/<job_id>/source.<ext> via a
    temp file + os.replace, then removes the original, so a crash between
    the temp write and the rename never leaves a half-moved file visible
    under its final name."""
    ext = src.suffix
    job_dir = paths.job_processing_dir(job_id, root)
    job_dir.mkdir(parents=True, exist_ok=True)
    final = job_dir / f"source{ext}"
    tmp = job_dir / f".tmp-source{ext}"
    shutil.copy2(src, tmp)
    with open(tmp, "rb") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, final)
    src.unlink()
    return str(final.relative_to(root) if root else final)


def scan(
    root: Path | None = None,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
) -> dict[str, Any]:
    """Runs one scan pass over content/inbox. Returns a summary dict:
    {"created": [job_id...], "duplicates": [job_id...], "pending": [path...]}
    """
    root = paths.ensure_layout(root)
    inbox = paths.inbox_dir(root)
    pending = _load_pending(root)
    now = time.time()
    known_hashes = _existing_hashes(root)

    created: list[str] = []
    duplicates: list[str] = []
    still_pending: list[str] = []
    seen_paths: set[str] = set()

    for path in _iter_candidate_files(inbox):
        key = str(path)
        seen_paths.add(key)
        try:
            st = path.stat()
        except OSError:
            continue
        size, mtime = st.st_size, st.st_mtime
        prior = pending.get(key)
        settled = (now - mtime) >= settle_seconds
        if not settled and prior and prior.get("size") == size and prior.get("mtime") == mtime:
            settled = True
        if not settled:
            pending[key] = {"size": size, "mtime": mtime, "first_seen": prior.get("first_seen", now) if prior else now}
            still_pending.append(key)
            continue

        # Settled: hash it and create (or skip as duplicate) a job.
        sha = sha256_file(path)
        mime_guess, _ = mimetypes.guess_type(path.name)
        stamp = jobs.now_stamp()
        job_id = jobs.make_job_id(sha, stamp)

        duplicate_of = known_hashes.get(sha)
        if duplicate_of:
            record = jobs.new_job_record(
                job_id=job_id,
                original_path=str(path.relative_to(root)) if _is_relative(path, root) else str(path),
                processing_path="",
                sha256_hex=sha,
                size_bytes=size,
                mime_guess=mime_guess,
                duplicate_of=duplicate_of,
            )
            processing_path = _atomic_move_into_processing(path, job_id, root)
            record["source"]["processing_path"] = processing_path
            jobs.save_job(record, root)
            duplicates.append(job_id)
        else:
            processing_path = _atomic_move_into_processing(path, job_id, root)
            record = jobs.new_job_record(
                job_id=job_id,
                original_path=str(path.relative_to(root)) if _is_relative(path, root) else str(path),
                processing_path=processing_path,
                sha256_hex=sha,
                size_bytes=size,
                mime_guess=mime_guess,
            )
            jobs.save_job(record, root)
            known_hashes[sha] = job_id
            created.append(job_id)
        pending.pop(key, None)

    # Drop pending entries for files that vanished (moved/deleted outside us).
    for key in list(pending.keys()):
        if key not in seen_paths:
            pending.pop(key, None)

    _save_pending(pending, root)
    return {"created": created, "duplicates": duplicates, "pending": still_pending}


def _is_relative(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def rescan(root: Path | None = None) -> dict[str, Any]:
    """Re-hashes files under content/processing and content/failed and
    flags any job whose recorded sha256 no longer matches its file on
    disk (corruption/tamper detection)."""
    root = paths.ensure_layout(root)
    mismatches: list[str] = []
    checked: list[str] = []
    for record in jobs.list_jobs(root):
        proc_path = record.get("source", {}).get("processing_path")
        if not proc_path:
            continue
        full = (root / proc_path) if not os.path.isabs(proc_path) else Path(proc_path)
        if not full.is_file():
            continue
        checked.append(record["job_id"])
        actual = sha256_file(full)
        if actual != record["source"]["sha256"]:
            mismatches.append(record["job_id"])
    return {"checked": checked, "mismatches": mismatches}


def list_jobs_summary(root: Path | None = None, state: str | None = None) -> list[dict[str, Any]]:
    root = paths.ensure_layout(root)
    out = []
    for record in jobs.list_jobs(root):
        if state and record.get("state") != state:
            continue
        out.append(
            {
                "job_id": record["job_id"],
                "state": record["state"],
                "sha256": record["source"]["sha256"],
                "duplicate_of": record["source"].get("duplicate_of"),
                "created_at": record["created_at"],
                "updated_at": record["updated_at"],
            }
        )
    return out
