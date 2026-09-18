---
issue: 87
type: changed
---
`bin/omes` now loads additional top-level commands from `lib/omes/cmd/<name>.sh` (listed by `omes help` under "Extension commands"), so new command families can be added without editing the dispatcher.
