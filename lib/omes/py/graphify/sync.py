"""OMES-side change detection for Graphify sources (issue #54).

Stdlib only (ADR-0012). Verified 2026-09-19 against graphify 0.9.64:
upstream does NOT expose a literal `--update`/`--watch` CLI *flag* (the
earlier framing in docs/graphify.md §1.8 was correct about that narrow
point), but it DOES ship real *subcommands* with those names -
`graphify update <path>` (re-extracts code files only, no LLM, and
leaves graph.json/graph.html/GRAPH_REPORT.md untouched when it detects
no topology change - confirmed empirically: a second `update` with no
source changes printed "No code-graph topology changes detected;
outputs left untouched" and returned in ~0.18s) and `graphify watch
<path>` (a continuous folder watcher, never wrapped or supervised by
OMES - see docs/graphify.md §6.5). This module exists because upstream's
own no-op detection still re-scans and re-parses every code file on
every `update` call; OMES's own manifest lets `omes graphify sync` skip
calling `graphify` at all when nothing has changed, and gives
`omes graphify status` something to compare against without invoking
graphify at all.

This module never re-implements AST extraction or the graph format -
it only decides "should extraction run again," by hashing/mtime-
snapshotting the source tree.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from typing import Dict, List, Tuple

MANIFEST_SCHEMA_VERSION = 1


def _read_gitignore_patterns(root: str) -> List[str]:
    """Best-effort, non-recursive .gitignore support: reads only
    <root>/.gitignore's non-comment, non-blank lines as flat glob
    patterns matched against each path component and the relative path
    itself. This is intentionally simple (no `**`, no negation, no
    nested .gitignore) - full .gitignore/.graphifyignore semantics are
    #55's concern (docs/graphify-privacy.md); this is only enough to
    keep an obviously-ignored tree (e.g. a committed virtualenv) out of
    the change-detection walk.
    """
    path = os.path.join(root, ".gitignore")
    patterns: List[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                patterns.append(line.rstrip("/"))
    except OSError:
        pass
    return patterns


def _is_ignored(rel_path: str, patterns: List[str]) -> bool:
    parts = rel_path.split(os.sep)
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        for part in parts:
            if fnmatch.fnmatch(part, pattern):
                return True
    return False


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_tree(root: str, exclude_abs_dirs: List[str]) -> Dict[str, Dict[str, object]]:
    """Walk `root`, returning {relpath: {"sha256": ..., "mtime": ...,
    "size": ...}} for every regular file, skipping `.git/`, anything
    matching a top-level `.gitignore` pattern (best-effort - see
    `_read_gitignore_patterns`), symlinks (never followed - a symlinked
    file or directory is skipped entirely, matching #56's "symlinks are
    refused/skipped" requirement), and any directory whose resolved
    absolute path is one of `exclude_abs_dirs` (graphify-out/, and the
    export vault subdirectory when it happens to live inside the
    source tree - loop avoidance, docs/graphify.md §6.3).
    """
    root = os.path.abspath(root)
    exclude_abs = {os.path.abspath(d) for d in exclude_abs_dirs}
    patterns = _read_gitignore_patterns(root)

    result: Dict[str, Dict[str, object]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirpath_abs = os.path.abspath(dirpath)
        dirnames[:] = [
            d
            for d in dirnames
            if d != ".git"
            and os.path.join(dirpath_abs, d) not in exclude_abs
            and not os.path.islink(os.path.join(dirpath_abs, d))
            and not _is_ignored(os.path.relpath(os.path.join(dirpath_abs, d), root), patterns)
        ]

        for name in filenames:
            abs_path = os.path.join(dirpath_abs, name)
            if os.path.islink(abs_path):
                continue
            rel_path = os.path.relpath(abs_path, root)
            if _is_ignored(rel_path, patterns):
                continue
            try:
                stat = os.stat(abs_path)
            except OSError:
                continue
            try:
                digest = sha256_file(abs_path)
            except OSError:
                continue
            result[rel_path] = {
                "sha256": digest,
                "mtime": stat.st_mtime,
                "size": stat.st_size,
            }
    return result


def diff_snapshot(
    old_files: Dict[str, Dict[str, object]],
    new_files: Dict[str, Dict[str, object]],
) -> Tuple[List[str], List[str], List[str]]:
    """Return (added, removed, modified) relative paths, sorted. A file
    counts as modified only when its sha256 differs (mtime/size changes
    with an identical hash - e.g. a touch or a re-checkout with the
    same content - are not treated as a change).
    """
    old_keys = set(old_files)
    new_keys = set(new_files)

    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    modified = sorted(
        k for k in (old_keys & new_keys) if old_files[k].get("sha256") != new_files[k].get("sha256")
    )
    return added, removed, modified


def has_changes(added: List[str], removed: List[str], modified: List[str]) -> bool:
    return bool(added or removed or modified)
