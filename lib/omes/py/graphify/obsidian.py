"""Render vault-ready Markdown notes from a graphify graph.json (issue #53).

Stdlib only (ADR-0012 / docs/adr/0012). No third-party imports.

graph.json shape (as produced by upstream `graphify extract`, per
docs/graphify.md §1.7): a JSON object with a "nodes" array (each node has
at least "id"; file-level nodes have "type": "file" and "path"; symbol
nodes reference their containing file, commonly via a "file" field) and an
"edges" array (each edge has "source", "target", and "type" of
"EXTRACTED" or "INFERRED" per docs/graphify.md §1.6). This module makes no
assumption beyond those fields being present-or-absent; anything missing
degrades gracefully (an empty vault export is valid - see the "empty
vault" test case in tests/py/graphify/test_obsidian.py).

Every function here is pure with respect to the filesystem except
`write_notes`, which is the only function that actually creates/modifies
files, and `plan_notes`, which never writes anything - it only inspects
the destination directory (read-only) to detect collisions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Dict, List, Optional, Tuple

OMES_GENERATED_MARKER = "omes_generated: true"
INDEX_NOTE_NAME = "_index.md"
PROVENANCE_NOTE_NAME = "_provenance.md"

# Upstream `graphify export obsidian` (verified 2026-09-19 against graphify
# 0.9.64, `graphify export obsidian --graph <graph.json> --dir <dir>`)
# writes its own manifest of every file it produced - reused here instead
# of re-deriving the file list ourselves. See docs/graphify.md §5.2.
UPSTREAM_MANIFEST_NAME = ".graphify_obsidian_manifest.json"

# OMES's own bookkeeping manifest for a target export directory, used only
# on the upstream (`export obsidian`) path to track ownership of
# non-Markdown generated files (the canvas, the vault's own nested
# .obsidian/graph.json, and upstream's own manifest file) - none of which
# can carry a YAML front-matter marker. A Markdown file is still tracked
# via `has_omes_marker` as usual; this file exists only for everything
# else this export writes.
OMES_EXPORT_MANIFEST_NAME = ".omes_export_manifest.json"


def sha256_file(path: str) -> str:
    """Return the hex sha256 digest of the file at `path`."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_graph(graph_json_path: str) -> dict:
    """Load and lightly validate a graphify graph.json file.

    Returns a dict with at least "nodes" and "edges" keys (defaulting to
    empty lists when absent from the source file), never raises on a
    structurally-empty-but-valid graph.
    """
    with open(graph_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"graph.json must contain a JSON object, got {type(data).__name__}")
    data.setdefault("nodes", [])
    data.setdefault("edges", [])
    return data


def _slugify(value: str) -> str:
    """Turn an arbitrary source path into a safe, flat Markdown filename
    stem: path separators become "__", and anything else that is not
    alphanumeric/dot/dash/underscore is replaced with "_".
    """
    value = value.strip().lstrip("/")
    value = value.replace(os.sep, "__").replace("/", "__")
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return value or "note"


def _front_matter(fields: List[Tuple[str, str]]) -> str:
    lines = ["---"]
    for key, value in fields:
        # YAML-safe enough for the fixed, controlled set of values this
        # module ever writes (paths, versions, timestamps, hashes,
        # booleans) - always quoted as a string except the boolean marker.
        if value in ("true", "false"):
            lines.append(f"{key}: {value}")
        else:
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key}: "{escaped}"')
    lines.append("---")
    return "\n".join(lines) + "\n"


def has_omes_marker(existing_content: str) -> bool:
    """True when `existing_content` (an existing note's full text) carries
    the OMES-generated YAML front-matter marker. Used to decide whether an
    existing file may be safely overwritten.
    """
    if not existing_content.startswith("---"):
        return False
    end = existing_content.find("\n---", 3)
    if end == -1:
        return False
    front_matter = existing_content[:end]
    return OMES_GENERATED_MARKER in front_matter


