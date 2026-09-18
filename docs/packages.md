# OMES Package Compatibility and Repository Policy

> Status: implemented (issue #9). Describes `lib/omes/pkg.sh` as it exists in this
> repository today: the apt package helpers, the logical-name package-mapping table, the
> per-release override mechanism, repository validation/management, and the deterministic
> network/partial-failure behavior modules built on it must exhibit. `modules/apt-base/` is
> the only module using it so far; every later module that installs apt packages or adds a
> third-party repository (`security-baseline`, `containers`, `hermes`, desktop modules, etc.)
> must use these helpers rather than shelling out to `apt-get`/`add-apt-repository` directly.

## 1. API surface (`lib/omes/pkg.sh`)

| Function | Purpose |
|---|---|
| `pkg_is_installed <pkg>` | True when `dpkg-query` reports the (real, already-mapped) package name fully installed. |
| `pkg_missing <pkg...>` | Prints the not-yet-installed subset of the given real package names, one per line, input order preserved. |
| `pkg_apt_update` | Runs `apt-get update`, rate-limited to once per `OMES_PKG_APT_UPDATE_MAX_AGE` seconds (default 3600 = 1h), tracked via the `pkg.apt_update.last_run` state key. |
| `pkg_install <pkg...>` | Idempotent install: recomputes the actually-missing subset itself, runs `DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends <missing>`, records only the packages installed by this call into `module.<MODULE_NAME>.installed_packages`. |
| `pkg_candidate_version <pkg>` | Prints `apt-cache policy <pkg>`'s `Candidate:` version (empty/`(none)` when apt has no candidate). |
| `pkg_exists_in_repos <pkg>` | Validates a package is available in the configured repositories BEFORE any mutation. |
| `pkg_map <logical-name>` | Resolves a logical dependency name to the real package name for the current OS/version. |
| `pkg_map_binary <logical-name>` | Prints the executable name to look for after installing `pkg_map`'s result (differs from the package name for `bat`/`fd`). |
| `repo_validate <name> <uri> <suite> <signed-by-path>` | Verifies an apt source is well-formed: `https://` URI, keyring file exists at mode `0644`. |
| `repo_add <name> <uri> <suite> <signed-by-path> [components] [--ubuntu-only]` | Validates, then writes a deb822 `<name>.sources` file, backed up and registered via `omes_manage_path`. |
| `repo_remove <name>` | Removes `<name>.sources` if present, backed up and registered via `omes_manage_path` first. |

All of the above honor `OMES_DRY_RUN` (Section 3.4 of `docs/architecture.md`): in dry-run mode
they print the planned `apt-get`/file-write action via `omes_run`/`log_info` and mutate
nothing, including their own bookkeeping state keys.

## 2. Package-name mapping table

`pkg_map` resolves a **logical name** (what a module wants conceptually) to the **real apt
package name** (what actually gets installed) for the current `${OMES_OS_ID}:${OMES_OS_VERSION_ID}`.
Known divergences, valid across every currently supported OS unless overridden (Section 3):

| Logical name | Real package | Binary name | Notes |
|---|---|---|---|
| `bat` | `bat` | `batcat` | Debian/Ubuntu ship the binary as `batcat` to avoid a name clash with an existing `bat` package; a module must look for `batcat` after install (`pkg_map_binary bat`), not `bat`. |
| `fd` | `fd-find` | `fdfind` | Same story: the package is `fd-find`, the shipped binary is `fdfind`. |
| `eza` | `eza` | `eza` | Package name matches upstream, but is **not guaranteed to exist** in every supported release's repositories (older Ubuntu/Mint point releases may not carry it). A module that wants `eza` MUST call `pkg_exists_in_repos "$(pkg_map eza)"` first and, if it returns non-zero, report the package as unavailable (`log_warn`, skip it, continue) rather than add a PPA or any other third-party source to obtain it — see Section 4 ("No PPAs unless allowlisted"). |

Any logical name with no entry in the mapping table passes through unchanged (e.g. `curl` ->
`curl`), so `pkg_map`/`pkg_install` are safe to call even for packages that need no mapping.

## 3. Per-release override mechanism

Some divergences are tied to a **specific** OS *version*, not the whole OS family (for
example, a package renamed or split starting in one release). These are handled by the
`PKG_MAP_RELEASE_OVERRIDES` associative array in `lib/omes/pkg.sh`, keyed
`"${OMES_OS_ID}:${OMES_OS_VERSION_ID}:<logical-name>"`:

```bash
# lib/omes/pkg.sh
declare -gA PKG_MAP_RELEASE_OVERRIDES=(
  # [ubuntu:22.04:eza]="eza-legacy"   # example only - no real overrides exist yet
)
```

`pkg_map` checks this table first, then the default `PKG_MAP_DEFAULT` table (Section 2), then
falls back to the logical name unchanged. Adding a real override is a reviewed change to this
one table (plus a row in this document and a fixture-driven unit test in
`tests/unit/pkg.bats`) — never an inline OS-version `case` statement duplicated inside a
module.

## 4. Repository policy

- **`https://` only.** `repo_validate`/`repo_add` reject any non-`https://` URI outright.
- **`Signed-By` keyring required, mode `0644`.** Every repository OMES adds must carry an
  explicit `Signed-By: <path>` pointing at a keyring file that already exists at mode `0644`
  (world-readable, matching how `apt-get update` itself needs to read it) — `repo_add` never
  writes the keyring file itself; the module that owns the keyring content is responsible for
  placing it (via `omes_manage_path`) before calling `repo_add`.
- **deb822 `.sources` files only, never `/etc/apt/sources.list`.** `repo_add`/`repo_remove`
  write/remove exactly one file, `<OMES_APT_SOURCES_DIR:-/etc/apt/sources.list.d>/<name>.sources`,
  in the modern deb822 format. `/etc/apt/sources.list` itself is never read or written by OMES.
- **Backed up and tracked.** Every write/removal goes through `omes_manage_path`, so the file
  is backed up before it is touched (when a backup session is active) and recorded in
  `module.<name>.managed_paths` — `omes backup`/`omes restore`/`omes uninstall` (issue #10)
  can find and reverse it.
- **No PPAs unless explicitly allowlisted here.** OMES does not call `add-apt-repository` for
  an arbitrary PPA at runtime (`docs/security.md` Section 6). As of this writing, **no PPA is
  allowlisted** — every module target (Section 2's `bat`/`fd`/`eza`, and anything added later)
  must come from the distribution's own repositories or an explicit `repo_add` entry meeting
  the policy above. A package unavailable under this policy is reported unavailable
  (`pkg_exists_in_repos` failing at check time), never worked around with a PPA.
- **Linux Mint + Ubuntu-only third-party repositories (e.g. Docker).** Docker publishes
  official support for Ubuntu only (`docs/scope.md` Section 4.6). On Linux Mint,
  `lib/omes/detect.sh`'s `detect_os` already resolves `OMES_OS_CODENAME` to
  `UBUNTU_CODENAME` (not the Mint codename) whenever it is present in `/etc/os-release` — so a
  caller adding an Ubuntu-only repo simply passes `$OMES_OS_CODENAME` as `<suite>`. Passing
  `--ubuntu-only` to `repo_add` additionally makes it emit the documented non-parity `WARN`
  when the host is Linux Mint, e.g.:

  ```bash
  repo_add docker "https://download.docker.com/linux/ubuntu" "$OMES_OS_CODENAME" \
    "/etc/apt/keyrings/docker.gpg" --ubuntu-only
  ```

  This never claims Docker support parity between Ubuntu and Mint; it only ensures the
  correct (Ubuntu) suite is used and that the operator is told, every time, that no parity is
  claimed.

## 5. Failure-mode matrix

`pkg_*`/`repo_*` functions never call `exit` themselves; they return one of a small set of
exit-code-shaped sentinels (reusing the `OMES_EX_*` constants from `lib/omes/core.sh` so the
meaning is self-documenting), and it is the caller — `module_check`/`module_apply`, then
`lib/omes/module.sh`'s `run_checks`/`run_apply` — that turns that into the process exit code.

| Condition | Detected by | Returned by the function | Process exit code | Why |
|---|---|---|---|---|
| Network required but unavailable, during `module_check` (nothing missing installs yet) | `pkg_exists_in_repos` calling `detect_network` | `$OMES_EX_NETWORK` (8) | **4** (preflight failed) | `docs/architecture.md` Section 11: any `module_check` failure — including one caused by absent network — surfaces as a preflight failure during `check`/`install`; no mutation has happened yet, so there is nothing to roll back. |
| Package unknown/renamed in the configured repositories (network is up) | `pkg_exists_in_repos` calling `apt-cache policy` | `$OMES_EX_PREFLIGHT` (4) | **4** (preflight failed) | Validated before mutation, exactly the acceptance criterion for issue #9: "missing packages are reported before mutation where possible." |
| Network drops between `module_check` and `module_apply` (or is absent going straight into `module_apply`, e.g. a module invoked outside the normal check-then-apply runner) | `pkg_apt_update` / `pkg_install` calling `detect_network` | `$OMES_EX_NETWORK` (8) | **8** (network required but unavailable) | `lib/omes/module.sh`'s `run_apply` recognizes a `module_apply` return of exactly `$OMES_EX_NETWORK` and maps the process exit code to 8 instead of the generic apply-failure code, so this specific, recoverable condition is distinguishable in automation from a real apt/package failure. |
| `apt-get update`/`apt-get install` itself exits non-zero (broken repo, dpkg lock, disk full, etc.) | `pkg_apt_update` / `pkg_install` checking the command's exit status | `$OMES_EX_MODULE_APPLY` (6) | **6** (module apply failed, names the module) | Ordinary apply-time failure; the module's managed paths up to that point were already backed up by the runner, and the module is left in a state an operator (or a future `omes restore`/`omes uninstall`, issue #10) can inspect and recover from. |
| Repository fails `repo_validate` (non-`https://` URI, keyring missing, or keyring has the wrong mode) | `repo_validate` (read-only) | non-zero (`1`) | Whatever the caller's own contract maps a `module_check`/`module_apply` failure to (4 during check, 6 during apply) | `repo_add` refuses to write anything when validation fails - never a "best effort" partial repository. |

`OMES_ASSUME_OFFLINE=1` (paired with `OMES_ASSUME_ONLINE=0` if a test/harness has already set
`OMES_ASSUME_ONLINE=1`, matching `detect_network`'s own precedence) forces every network check
in this file to report offline deterministically, without needing to simulate a real outage -
this is what `tests/integration/pkg.bats` and `tests/unit/pkg.bats` use.

## 6. Testing

- `tests/shims/apt-get` logs every invocation and simulates package installs (so a later
  `dpkg-query` shim call reports them installed); `SHIM_APT_GET_FAIL_UPDATE=1` /
  `SHIM_APT_GET_FAIL_INSTALL=1` make it simulate an apt-level failure.
- `tests/shims/apt-cache` simulates `apt-cache policy <pkg>`; `SHIM_APT_CACHE_UNKNOWN_PKGS`
  (space-separated) makes specific packages report no candidate (unavailable), and
  `SHIM_APT_CACHE_VERSION` controls the fake candidate version for everything else.
- `tests/unit/pkg.bats` covers `pkg_map` per fixture OS (`tests/fixtures/os-release/*`),
  `pkg_missing`, `pkg_apt_update`'s rate-limiting/dry-run/offline/apt-failure paths,
  `pkg_exists_in_repos`'s offline-vs-unknown distinction, `pkg_install`'s idempotency and
  `installed_packages` bookkeeping, and `repo_validate`/`repo_add`/`repo_remove` (including
  rejecting `http://` and a missing/wrong-mode keyring, and the Linux Mint `--ubuntu-only`
  warning).
- `tests/integration/pkg.bats` proves, through the real `bin/omes` CLI against `apt-base`,
  that a package missing from the configured repos fails `omes check`/`omes install` at
  **check** time (exit 4) with **no** `apt-get install` call logged, that offline behaves the
  same way (exit 4, not 8, per Section 5), and that a normal install calls `apt-get install`
  exactly once and records `installed_packages`.

<!-- OMES-MERMAID: docs/packages.md -->

## Visual summary

```mermaid
flowchart LR
    Module[OMES module] --> Map[Package-name mapping]
    Map --> Repo[Repository policy]
    Repo --> Install[Install package]
    Install --> Verify[Verify command and version]
    Verify --> Override[Apply release override when needed]
```

