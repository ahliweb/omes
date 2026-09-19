"""`python3 -m graphify.cli <action> ...` - stdlib-only CLI glue invoked by
lib/omes/cmd/graphify.sh (issues #53, #54). Never called directly by an
operator; all path validation, vault existence checks, confirmation, and
backups happen in the bash layer before this runs. This script only
prints a single JSON object to stdout in every case (docs/architecture.md:
single JSON object on stdout).

Obsidian export actions (issue #53, docs/graphify.md §5):

- `plan`/`write` render Markdown notes directly from graph.json using
  OMES's own renderer (lib/omes/py/graphify/obsidian.py's build_notes) -
  used when the upstream `graphify export obsidian` command is
  unavailable or fails.
- `plan-upstream`/`write-upstream` post-process an ALREADY-RUN
  `graphify export obsidian --dir <staging-dir>` output (verified
  2026-09-19 against graphify 0.9.64 to produce real vault-ready
  Markdown with its own YAML front matter) by injecting OMES's required
  front-matter fields into every `.md` file it listed in its own
  `.graphify_obsidian_manifest.json`, then applying the same
  marker-based overwrite-refusal rule when copying into the vault.

Change-detection actions (issue #54, docs/graphify.md §6):

- `scan` snapshots the source tree, diffs it against a manifest file
  (creating/updating it when `--write` is given), and reports whether
  anything changed.
- `status` is the read-only version of `scan` (never writes the
  manifest) - backs `omes graphify status`.

Privacy/deletion actions (issue #55, docs/graphify-privacy.md):

- `purge-plan`/`purge` classify (and, for `purge`, delete) every file
  under an exported vault subdirectory into OMES-owned (safe to
  remove) versus everything else (always kept) - backs `omes graphify
  purge`.

No secrets are ever read, accepted, or emitted by this script.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from . import obsidian, sync


def _build_args(argv):
    parser = argparse.ArgumentParser(prog="graphify-cli")
    sub = parser.add_subparsers(dest="action", required=True)

    for name in ("plan", "write"):
        p = sub.add_parser(name)
        p.add_argument("--graph-json", required=True)
        p.add_argument("--target-dir", required=True)
        p.add_argument("--project-name", required=True)
        p.add_argument("--source-root", required=True)
        p.add_argument("--graphify-version", default="unknown")
        p.add_argument("--extraction-mode", default="unknown")

    for name in ("plan-upstream", "write-upstream"):
        p = sub.add_parser(name)
        p.add_argument("--staging-dir", required=True)
        p.add_argument("--target-dir", required=True)
        p.add_argument("--graph-json", required=True)
        p.add_argument("--graphify-version", default="unknown")
        p.add_argument("--extraction-mode", default="unknown")

    for name in ("scan", "status"):
        p = sub.add_parser(name)
        p.add_argument("--path", required=True)
        p.add_argument("--manifest", required=True)
        p.add_argument("--exclude", action="append", default=[])
        p.add_argument("--max-file-mb", type=float, default=sync.DEFAULT_MAX_FILE_MB)
        if name == "scan":
            p.add_argument("--write", action="store_true")

    for name in ("purge-plan", "purge"):
        p = sub.add_parser(name)
        p.add_argument("--target-dir", required=True)

    return parser.parse_args(argv)


def _render_payload_from_graph(args):
    graph = obsidian.load_graph(args.graph_json)
    graph_sha256 = obsidian.sha256_file(args.graph_json)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return obsidian.build_notes(
        graph,
        project_name=args.project_name,
        source_root=args.source_root,
        graphify_version=args.graphify_version,
        extraction_mode=args.extraction_mode,
        generated_at=generated_at,
        graph_sha256=graph_sha256,
    )


def _render_payload_from_upstream(args):
    file_list = obsidian.read_upstream_manifest(args.staging_dir)
    if not file_list:
        return None
    graph_sha256 = obsidian.sha256_file(args.graph_json)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return obsidian.postprocess_upstream_export(
        args.staging_dir,
        file_list,
        graphify_version=args.graphify_version,
        extraction_mode=args.extraction_mode,
        generated_at=generated_at,
        graph_sha256=graph_sha256,
    )


def _load_sync_manifest(manifest_path):
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "files" not in data:
        return None
    return data


def _run_scan_or_status(args) -> int:
    old_manifest = _load_sync_manifest(args.manifest)
    old_files = old_manifest["files"] if old_manifest else {}

    new_files = sync.scan_tree(args.path, args.exclude, max_file_mb=args.max_file_mb)
    added, removed, modified = sync.diff_snapshot(old_files, new_files)
    changed = sync.has_changes(added, removed, modified)
    first_run = old_manifest is None

    result = {
        "ok": True,
        "changed": bool(changed or first_run),
        "first_run": first_run,
        "added": added,
        "removed": removed,
        "modified": modified,
        "file_count": len(new_files),
    }

    if args.action == "scan" and getattr(args, "write", False):
        # Always write on `scan --write` (even with no changes) so
        # last_run_at advances - lib/omes/cmd/graphify.sh's debounce
        # (--min-interval) reads this field, and it must reflect every
        # completed sync attempt, not only ones that found changes.
        manifest = {
            "schema_version": sync.MANIFEST_SCHEMA_VERSION,
            "last_run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "files": new_files,
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.manifest)), exist_ok=True)
        tmp_path = args.manifest + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f)
            f.write("\n")
        os.replace(tmp_path, args.manifest)
        result["manifest_written"] = True
    else:
        result["manifest_written"] = False

    print(json.dumps(result))
    return 0


def _run_purge(args) -> int:
    if args.action == "purge-plan":
        removable, skipped = obsidian.plan_purge_export(args.target_dir)
        print(json.dumps({"ok": True, "removable": removable, "skipped": skipped}))
        return 0

    removed, skipped = obsidian.purge_export(args.target_dir)
    print(json.dumps({"ok": True, "removed": removed, "skipped": skipped}))
    return 0


def main(argv=None) -> int:
    args = _build_args(sys.argv[1:] if argv is None else argv)

    if args.action in ("scan", "status"):
        try:
            return _run_scan_or_status(args)
        except OSError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1

    if args.action in ("purge-plan", "purge"):
        try:
            return _run_purge(args)
        except OSError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1

    try:
        if args.action in ("plan", "write"):
            payload = _render_payload_from_graph(args)
            plan_fn, write_fn = obsidian.plan_notes, obsidian.write_notes
        else:
            payload = _render_payload_from_upstream(args)
            if payload is None:
                print(json.dumps({"ok": False, "error": "no upstream manifest found in staging dir"}))
                return 1
            plan_fn, write_fn = obsidian.plan_upstream_export, obsidian.write_upstream_export
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    if args.action in ("plan", "plan-upstream"):
        planned, conflicts = plan_fn(payload, args.target_dir)
        print(json.dumps({"ok": True, "planned": planned, "conflicts": conflicts, "note_count": len(payload)}))
        return 0

    written, conflicts = write_fn(payload, args.target_dir)
    print(json.dumps({"ok": True, "written": written, "conflicts": conflicts, "note_count": len(payload)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