def build_notes(
    graph: dict,
    *,
    project_name: str,
    source_root: str,
    graphify_version: str,
    extraction_mode: str,
    generated_at: str,
    graph_sha256: str,
) -> Dict[str, str]:
    """Render every note this export produces as {relative_path: content}.

    Always includes `_index.md` and `_provenance.md`, plus one note per
    unique file-like node in the graph. Never touches the filesystem.
    """
    notes: Dict[str, str] = {}

    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    file_nodes = [n for n in nodes if isinstance(n, dict) and n.get("type") == "file" and n.get("path")]
    # Deduplicate by path, preserving first-seen order.
    seen_paths = set()
    ordered_file_nodes = []
    for n in file_nodes:
        p = n["path"]
        if p in seen_paths:
            continue
        seen_paths.add(p)
        ordered_file_nodes.append(n)

    node_by_id = {n.get("id"): n for n in nodes if isinstance(n, dict) and "id" in n}

    # Symbols (functions/classes/etc.) grouped by the file that defines
    # them, via a "file" field the node carries (best-effort; nodes
    # without a resolvable "file" are simply not listed under any file
    # note - they still count toward the index note's totals).
    symbols_by_file: Dict[str, List[dict]] = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("type") == "file":
            continue
        file_ref = n.get("file")
        if file_ref:
            symbols_by_file.setdefault(file_ref, []).append(n)

    common_fields = [
        ("omes_generated", "true"),
        ("graphify_version", graphify_version),
        ("extraction_mode", extraction_mode),
        ("generated_at", generated_at),
        ("graph_sha256", graph_sha256),
    ]

    file_note_names: Dict[str, str] = {}
    for n in ordered_file_nodes:
        path = n["path"]
        file_note_names[path] = _slugify(path) + ".md"

    for n in ordered_file_nodes:
        path = n["path"]
        note_name = file_note_names[path]
        fields = [("source", path)] + common_fields
        body = [_front_matter(fields), "", f"# {path}", ""]

        syms = symbols_by_file.get(path, [])
        if syms:
            body.append("## Symbols")
            body.append("")
            for s in syms:
                name = s.get("name") or s.get("id") or "unknown"
                kind = s.get("type") or "symbol"
                body.append(f"- **{name}** ({kind})")
            body.append("")

        outgoing = []
        for e in edges:
            if not isinstance(e, dict):
                continue
            if e.get("source") != n.get("id"):
                continue
            target = node_by_id.get(e.get("target"))
            if not target:
                continue
            target_path = target.get("path") if target.get("type") == "file" else target.get("file")
            if target_path and target_path in file_note_names and target_path != path:
                edge_type = e.get("type", "EXTRACTED")
                outgoing.append((target_path, edge_type))

        if outgoing:
            body.append("## Links")
            body.append("")
            for target_path, edge_type in outgoing:
                target_note = file_note_names[target_path][:-3]  # strip .md
                body.append(f"- [[{target_note}]] ({edge_type})")
            body.append("")

        body.append(f"Source file: `{path}`")
        notes[note_name] = "\n".join(body) + "\n"

    # Index note.
    index_fields = [("source", source_root)] + common_fields
    index_body = [
        _front_matter(index_fields),
        "",
        f"# {project_name} - Graphify export",
        "",
        f"Extraction mode: `{extraction_mode}` (see docs/graphify.md §1.6 for "
        "EXTRACTED vs INFERRED edge provenance).",
        "",
        "## Files",
        "",
    ]
    for n in ordered_file_nodes:
        path = n["path"]
        note_stem = file_note_names[path][:-3]
        index_body.append(f"- [[{note_stem}]] - `{path}`")
    index_body.append("")
    index_body.append(f"See also: [[{PROVENANCE_NOTE_NAME[:-3]}]]")
    notes[INDEX_NOTE_NAME] = "\n".join(index_body) + "\n"

    # Provenance note.
    prov_fields = [("source", source_root)] + common_fields
    prov_body = [
        _front_matter(prov_fields),
        "",
        "# Graphify export provenance",
        "",
        f"- Source path: `{source_root}`",
        f"- Graphify version: `{graphify_version}`",
        f"- Extraction mode: `{extraction_mode}`",
        f"- Generated at: `{generated_at}`",
        f"- graph.json sha256: `{graph_sha256}`",
        f"- Nodes: {len(nodes)}",
        f"- Edges: {len(edges)}",
        "",
        f"See also: [[{INDEX_NOTE_NAME[:-3]}]]",
    ]
    notes[PROVENANCE_NOTE_NAME] = "\n".join(prov_body) + "\n"

    return notes


def plan_notes(notes: Dict[str, str], target_dir: str) -> Tuple[List[dict], List[str]]:
    """Compare rendered `notes` against what already exists under
    `target_dir` (never writes anything). Returns (planned, conflicts):

    - planned: a list of {"path", "action"} dicts, action is "create" or
      "update", for every note not in conflict.
    - conflicts: relative paths of existing files that already exist at
      that location but lack the OMES-generated marker - these are never
      included in `planned` and must never be overwritten.
    """
    planned = []
    conflicts = []
    for rel_path in sorted(notes):
        abs_path = os.path.join(target_dir, rel_path)
        if os.path.exists(abs_path):
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    existing = f.read()
            except OSError:
                existing = ""
            if has_omes_marker(existing):
                planned.append({"path": rel_path, "action": "update"})
            else:
                conflicts.append(rel_path)
        else:
            planned.append({"path": rel_path, "action": "create"})
    return planned, conflicts


