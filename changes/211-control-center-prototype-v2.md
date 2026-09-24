---
issue: 211
type: changed
---
Replaced the Control Center UI prototype (`ui/control-center/`) with redesign v2 and made its example data generated from the `contracts/control-center/v1` fixtures (plus a new `sample-fleet.json` supplement) via `scripts/generate-control-center-data.py`, instead of hand-typed in `index.html`; added accessibility fixes (contrast, focus-visible, reduced-motion, keyboard-reachable rows, `lang="id"`/`<title>`) and a `--check` freshness gate wired into `tests/run.sh`, `scripts/lint.sh`, and CI.
