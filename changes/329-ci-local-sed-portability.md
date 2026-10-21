---
issue: 329
type: fixed
---
Fixed `scripts/ci-local.sh` failing every job when run inside the Alpine-based local bats image: its proxy-redaction log filter called GNU-only `sed -u`, which BusyBox `sed` rejects, so 23 tests in `tests/unit/ci-local.bats` and `tests/unit/ci-local-docker.bats` failed there. The runner now uses unbuffered `sed -u` only when the local `sed` supports it and falls back to plain `sed` otherwise, with identical redaction output.