def inject_front_matter_fields(content: str, fields: List[Tuple[str, str]]) -> str:
    """Add `fields` as extra keys inside an existing (or absent) YAML
    front-matter block, WITHOUT parsing/re-serializing the rest of the
    block - this preserves upstream `graphify export obsidian`'s own
    fields (including nested list values like `tags:`) exactly as
    written. If `content` has no front-matter block at all, one is
    prepended (built purely from `fields`, matching `_front_matter`).
    """
    parts = []
    for k, v in fields:
        if v in ("true", "false"):
            parts.append(f"{k}: {v}")
        else:
            escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
            parts.append(f'{k}: "{escaped}"')
    insertion = "\n".join(parts)

    if not content.startswith("---\n"):
        return _front_matter(fields) + "\n" + content

    end = content.find("\n---", 3)
    if end == -1:
        return _front_matter(fields) + "\n" + content

    return content[:end] + "\n" + insertion + content[end:]


def read_upstream_manifest(export_dir: str) -> List[str]:
    """Read upstream's own `.graphify_obsidian_manifest.json` (written by
    `graphify export obsidian --dir <export_dir>`) and return its "files"
    list (relative paths). Returns [] when the manifest is absent or
    unreadable - callers must treat that as "nothing to post-process",
    never as an error, since a manifest-less upstream export is itself the
    signal to fall back to OMES's own renderer (docs/graphify.md §5.1).
    """
    manifest_path = os.path.join(export_dir, UPSTREAM_MANIFEST_NAME)
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    files = data.get("files")
    if not isinstance(files, list):
        return []
    return [f for f in files if isinstance(f, str)]


def postprocess_upstream_export(
    export_dir: str,
    file_list: List[str],
    *,
    graphify_version: str,
    extraction_mode: str,
    generated_at: str,
    graph_sha256: str,
) -> Dict[str, str]:
    """Read every file `file_list` names (relative to `export_dir`,
    normally read_upstream_manifest()'s own output) and return
    {relative_path: content}. Markdown files get OMES's own front-matter
    fields injected via `inject_front_matter_fields` (source_file - if
    upstream's own front matter provides one - falls back to the relative
    path itself); every other file (the canvas, the vault's own nested
    .obsidian/graph.json, the upstream manifest itself) is returned
    unmodified - see OMES_EXPORT_MANIFEST_NAME for how those are tracked
    for the overwrite-refusal rule instead.
    """
    common_fields = [
        ("omes_generated", "true"),
        ("graphify_version", graphify_version),
        ("extraction_mode", extraction_mode),
        ("generated_at", generated_at),
        ("graph_sha256", graph_sha256),
    ]

    payload: Dict[str, str] = {}
    for rel in file_list:
        abs_path = os.path.join(export_dir, rel)
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue

        if rel.endswith(".md"):
            payload[rel] = inject_front_matter_fields(content, common_fields)
        else:
            payload[rel] = content

    return payload


def plan_upstream_export(payload: Dict[str, str], target_dir: str) -> Tuple[List[dict], List[str]]:
    """Like `plan_notes`, but for an upstream-produced `payload`
    (postprocess_upstream_export's output) that may contain non-Markdown
    files: a `.md` target is checked via `has_omes_marker` as usual; any
    other target is checked against the PREVIOUS export's own
    OMES_EXPORT_MANIFEST_NAME bookkeeping file (present at `target_dir`
    from a prior `write_upstream_export` call) - present and lists the
    path -> safe to overwrite; anything else (no bookkeeping file yet, or
    present but doesn't list this exact path) with a pre-existing file at
    that path -> conflict, never overwritten.
    """
    prior_owned = set(_read_export_manifest_files(target_dir))

    planned = []
    conflicts = []
    for rel_path in sorted(payload):
        abs_path = os.path.join(target_dir, rel_path)
        if not os.path.exists(abs_path):
            planned.append({"path": rel_path, "action": "create"})
            continue

        if rel_path.endswith(".md"):
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    existing = f.read()
            except OSError:
                existing = ""
            if has_omes_marker(existing):
                planned.append({"path": rel_path, "action": "update"})
            else:
                conflicts.append(rel_path)
        elif rel_path in prior_owned:
            planned.append({"path": rel_path, "action": "update"})
        else:
            conflicts.append(rel_path)

    return planned, conflicts


def _read_export_manifest_files(target_dir: str) -> List[str]:
    manifest_path = os.path.join(target_dir, OMES_EXPORT_MANIFEST_NAME)
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    files = data.get("files")
    if not isinstance(files, list):
        return []
    return [f for f in files if isinstance(f, str)]


