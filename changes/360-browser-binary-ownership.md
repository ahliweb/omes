---
issue: 360
type: security
---
`omes health browser-worker` now also refuses, when run as root, to execute a browser binary whose immediate parent directory is not root-owned or is group/world-writable: the result is `blocked` with the new stable reason code `BROWSER_BINARY_DIR_UNSAFE` (engine and version `unknown`) and `<browser> --version` is never run, extending the existing `BROWSER_BINARY_UNSAFE` check on the binary itself; a directory that cannot be `stat`ed fails closed, a missing binary keeps `BROWSER_BINARY_MISSING`, and non-root runs are unaffected. Negative and positive unit tests use a faked `stat`, so they hold when the suite itself runs as root. Real-host exit codes for `omes health budget` (item 1 of #360) are Not implemented yet (tracked in #360).
