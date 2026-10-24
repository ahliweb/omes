# UI/UX redesign — Control Center inside AWCMS (October 2026)

Reference-only design proposal that carries the
[`control-center-v3/`](../control-center-v3/README.md) proposal into the AWCMS
admin shell, where the functional Control Center screens live
(`ahliweb/awcms` `/admin/omes/*`). It keeps the v3 palette, status vocabulary
and information architecture, and adds an OMES workspace with its own
navigation, a lifecycle strip, and data freshness per authority. UI copy is
Indonesian.

**Not implemented.** These pages render example data only and call no API.
They visualize signals that each authority reports; they do not imply that OMES
schedules, routes or supervises Hermes agents, and the approval screen does not
add any operation beyond the existing safe-operation allowlist.

## How to view

```bash
python3 -m http.server 8080   # from the repository root
# then open http://localhost:8080/design/redesain-ui-ux-2026-10/OmesFleet.dc.html
```

Each `*.dc.html` uses the existing vendor runtime
[`ui/control-center/support.js`](../../ui/control-center/support.js) by relative
path, so serve the repository root. As in `control-center-v3/`, pages load
Public Sans and JetBrains Mono from Google Fonts and fall back to system fonts
offline. [`canvas.json`](canvas.json) records the artboard layout.

## Contents

| File                                             | Screen                                                                                                                                                     | Interactive              |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------ |
| [`Main.dc.html`](Main.dc.html)                   | 00 · Analysis of awcms, awcms-one, omes and awcms-astro                                                                                                   | no                       |
| [`OmesFleet.dc.html`](OmesFleet.dc.html)         | Fleet overview: workspace switcher, production banner, lifecycle strip, "needs action", freshness per authority, risk-sorted fleet with multi-select        | filter, host selection   |
| [`OmesOperation.dc.html`](OmesOperation.dc.html) | Operation approval for `corr-op-0001`: plan, preflight, worker steps, four-eyes rule, required note and typed host confirmation                             | note, typed confirmation |

## Data provenance

- Hosts and resource values come from
  [`ui/control-center/sample-fleet.json`](../../ui/control-center/sample-fleet.json);
  operation identifiers from
  `contracts/control-center/v1/fixtures/operation-request/valid-01.json`.
- The approval scenario (owner approval required, medium risk) follows the
  `control-center-v3` design scenario; the fixture itself records
  `requires_approval: false`.
- Preflight results, worker ID and backup ID on the approval screen are design
  examples, not fixture data.
