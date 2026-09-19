"""lib/omes/py/agent/provenance.py - version/provenance metadata recorded
in each agent's state file on apply (issue #87). No credentials, ever -
only identity/version facts."""
from __future__ import annotations

import shutil
import subprocess  # nosec B404 - fixed, read-only, argv-safe commands only
from pathlib import Path
from typing import Optional


def _run(cmd: list, timeout: float = 5.0) -> Optional[str]:
    if shutil.which(cmd[0]) is None:
        return None
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, no shell, bounded timeout
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def omes_version(omes_root: Path) -> str:
    version_file = Path(omes_root) / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def git_ref(omes_root: Path) -> Optional[str]:
    return _run(["git", "-C", str(omes_root), "rev-parse", "HEAD"])


def hermes_version() -> Optional[str]:
    out = _run(["hermes", "--version"])
    return out


def collect(omes_root: Path) -> dict:
    return {
        "omesVersion": omes_version(omes_root),
        "gitRef": git_ref(omes_root),
        "hermesVersion": hermes_version(),
    }
