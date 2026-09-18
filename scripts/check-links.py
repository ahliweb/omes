#!/usr/bin/env python3
"""Check that relative Markdown links in this repository resolve to real files.

Usage: scripts/check-links.py [path ...]
  (default: every tracked *.md file in the repository)

Only *relative* links are checked, e.g. "../security.md",
"./adr/0001-bash-as-implementation-language.md", or "docs/cli.md#exit-codes".
Absolute URLs (http://, https://, mailto:, ...) and pure same-page anchors
(#foo) are intentionally skipped -- this script is about catching broken
intra-repo links, not validating the internet.

A link whose target *file* does not exist is a hard failure (exit 1). A
link with a fragment (`#heading`) that cannot be matched against any
heading in the target file is printed as an advisory warning only, since
the heading-to-anchor slug algorithm implemented here is a best-effort
approximation of GitHub's and can have false positives.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
SKIP_SCHEMES = ("http://", "https://", "mailto:", "tel:", "ftp://")


def repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(out.stdout.strip())


def tracked_markdown_files(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--", "*.md"],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
    )
    return [root / p for p in out.stdout.splitlines() if p]


def is_relative_link(target: str) -> bool:
    target = target.strip()
    if not target or target.startswith("#"):
        return False
    lowered = target.lower()
    if any(lowered.startswith(s) for s in SKIP_SCHEMES):
        return False
    if target.startswith("<") and target.endswith(">"):
        return False
    return True


def slugify(heading: str) -> str:
    """Best-effort approximation of GitHub's heading-anchor slug algorithm."""
    text = heading.strip().lower()
    text = re.sub(r"[`*_]", "", text)
    text = "".join(c for c in text if c.isalnum() or c in (" ", "-", "_"))
    return text.strip().replace(" ", "-")


def heading_slugs(path: Path) -> set[str]:
    slugs: set[str] = set()
    if not path.exists() or not path.is_file():
        return slugs
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return slugs
    for line in text.splitlines():
        m = HEADING_RE.match(line)
        if m:
            slugs.add(slugify(m.group(2)))
    return slugs


def check_file(md_path: Path, root: Path, warnings: list[str], errors: list[str]) -> None:
    text = md_path.read_text(encoding="utf-8", errors="replace")
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # Strip inline code spans (`...`) first: illustrative syntax like
        # "a Markdown link (`[text](target)`)" is prose about link syntax,
        # not an actual link to resolve, but the surrounding parentheses
        # still make it match LINK_RE if left in place.
        scanned_line = INLINE_CODE_RE.sub(lambda mm: " " * len(mm.group(0)), line)
        for m in LINK_RE.finditer(scanned_line):
            raw = m.group(1).strip()
            target = raw.split(" ", 1)[0].strip('"\'')
            if not is_relative_link(target):
                continue
            path_part, _, anchor = target.partition("#")
            if not path_part:
                continue
            resolved = (md_path.parent / path_part).resolve()
            location = f"{md_path.relative_to(root)}:{lineno}"
            if not resolved.exists():
                errors.append(f"{location}: broken link to '{path_part}' (resolved: {resolved})")
                continue
            if anchor and resolved.suffix.lower() == ".md":
                slugs = heading_slugs(resolved)
                wanted = slugify(anchor.replace("-", " "))
                if slugs and wanted not in slugs and anchor.lower() not in slugs:
                    try:
                        display_target = resolved.relative_to(root)
                    except ValueError:
                        display_target = resolved
                    warnings.append(
                        f"{location}: anchor '#{anchor}' not found in {display_target} "
                        "(heading-slug heuristic; may be a false positive)"
                    )


def main(argv: list[str]) -> int:
    root = repo_root()
    if len(argv) > 1:
        files = [Path(p).resolve() for p in argv[1:]]
    else:
        files = tracked_markdown_files(root)

    errors: list[str] = []
    warnings: list[str] = []
    checked = 0
    for f in files:
        if f.suffix.lower() != ".md" or not f.exists():
            continue
        checked += 1
        check_file(f, root, warnings, errors)

    if warnings:
        print("check-links.py: advisory anchor warnings (non-blocking):")
        for w in warnings:
            print(f"  {w}")
        print()

    if errors:
        print("check-links.py: broken relative links found:")
        for e in errors:
            print(f"  {e}")
        return 1

    print(f"check-links.py: OK ({checked} markdown files checked, no broken relative links)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
