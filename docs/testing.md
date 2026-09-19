# OMES Testing

> Status: describes the test suite implemented in issue [#15](https://github.com/ahliweb/omes/issues/15)
> (installation/regression test matrix) as it exists in this repository today, building on the
> unit/integration bats suite from issue [#6](https://github.com/ahliweb/omes/issues/6) and the
> lint/secret-scan/supply-chain CI from issue [#16](https://github.com/ahliweb/omes/issues/16)
> (see [docs/ci.md](ci.md) for that layer). Rollback/disaster-recovery testing specifically is
> covered in issue [#17](https://github.com/ahliweb/omes/issues/17); see `docs/disaster-recovery.md`
> (not yet on this branch - added by #17) and [docs/rollback.md](rollback.md)'s "Tested
> scenarios" section for that layer once it exists.

## 1. The test pyramid

```text
                    manual desktop checklist   (tests/vm/checklist.md - Mint only)
                  VM matrix (real reboot)       (tests/vm/run.sh - Ubuntu Server only)
              container regression matrix      (scripts/test-matrix.sh)
          integration bats (shims, bin/omes)    (tests/integration/*.bats)
      unit bats (shims, lib/omes/*.sh directly)  (tests/unit/*.bats)
```

| Layer | What it proves | Speed | Where |
|---|---|---|---|
| Unit bats | `lib/omes/*.sh` functions in isolation, sourced directly, against `tests/shims/` fakes (`apt-get`, `systemctl`, `sudo`, `hermes`, `curl`, ...) and `tests/fixtures/os-release/*` variants. | Seconds | `tests/unit/*.bats` |
| Integration bats | `bin/omes` end-to-end, still against `tests/shims/` (never a real `apt-get`), `OMES_DRY_RUN=1` where the scenario doesn't need a real mutation. | Seconds | `tests/integration/*.bats` |
| Container regression matrix | Real `bin/omes` against a **real** `apt-get`/`dpkg` inside disposable Docker containers for each supported OS - fresh install, idempotent re-run, offline behavior, a genuinely failing package install, a reboot proxy, and a backup/restore/uninstall round-trip. | Minutes | `scripts/test-matrix.sh`, results in `tests/matrix/` |
| VM matrix | What containers structurally cannot prove: a **real** reboot and (manually) a real Linux Mint desktop session. | Tens of minutes | `tests/vm/run.sh`, `tests/vm/checklist.md` |
| Manual desktop checklist | Login/logout, session selector, suspend, multi-monitor, screen sharing on real Linux Mint hardware/VM. | Human-paced | `tests/vm/checklist.md` |

Each layer is a superset check on the layer below it: the container matrix does not re-derive
`lib/omes/restore.sh`'s restore-vs-remove decision logic (that's unit-tested already), it proves
the real CLI commands work end-to-end on a real filesystem; the VM matrix does not re-derive
"does apt-base install packages" (the container matrix already proved that), it proves what only
a real reboot can prove.

## 2. Running each layer locally

### 2.1 Unit + integration bats (and ShellCheck + `bash -n`)

```bash
./tests/run.sh
```

Uses a locally installed `bats`/`shellcheck` when present, falling back to Docker
(`koalaman/shellcheck:stable`, a derived `bats/bats:latest` image with `python3` and `git`
layered on for the JSON-validity assertions and the `omes update` git tests - see
[ADR-0009](adr/0009-testing-with-bats-in-containers.md)) otherwise.

### 2.1a Python (`lib/omes/py/**`, stdlib-only per ADR-0012)

`./tests/run.sh` also runs, on the host (not inside a container):

```bash
python3 -m py_compile $(find lib/omes/py -name '*.py')
python3 -m unittest discover -s tests/py -t .
```

`tests/py/health/test_ollama.py` (issue #71) is the first suite here; it
exercises `lib/omes/py/health/ollama.py` against a stdlib
`http.server`-based fake Ollama endpoint - no real network, no real
Ollama, and no third-party test dependency (ADR-0012 forbids anything
outside the standard library, including test-only packages). Add new
Python logic under `lib/omes/py/<package>/` with tests under
`tests/py/<package>/test_*.py`; `tests/run.sh` discovers them
automatically.

### 2.2 Container regression matrix

```bash
scripts/test-matrix.sh                                    # full default matrix
OMES_MATRIX_IMAGES="ubuntu:24.04" scripts/test-matrix.sh   # one image
OMES_MATRIX_SCENARIOS="fresh rerun" scripts/test-matrix.sh # a subset of scenarios
OMES_MATRIX_FAIL_PKG=curl scripts/test-matrix.sh           # override the partial-failure scenario's package
```

Requires Docker. Runs, per image in `OMES_MATRIX_IMAGES` (default: `ubuntu:24.04`,
`ubuntu:22.04`, `linuxmintd/mint22-amd64`):

| Scenario | What it proves |
|---|---|
| `fresh` | `omes check --json` exits 0/3 per tier; a root, sudo-less `omes install --profile server --dry-run --yes`; then a **real** `omes install --module apt-base --yes`. |
| `rerun` | A second real install makes no *substantive* change (no `apt-get install` call) - see [4. Known gaps](#4-known-gaps) for the one non-blocking caveat. |
| `offline` | `OMES_ASSUME_OFFLINE=1`: `status`/`restore --list`/`uninstall --dry-run` still work; `install` fails deterministically (exit 4) and mutates nothing. |
| `partial-failure` | A forced `apt-get install` failure (via `tests/matrix/shims/apt-get`, mounted ahead on `PATH`) surfaces as exit 6, naming the module. |
| `reboot` | A container stop/start proxy for a real reboot - see [4. Known gaps](#4-known-gaps). |
| `rollback` | `omes backup` → modify → `omes restore` → checksum equality; a plain `omes uninstall` leaves packages installed. |

The repository is bind-mounted **read-only** at `/omes` in every container; only a per-scenario
writable state directory and the container's own writable root filesystem (for real `apt-get`
calls) are ever mutated. Each scenario writes `tests/matrix/results/<image>-<scenario>.json`
(schema documented in [`tests/matrix/results/README.md`](../tests/matrix/results/README.md)) and
a full transcript to `tests/matrix/logs/<image>-<scenario>.log` (both gitignored - see
[3. Evidence and release gates](#3-evidence-and-release-gates)). The runner prints a summary
table and exits non-zero only if a **Tier-1** image/scenario combination failed (Tier-2/3 are
advisory, matching [docs/ci.md](ci.md) section 3's blocking/advisory split).

### 2.3 VM matrix

```bash
tests/vm/run.sh --image /path/to/ubuntu-24.04-server-cloudimg-amd64.img
OMES_VM_SKIP_HERMES=1 tests/vm/run.sh --image <path>   # skip the Hermes install/verify steps
```

Requires `virt-install`/`virsh`/`qemu-img` (`apt-get install virtinst qemu-system-x86
libvirt-daemon-system genisoimage` on Ubuntu), KVM, and an operator-supplied Ubuntu Server 24.04
cloud image (this script deliberately never downloads one itself - see `tests/vm/run.sh`'s
header comment and [`tests/vm/README.md`](../tests/vm/README.md)). Boots the image, copies this
exact working tree over SSH, runs check → install → a **real** `reboot` → post-reboot
assertions, and writes a timestamped, sha256-summed evidence bundle under `tests/vm/evidence/`
(gitignored).

### 2.4 Manual desktop checklist

Follow [`tests/vm/checklist.md`](../tests/vm/checklist.md) by hand against a real (or virtualized)
Linux Mint 22.x install. Not automated - see [4. Known gaps](#4-known-gaps) for why.

## 3. Evidence and release gates

`docs/business/release-gates.md` cites this test suite as the proof for several of its
technical gates:

| Release gate | Proven by |
|---|---|
| Installation success on the Tier-1 matrix | `scripts/test-matrix.sh`'s `fresh` scenario, both Tier-1 images |
| Re-run idempotency | `scripts/test-matrix.sh`'s `rerun` scenario |
| Reboot survival | `scripts/test-matrix.sh`'s `reboot` scenario (container proxy) **and** `tests/vm/run.sh` (real reboot) - see [4. Known gaps](#4-known-gaps) for why both matter |
| Docs walkthrough by a second operator | `tests/vm/checklist.md`'s procedure, run by someone other than the primary author |

A specific run's evidence is **not** committed to the repository (`tests/matrix/results/`,
`tests/matrix/logs/`, and `tests/vm/evidence/` are all gitignored except
`tests/matrix/results/README.md`). Instead:

- **Locally**: the files land on disk under `tests/matrix/`/`tests/vm/evidence/` and are pasted
  or attached (the JSON results, the summary table, a `tar`/zip of an evidence bundle) into the
  pull request body per the engineering brief's PR-body "Verification" section convention, and
  into `docs/business/release-gates.md`'s go/no-go checklist as "the specific artifact/run that
  proves each row" (that document's section 5, step 1).
- **In CI**: `.github/workflows/compatibility.yml` uploads `tests/matrix/results/` and
  `tests/matrix/logs/` as a build artifact named `omes-test-matrix-<image>` per matrix job run
  (and `omes-real-install-ubuntu-24.04` for the dedicated idempotency job) - that artifact's URL
  (from the Actions run) is the durable, referenceable evidence a release review cites.

## 4. What blocks a release vs. what is advisory

Blocking (a failing check fails the PR/blocks a release, per this document's evidence table and
[docs/ci.md](ci.md)):

- `tests/run.sh` (ShellCheck, unit bats, integration bats) - unchanged from issue #6/#16.
- `scripts/test-matrix.sh` for the two Tier-1 images (`ubuntu:24.04`, `linuxmintd/mint22-amd64`),
  per `docs/compatibility-matrix.md` section 1's "a regression here blocks the release."
- `.github/workflows/compatibility.yml`'s `real-install-ubuntu-24-04` job (`continue-on-error:
  false` as of this issue, now that apt-base exists to install for real).

Advisory (reported, does not fail the PR/block a release by itself):

- `scripts/test-matrix.sh` for `ubuntu:22.04` (Tier 2 - "tested periodically... regressions are
  tracked but do not block a release by themselves," per `docs/compatibility-matrix.md`).
- `tests/vm/run.sh` and `tests/vm/checklist.md` are not run in CI at all (they need a
  KVM-capable host and, for the checklist, a human) - they are release-review artifacts an
  operator runs and attaches manually before a stage transition, per
  `docs/business/release-gates.md` section 5.

## 5. How to add a scenario

1. Write a new `scenario_<name>()` function in `scripts/test-matrix.sh` (see the existing ones
   for the pattern: build/reuse a container via `mx_start`, run commands via `mx_exec`/`mx_json`,
   set `ok=0` and a note on any unexpected result, always call `write_result` at the end).
2. Add `<name>` to `DEFAULT_SCENARIOS` and to `run_image`'s dispatch (`_has_scenario <name>` +
   the call).
3. Decide whether the new scenario needs the shared "main" container (already `apt-base`-applied
   - reuse it, like `reboot`/`rollback` do) or its own fresh one (like `offline`/
   `partial-failure` do, when the scenario needs a never-installed host).
4. Run it locally against at least `ubuntu:24.04` (`OMES_MATRIX_SCENARIOS="<name>"
   scripts/test-matrix.sh`) before adding it to `DEFAULT_SCENARIOS` for every image/every CI run.
5. Document the new scenario in this file's section 2.2 table and, if it changes what blocks a
   release, in section 4 above.

## 6. Known gaps

Documented explicitly per the engineering brief's rule to describe actual, not aspirational,
behavior - none of these are silently papered over in the scripts themselves (see the relevant
script's own comments for the same notes, cross-referenced here):

- **`linuxmintd/mint22-amd64`'s own `/etc/os-release` reports `ID=ubuntu`.** It is an
  Ubuntu-Noble-based package-build image with Mint apt sources added (`packages.linuxmint.com
  wilma`), not a real Mint install - `omes check` detects it as Ubuntu 24.04 Tier 1, **not** as
  Linux Mint. It exercises the server-profile `apt-base` path on a Mint-adjacent apt
  configuration only. Real Mint `ID`/tier detection is covered by `tests/unit/detect.bats`'s
  fixtures (`tests/fixtures/os-release/linuxmint-*`); real Mint desktop-profile end-to-end
  behavior is `tests/vm/checklist.md`'s job, manually.
- **No container in the matrix runs systemd as PID 1** (`systemctl` is entirely absent from the
  base images). `scripts/test-matrix.sh`'s `reboot` scenario substitutes a `docker stop`/`docker
  start` cycle (kills and restarts the container's PID 1, keeps the filesystem) and checks that
  installed packages and OMES's own state file survive that - it does **not** exercise real
  `systemctl is-enabled`/unit re-enablement across a boot. That is `tests/vm/run.sh`'s job (a
  real VM, a real `reboot`, and a real `systemctl is-enabled hermes-gateway` check afterward).
- **`apt-base` manages no config file** (`omes_manage_path` is never called by
  `modules/apt-base/module.sh` - it only tracks installed packages). The `rollback` scenario
  therefore seeds one synthetic OMES-managed path itself to exercise the real `omes
  backup`/`restore`/`uninstall` CLI end-to-end on a real filesystem. The restore-vs-remove
  *decision logic* (pre-existing vs. OMES-created path) is not re-derived here - it is already
  covered by `tests/unit/restore.bats` and `tests/integration/restore.bats`/`uninstall.bats`.
- **`rerun`'s `applied_at`-unchanged check.** `lib/omes/module.sh`'s `run_apply` keeps
  `module.<name>.applied_at` stable across a no-op re-apply (it only moves when a module
  transitions into `applied`) and separately records `last_run_at` on every run, so both the
  substantive idempotency signal (no `apt-get install` call) and the timestamp signal are
  blocking assertions in the `rerun` scenario.
- **`omes install`'s network-unavailable exit code is 4 for `apt-base` specifically, not 8.**
  Both are documented as valid ("network required but unavailable") in `docs/cli.md`, but which
  one actually happens depends on *when* the network check runs relative to `module_check`
  passing: `apt-base`'s `module_check` itself calls `pkg_exists_in_repos` (which needs network)
  for every missing package, so an offline `omes install` on a host missing `apt-base`'s
  packages always fails at the preflight stage (exit 4) before `module_apply` (where exit 8
  would apply) is ever reached. The `offline` scenario asserts exit 4 specifically and documents
  why, rather than accepting either code opaquely.
- **`tests/vm/run.sh` has not been executed end-to-end in this issue's authoring environment.**
  It is written, `bash -n`-checked, and ShellCheck-clean (both `koalaman/shellcheck:stable` and
  `:v0.9.0`, `-S warning`), but provisioning and booting a real VM, waiting for cloud-init, and
  rebooting it takes several real minutes on a KVM-capable host with an operator-supplied cloud
  image - neither of which the sandboxed session that wrote it could exercise safely within its
  time budget. Run it on a real workstation or a self-hosted CI runner with KVM before treating a
  pass as release-gate evidence for the "Reboot survival" gate specifically (the container
  matrix's stop/start proxy is not a substitute - see `tests/vm/README.md`).
- **Linux Mint desktop verification is entirely manual** (`tests/vm/checklist.md`). Mint
  publishes no official cloud image (the format `tests/vm/run.sh` automates against), only
  installer ISOs meant for an interactive or preseed/autoinstall install; building and
  maintaining a reliable unattended Mint installer is out of this issue's scope.
- **Hermes/hermes-gateway are never installed for real by `scripts/test-matrix.sh`** (only
  `apt-base` is, today) - they are user-scope modules needing a real non-root user/session and a
  real download from `hermes-agent.nousresearch.com`, both of which add meaningful complexity to
  a container-based harness. `tests/vm/run.sh` does install and verify them for real (skippable
  via `OMES_VM_SKIP_HERMES=1`), so that VM run is currently the only automated, real (non-shim)
  proof of the Hermes/gateway install path end-to-end.
