"""Maps a platform name to its worker executable (issue #66).

Adding a new platform means adding one new `workers/<platform>/worker.py`
implementing the contract in docs/content-distribution.md section 6 -
this registry is the only place that needs to know it exists, and it
finds platforms by directory convention rather than a hardcoded list.
"""
from __future__ import annotations

from pathlib import Path

_WORKERS_ROOT = Path(__file__).resolve().parent


class UnknownPlatformError(Exception):
    def __init__(self, platform: str):
        super().__init__(f"unknown content platform {platform!r} (no workers/{platform}/worker.py)")
        self.platform = platform


def list_platforms() -> list[str]:
    if not _WORKERS_ROOT.is_dir():
        return []
    return sorted(
        p.name
        for p in _WORKERS_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith("_") and (p / "worker.py").is_file()
    )


def worker_executable_for_platform(platform: str) -> Path:
    candidate = _WORKERS_ROOT / platform / "worker.py"
    if not candidate.is_file():
        raise UnknownPlatformError(platform)
    return candidate
