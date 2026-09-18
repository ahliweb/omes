# tests/vm/ - VM matrix (issue #15)

Proves what `scripts/test-matrix.sh`'s container matrix structurally cannot:
a real reboot, and (manually, see below) a real Linux Mint desktop session.
Per `docs/compatibility-matrix.md` section 1, Tier-1 platforms are "tested
in CI containers **and** in a VM before every release" - the container
matrix is the first half, this directory is the second.

| Path | What it is | Automated? |
|---|---|---|
| [`run.sh`](run.sh) | Boots Ubuntu Server 24.04 from an operator-supplied cloud image via `virt-install`, copies this exact repository over SSH, runs `omes check` → `omes install --profile server --yes` → a real `reboot` → post-reboot assertions (packages/state survive; Hermes/gateway present and enabled unless `OMES_VM_SKIP_HERMES=1`), and writes an evidence bundle. | Yes |
| [`checklist.md`](checklist.md) | Linux Mint desktop verification (login/logout, Hyprland ↔ Cinnamon fallback, suspend, multi-monitor, screen sharing). | No — manual, by design (see checklist.md's header for why) |
| [`cloud-init/`](cloud-init) | `user-data.tmpl`/`meta-data.tmpl` templates `run.sh` fills in (creates one sudo user with an SSH key; nothing else). | — |
| `evidence/` | Timestamped evidence bundles from real runs. Gitignored (see the repository `.gitignore`); never committed. | — |

## Requirements

See `run.sh`'s own header comment for the exact package list
(`virtinst qemu-system-x86 libvirt-daemon-system genisoimage` on a stock
Ubuntu host) and why this script never auto-downloads a cloud image or ISO
for you (supply-chain policy: a network artifact you are about to boot as a
VM disk is an explicit, reviewed, operator action, matching
`scripts/check-supply-chain.sh`'s spirit for this repository's shell code).

## Running it

```bash
# One-time: get an Ubuntu Server 24.04 cloud image and verify its checksum
# yourself (https://cloud-images.ubuntu.com/releases/24.04/release/), e.g.:
#   curl -fsSL -o ubuntu-24.04-server-cloudimg-amd64.img \
#     https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img
#   (compare against that release's published SHA256SUMS)

tests/vm/run.sh --image ./ubuntu-24.04-server-cloudimg-amd64.img
OMES_VM_SKIP_HERMES=1 tests/vm/run.sh --image ./ubuntu-24.04-server-cloudimg-amd64.img
tests/vm/run.sh --image ./ubuntu-24.04-server-cloudimg-amd64.img --keep   # leave the VM up for debugging
```

For the Mint desktop side: follow [`checklist.md`](checklist.md) by hand.

## Known limitation: not executed end-to-end while authoring this PR

`run.sh` was written, `bash -n`-checked, and is ShellCheck-clean (both
`koalaman/shellcheck:stable` and `:v0.9.0`, `-S warning`), but has **not**
been run end-to-end in the sandboxed environment this PR was authored in -
provisioning and booting a real VM, waiting for cloud-init, and rebooting it
takes several real minutes and a KVM-capable host with an operator-supplied
cloud image, neither of which this authoring session could exercise safely
within its time budget. Run it on a real workstation or a self-hosted CI
runner with KVM before treating a pass as release-gate evidence — see
`docs/testing.md`'s "Known gaps" for how this is flagged there too, and
`docs/business/release-gates.md` section 1.1's "Reboot survival" gate for
why the container matrix's stop/start proxy (see
`scripts/test-matrix.sh`'s header) is not a substitute for actually running
this.
