---
issue: 64
type: added
---
Add `omes content scan|rescan|list` (`lib/omes/cmd/content.sh` +
`lib/omes/py/content/`): detects new inbox files by sha256 + size/mtime
settle window, creates versioned job records, moves settled files into
`processing/<job-id>/source.<ext>` atomically, skips duplicates with
`duplicate_of`, never follows symlinks, and guards concurrent scans with a
stale-reclaimable lock file. First user of the `python3 -m unittest discover
-s tests/py -t .` hook in `tests/run.sh`.