def write_upstream_export(payload: Dict[str, str], target_dir: str) -> Tuple[List[str], List[str]]:
    """Write `payload` (postprocess_upstream_export's output) under
    `target_dir`, honoring `plan_upstream_export`'s conflict rule, then
    (re)write OMES_EXPORT_MANIFEST_NAME to record every path this call
    wrote - so the NEXT export recognizes them as OMES-owned. Returns
    (written, conflicts) - relative paths.
    """
    planned, conflicts = plan_upstream_export(payload, target_dir)
    os.makedirs(target_dir, exist_ok=True)
    written = []
    for item in planned:
        rel_path = item["path"]
        abs_path = os.path.join(target_dir, rel_path)
        os.makedirs(os.path.dirname(abs_path) or target_dir, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(payload[rel_path])
        written.append(rel_path)

    # Record every OMES-owned path (this write plus whatever this export
    # already owned from a previous run) so a future re-export still
    # recognizes untouched-this-time files as OMES-owned, not conflicts.
    owned = sorted(set(_read_export_manifest_files(target_dir)) | set(written))
    manifest_path = os.path.join(target_dir, OMES_EXPORT_MANIFEST_NAME)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"omes_generated": True, "files": owned}, f)
        f.write("\n")

    return written, conflicts


def plan_purge_export(target_dir: str) -> Tuple[List[str], List[str]]:
    """Scan an already-exported vault subdirectory (`target_dir`) and
    classify every file under it into (removable, skipped) relative
    paths, WITHOUT deleting anything (issue #55, docs/graphify-privacy.md
    "deletion/re-index procedure"):

    - A `.md` file is removable only when it carries the omes_generated
      marker (`has_omes_marker`).
    - Any other file is removable only when it is listed in this
      directory's own OMES_EXPORT_MANIFEST_NAME bookkeeping file
      (written by `write_upstream_export`) - i.e. only files OMES itself
      is known to have written, exactly the same ownership test
      `plan_upstream_export` already uses for the overwrite-refusal
      rule, reused here for the deletion-safety rule.
    - Everything else (a user-authored note, or any file this export
      never owned) is always skipped, never removed.

    Returns ([], []) when `target_dir` does not exist.
    """
    if not os.path.isdir(target_dir):
        return [], []

    owned_non_md = set(_read_export_manifest_files(target_dir))

    removable: List[str] = []
    skipped: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(target_dir):
        for name in filenames:
            abs_path = os.path.join(dirpath, name)
            rel_path = os.path.relpath(abs_path, target_dir)

            if rel_path == OMES_EXPORT_MANIFEST_NAME:
                # Handled last (after everything it lists), see purge_export.
                continue

            if rel_path.endswith(".md"):
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except OSError:
                    skipped.append(rel_path)
                    continue
                if has_omes_marker(content):
                    removable.append(rel_path)
                else:
                    skipped.append(rel_path)
            elif rel_path in owned_non_md:
                removable.append(rel_path)
            else:
                skipped.append(rel_path)

    return sorted(removable), sorted(skipped)


def purge_export(target_dir: str) -> Tuple[List[str], List[str]]:
    """Delete every OMES-owned file under `target_dir` per
    `plan_purge_export`'s classification, then remove
    OMES_EXPORT_MANIFEST_NAME itself and, if `target_dir` is now empty,
    `target_dir` itself. Never touches a `skipped` path. Returns
    (removed, skipped) - relative paths (`removed` does not include the
    directory itself, only files).
    """
    removable, skipped = plan_purge_export(target_dir)
    if not os.path.isdir(target_dir):
        return [], []

    removed = []
    for rel_path in removable:
        abs_path = os.path.join(target_dir, rel_path)
        try:
            os.remove(abs_path)
            removed.append(rel_path)
        except OSError:
            skipped.append(rel_path)

    manifest_path = os.path.join(target_dir, OMES_EXPORT_MANIFEST_NAME)
    if os.path.isfile(manifest_path):
        try:
            os.remove(manifest_path)
            removed.append(OMES_EXPORT_MANIFEST_NAME)
        except OSError:
            pass

    # Clean up now-empty directories (deepest first), but never remove a
    # directory that still contains a skipped (kept) file.
    for dirpath, dirnames, filenames in list(os.walk(target_dir, topdown=False)):
        if not dirnames and not filenames:
            try:
                os.rmdir(dirpath)
            except OSError:
                pass

    return sorted(removed), sorted(set(skipped))


def write_notes(notes: Dict[str, str], target_dir: str) -> Tuple[List[str], List[str]]:
    """Write `notes` under `target_dir`, refusing any note whose target
    already exists without the OMES-generated marker. Creates `target_dir`
    (and any parent) if missing. Returns (written, conflicts) - relative
    paths.
    """
    planned, conflicts = plan_notes(notes, target_dir)
    os.makedirs(target_dir, exist_ok=True)
    written = []
    for item in planned:
        rel_path = item["path"]
        abs_path = os.path.join(target_dir, rel_path)
        os.makedirs(os.path.dirname(abs_path) or target_dir, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(notes[rel_path])
        written.append(rel_path)
    return written, conflicts
