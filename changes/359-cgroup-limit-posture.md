---
issue: 359
type: added
---
`omes health budget` now reports the host cgroup limits of the `hermes-gateway` unit instead of `host_limits: "not_collected"`. It reads `MemoryMax`, `CPUQuotaPerSecUSec` and `TasksMax` with the fixed, read-only, time-bounded `systemctl [--user] show hermes-gateway` (no `sudo`, nothing is changed). `host_limits` is now an object: each limit is `bounded`, `unlimited` (`infinity`) or `unknown` and names its deciding and enforcing authority, the values are labelled `systemctl_runtime_property` (unit properties, not proof of the effective kernel limit), and a host without the gateway unit reports `not_applicable`. Host limits are informational and never change the budget status or exit code. The AWCMS budget-policy contract, the hard-deny versus soft-warning tests, usage telemetry intake, the scoped emergency stop and Compose limits are Not implemented yet (tracked in #359).
