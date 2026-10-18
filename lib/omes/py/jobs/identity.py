"""lib/omes/py/jobs/identity.py - executor identity for `running` host jobs
(issue #271, ADR-0032 rule 3).

When `runner.run()` moves a job to `running` it records who is executing it:
`{"pid", "boot_id", "started_at"}` (plus `pid_start_ticks`, see below). After
a crash or reboot, `reconcile.py` uses this identity to prove - never to
guess - that a `running` record no longer has a live executor. This replaces
a per-job lease or heartbeat: a single host runs a job as a synchronous
subprocess of one `omes job run` process, so (boot id, pid, process start
time) identifies that executor exactly, with no timer that could expire a job
that is merely slow.

Every probe here is a small function so tests can monkeypatch it; none of
them mutates the host. Stdlib only (ADR-0012).
"""
from __future__ import annotations

import os
from typing import Any

from . import store

BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"

ALIVE = "alive"
DEAD = "dead"
UNKNOWN = "unknown"


def current_boot_id() -> str | None:
    """The kernel boot id of the running host, or None if unreadable
    (non-Linux, restricted /proc). A new boot always yields a new id."""
    try:
        with open(BOOT_ID_PATH, "r", encoding="utf-8") as fh:
            value = fh.read().strip()
    except OSError:
        return None
    return value or None


def process_start_ticks(pid: int) -> int | None:
    """Start time of `pid` in clock ticks since boot (field 22 of
    /proc/<pid>/stat), or None if unreadable. Together with the boot id this
    distinguishes the original runner from an unrelated process that was
    later handed the same pid."""
    try:
        with open(f"/proc/{int(pid)}/stat", "r", encoding="utf-8") as fh:
            stat = fh.read()
    except (OSError, ValueError):
        return None
    # `comm` (field 2) may contain spaces and parentheses; everything after
    # the LAST ")" is space-separated, starting at field 3 (state).
    rparen = stat.rfind(")")
    if rparen == -1:
        return None
    fields = stat[rparen + 1 :].split()
    try:
        return int(fields[19])  # field 22 overall
    except (IndexError, ValueError):
        return None


def pid_liveness(pid: int) -> str:
    """`dead` only when the kernel says no such process exists. A process
    that exists but belongs to another user (PermissionError) is `alive`;
    any other error is `unknown`. Never signals the process (signal 0 only
    probes), and refuses pid <= 0 because os.kill(0, 0) would address a whole
    process group."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return UNKNOWN
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return DEAD
    except PermissionError:
        return ALIVE
    except OSError:
        return UNKNOWN
    return ALIVE


def runner_identity() -> dict[str, Any]:
    """Identity of the current process as the executor of a job."""
    pid = os.getpid()
    return {
        "pid": pid,
        "boot_id": current_boot_id(),
        "started_at": store.now_iso(),
        "pid_start_ticks": process_start_ticks(pid),
    }
