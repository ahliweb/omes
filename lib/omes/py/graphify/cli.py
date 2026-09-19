"""`python3 -m graphify.cli <plan|write|plan-upstream|write-upstream> ...` -
stdlib-only CLI glue invoked by lib/omes/cmd/graphify.sh (issue #53). Never
called directly by an operator; all path validation, vault existence
checks, confirmation, and backups happen in the bash layer before this
runs. This script only prints a single JSON object to stdout in every
case (docs/architecture.md: single JSON object on stdout).

Two rendering paths (docs/graphify.md §5):

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

No secrets are ever read, accepted, or emitted by this script.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from . import obsidian


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


def main(argv=None) -> int:
    args = _build_args(sys.argv[1:] if argv is None else argv)

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
