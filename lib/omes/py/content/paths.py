"""Path/layout resolution for the content distribution workflow.

See docs/content-distribution.md section 3 for the authoritative layout.
Stdlib only (ADR-0012).
"""
from __future__ import annotations

import os
from pathlib import Path

# Subdirectories that make up the content root layout. Order matters only
# for ensure_layout()'s creation order (parents before children is not
# required here, all are direct children of the root).
LAYOUT_DIRS = (
    "inbox",
    "processing",
    "uploaded",
    "failed",
    "review",
    "reports",
    "sessions",
    "state",
)

# Directories/prefixes a scan must never walk into when looking for new
# inbox files (docs/content-distribution.md section 3/8).
IGNORED_TOP_LEVEL = {"reports", "processing", "state", "sessions", "uploaded", "failed", "review"}


def content_root() -> Path:
    """Resolve OMES_CONTENT_ROOT, defaulting to
    ``$XDG_DATA_HOME/omes/content`` (``~/.local/share/omes/content``)."""
    override = os.environ.get("OMES_CONTENT_ROOT")
    if override:
        return Path(override).expanduser()
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"
    return base / "omes" / "content"


def inbox_dir(root: Path | None = None) -> Path:
    return (root or content_root()) / "inbox"


def processing_dir(root: Path | None = None) -> Path:
    return (root or content_root()) / "processing"


def uploaded_dir(root: Path | None = None) -> Path:
    return (root or content_root()) / "uploaded"


def failed_dir(root: Path | None = None) -> Path:
    return (root or content_root()) / "failed"


def review_dir(root: Path | None = None) -> Path:
    return (root or content_root()) / "review"


def reports_dir(root: Path | None = None) -> Path:
    return (root or content_root()) / "reports"


def sessions_dir(root: Path | None = None) -> Path:
    """The ONLY directory holding browser profiles/cookies (secrets).

    Callers in reports.py / audit / export / backup code must never walk
    into this directory. Kept as its own function (rather than a generic
    path-join helper) precisely so that omission from those modules is a
    reviewable, greppable fact.
    """
    return (root or content_root()) / "sessions"


def state_dir(root: Path | None = None) -> Path:
    return (root or content_root()) / "state"


def jobs_dir(root: Path | None = None) -> Path:
    return state_dir(root) / "jobs"


def audit_log_path(root: Path | None = None) -> Path:
    return state_dir(root) / "audit.jsonl"


def scan_lock_path(root: Path | None = None) -> Path:
    return state_dir(root) / "scan.lock"


def job_processing_dir(job_id: str, root: Path | None = None) -> Path:
    return processing_dir(root) / job_id


def job_record_path(job_id: str, root: Path | None = None) -> Path:
    return jobs_dir(root) / f"{job_id}.json"


def session_platform_dir(platform: str, root: Path | None = None) -> Path:
    """A single platform worker's isolated browser profile directory
    (issue #66). Created at mode 0700 by the worker's own `prepare`/
    `bootstrap-session` operation, not by the manager - see
    workers/base.py::ensure_session_dir()."""
    return sessions_dir(root) / platform


def job_evidence_dir(job_id: str, root: Path | None = None) -> Path:
    """Non-secret evidence a worker is allowed to write for one job
    (screenshots/manifests referenced by path, never inline bytes, never
    session/cookie data - issue #66)."""
    return reports_dir(root) / job_id / "evidence"


def job_variants_dir(job_id: str, platform: str, root: Path | None = None) -> Path:
    """Per-platform generated/edited caption variants (issue #69),
    deliberately kept separate from `job_processing_dir()`'s immutable
    `source.<ext>` - editing a platform's caption must never touch, move,
    or re-hash the original source file."""
    return job_processing_dir(job_id, root) / "variants" / platform


def ensure_layout(root: Path | None = None) -> Path:
    """Create the full directory layout under `root` (default
    content_root()). sessions/ and state/ are created 0700; everything else
    default (0755, subject to umask)."""
    resolved = root or content_root()
    resolved.mkdir(parents=True, exist_ok=True)
    for name in LAYOUT_DIRS:
        d = resolved / name
        d.mkdir(parents=True, exist_ok=True)
        if name in ("sessions", "state"):
            os.chmod(d, 0o700)
    jd = jobs_dir(resolved)
    jd.mkdir(parents=True, exist_ok=True)
    os.chmod(jd, 0o700)
    return resolved
