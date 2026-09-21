#!/usr/bin/env bash
# shellcheck shell=bash
#
# tests/vm/run.sh - VM matrix runner for what the container matrix
# (scripts/test-matrix.sh) cannot prove: a real reboot, systemd unit
# re-enablement across it, and (best-effort) a real Linux Mint desktop
# session. Issue #15; see docs/compatibility-matrix.md section 1's Tier-1
# requirement ("tested in CI containers AND in a VM").
#
# Approach chosen (documented honestly, per the issue's instructions - see
# "Requirements" below for why): libvirt/virt-install + cloud-init, driving
# a local KVM/QEMU VM. This was picked over Vagrant (an extra dependency
# with its own provider plugin ecosystem, not installed by default on
# Ubuntu) and over Multipass (Canonical's own tool, but not preinstalled
# either, and less transparent about the exact QEMU/libvirt invocation than
# calling virt-install directly). `apt-get install virtinst
# qemu-system-x86 libvirt-daemon-system genisoimage` is the one-line
# prerequisite on a stock Ubuntu host; see "Requirements" below.
#
# Flow: boot Ubuntu Server 24.04 from an operator-supplied cloud image (this
# script deliberately never downloads one itself - see "Why no auto-
# download" below) -> wait for SSH -> copy this repository over (not
# install/bootstrap.sh's curl pipeline, which targets a public GitHub raw
# URL; copying the actual working tree is what proves *this* branch/PR) ->
# `omes check` -> `omes install --profile server --yes` -> real `reboot` ->
# reconnect -> assert hermes is present and the gateway unit is enabled
# (skippable with OMES_VM_SKIP_HERMES=1, since Hermes Agent's installer
# needs its own upstream network access) -> write an evidence bundle.
#
# Usage:
#   tests/vm/run.sh --image /path/to/ubuntu-24.04-server-cloudimg-amd64.img
#   OMES_VM_SKIP_HERMES=1 tests/vm/run.sh --image <path>
#   tests/vm/run.sh --image <path> --keep     # leave the VM running for debugging
#
# A Linux Mint run is NOT automated by this script (see checklist.md): Mint
# ships no official cloud image, so the desktop profile's install/reboot/
# session verification is manual, following tests/vm/checklist.md.
#
# HONESTY NOTE (read before treating a run of this script as evidence):
# this script has been written, `bash -n`-checked, and ShellCheck-clean,
# but has NOT been executed end-to-end in the sandboxed environment this
# PR was authored in - provisioning and booting a real VM, waiting for
# cloud-init, and rebooting it takes several minutes of real wall-clock
# time and this session's environment does not have a safe, bounded way
# to do that. Run it on a real workstation/CI runner with KVM before
# relying on its result as release-gate evidence (docs/testing.md
# "Known gaps" repeats this).

set -Eeuo pipefail
IFS=$'\n\t'

SELF="$(readlink -f "$0")"
VMDIR="$(cd "$(dirname "$SELF")" && pwd)"
ROOT="$(cd "$VMDIR/.." && cd .. && pwd)"
cd "$ROOT"

log() { printf '[vm-run] %s\n' "$*"; }
err() { printf '[vm-run] ERROR %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------
#   - virt-install, virsh (package virtinst / libvirt-clients on Ubuntu)
#   - qemu-system-x86_64, qemu-img (package qemu-system-x86)
#   - libvirtd running (package libvirt-daemon-system), current user in the
#     `libvirt` group (or run this script with sudo - the latter is what a
#     CI runner would typically need anyway)
#   - genisoimage or cloud-localds (package genisoimage or cloud-image-utils)
#     to build the cloud-init seed ISO
#   - /dev/kvm present (hardware virtualization enabled) - a nested-VM CI
#     runner without KVM can still run this with `--no-kvm` (falls back to
#     TCG software emulation, MUCH slower - expect boot to take minutes)
#   - An Ubuntu Server 24.04 cloud image (.img/.qcow2), operator-supplied via
#     --image. Get one from https://cloud-images.ubuntu.com/releases/24.04/release/
#     (verify its SHA256SUMS file yourself before use - this script does not
#     fetch or verify it for you, see "Why no auto-download" below)
#   - An SSH keypair; --ssh-key defaults to ~/.ssh/id_ed25519.pub (generates
#     one with `ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519` if missing)
#
# Why no auto-download: scripts/check-supply-chain.sh (issue #16) forbids
# piping a network download into an interpreter, and more broadly this
# repository's policy is that anything fetched from the network and then
# treated as trusted (here: booted as a VM disk) is a reviewed, explicit,
# operator action - not a default a test script takes on your behalf.

IMAGE=""
MINT_ISO=""
KEEP=0
NO_KVM=0
SSH_KEY="${HOME}/.ssh/id_ed25519.pub"
VM_NAME="omes-vm-$$"
MEMORY_MB="${OMES_VM_MEMORY_MB:-4096}"
VCPUS="${OMES_VM_VCPUS:-2}"
DISK_GB="${OMES_VM_DISK_GB:-20}"
VM_USER="omes"

usage() {
  cat <<'EOF'
Usage: tests/vm/run.sh --image <ubuntu-cloud-image> [options]

Options:
  --image <path>      Ubuntu Server 26.04 or 24.04 cloud image (.img/.qcow2). Required.
  --mint-iso <path>   Linux Mint 22 ISO path (accepted for parity with the
                       issue's spec; NOT automated today - see checklist.md.
                       Passing this only prints a pointer to the manual
                       checklist and does not attempt a Mint boot).
  --ssh-key <path>    SSH public key to inject (default: ~/.ssh/id_ed25519.pub)
  --keep              Leave the VM running after the run (for debugging)
  --no-kvm            Use TCG software emulation instead of KVM (slow)
  -h, --help          Show this help

Env overrides:
  OMES_VM_SKIP_HERMES=1     Skip the Hermes Agent install/verify steps
                            (Hermes's installer needs its own upstream
                            network access; this run stops at the
                            server-profile apt-base/OS-level checks)
  OMES_VM_MEMORY_MB, OMES_VM_VCPUS, OMES_VM_DISK_GB
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image)
      IMAGE="$2"
      shift 2
      ;;
    --mint-iso)
      MINT_ISO="$2"
      shift 2
      ;;
    --ssh-key)
      SSH_KEY="$2"
      shift 2
      ;;
    --keep)
      KEEP=1
      shift
      ;;
    --no-kvm)
      NO_KVM=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      err "unknown option: $1"
      usage
      exit 2
      ;;
  esac
done

if [[ -n "$MINT_ISO" ]]; then
  log "Linux Mint desktop verification is NOT automated by this script."
  log "See tests/vm/checklist.md for the manual procedure using ${MINT_ISO}."
fi

if [[ -z "$IMAGE" ]]; then
  err "--image <ubuntu-24.04-cloud-image> is required"
  usage
  exit 2
fi
if [[ ! -r "$IMAGE" ]]; then
  err "image not readable: ${IMAGE}"
  exit 2
fi
if [[ ! -r "$SSH_KEY" ]]; then
  err "SSH public key not readable: ${SSH_KEY} (generate one: ssh-keygen -t ed25519 -N '' -f ${SSH_KEY%.pub})"
  exit 2
fi

for cmd in virt-install virsh qemu-img; do
  command -v "$cmd" >/dev/null 2>&1 || {
    err "required command not found: ${cmd} (see this script's 'Requirements' comment)"
    exit 2
  }
done
SEED_TOOL=""
if command -v cloud-localds >/dev/null 2>&1; then
  SEED_TOOL="cloud-localds"
elif command -v genisoimage >/dev/null 2>&1; then
  SEED_TOOL="genisoimage"
else
  err "neither cloud-localds nor genisoimage found (install cloud-image-utils or genisoimage)"
  exit 2
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_DIR="${VMDIR}/evidence/${TS}"
mkdir -p "$EVIDENCE_DIR"
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/omes-vm.XXXXXX")"
DISK="${WORKDIR}/${VM_NAME}.qcow2"
SEED="${WORKDIR}/seed.iso"

cleanup() {
  local rc=$?
  if [[ "$KEEP" != "1" ]]; then
    virsh destroy "$VM_NAME" >/dev/null 2>&1 || true
    virsh undefine "$VM_NAME" --nvram >/dev/null 2>&1 || true
    rm -rf "$WORKDIR"
  else
    log "kept: VM '${VM_NAME}' and workdir ${WORKDIR} (use --keep intentionally, remember to clean up)"
  fi
  exit "$rc"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 1. Build the disk (copy-on-write over the operator's base image) and the
#    cloud-init seed ISO.
# ---------------------------------------------------------------------------

log "building disk (copy-on-write over ${IMAGE})"
qemu-img create -f qcow2 -F qcow2 -b "$IMAGE" "$DISK" "${DISK_GB}G" \
  | tee -a "${EVIDENCE_DIR}/00-qemu-img.log"

SSH_PUBKEY_CONTENT="$(tr -d '\n' <"$SSH_KEY")"
USER_DATA="${WORKDIR}/user-data"
META_DATA="${WORKDIR}/meta-data"
sed -e "s#__OMES_VM_USER__#${VM_USER}#g" \
  -e "s#__OMES_VM_SSH_PUBKEY__#${SSH_PUBKEY_CONTENT}#g" \
  "${VMDIR}/cloud-init/user-data.tmpl" >"$USER_DATA"
sed -e "s#__OMES_VM_NAME__#${VM_NAME}#g" \
  "${VMDIR}/cloud-init/meta-data.tmpl" >"$META_DATA"

log "building cloud-init seed ISO (${SEED_TOOL})"
if [[ "$SEED_TOOL" == "cloud-localds" ]]; then
  cloud-localds "$SEED" "$USER_DATA" "$META_DATA"
else
  genisoimage -output "$SEED" -volid cidata -joliet -rock "$USER_DATA" "$META_DATA" \
    >"${EVIDENCE_DIR}/00-genisoimage.log" 2>&1
fi

# ---------------------------------------------------------------------------
# 2. virt-install: define and boot the VM, network via a NAT-mode libvirt
#    network with an SSH port forward isn't a standard libvirt feature, so
#    we use --network network=default with a static-ish DHCP lease and
#    resolve the guest's IP via `virsh domifaddr` after boot instead of
#    trying to port-forward.
# ---------------------------------------------------------------------------

# shellcheck disable=SC2054  # commas below are literal virt-install option values, not array separators
VIRT_INSTALL_ARGS=(
  --name "$VM_NAME"
  --memory "$MEMORY_MB"
  --vcpus "$VCPUS"
  --disk "path=${DISK},format=qcow2"
  --disk "path=${SEED},device=cdrom"
  --os-variant ubuntu24.04
  --network network=default,model=virtio
  --graphics none
  --noautoconsole
  --import
)
[[ "$NO_KVM" == "1" ]] && VIRT_INSTALL_ARGS+=(--virt-type qemu) || VIRT_INSTALL_ARGS+=(--virt-type kvm)

log "virt-install: creating and booting ${VM_NAME} (${MEMORY_MB}MB, ${VCPUS} vCPU)"
virt-install "${VIRT_INSTALL_ARGS[@]}" | tee -a "${EVIDENCE_DIR}/01-virt-install.log"

# ---------------------------------------------------------------------------
# 3. Wait for an IP, then for SSH.
# ---------------------------------------------------------------------------

log "waiting for the guest to acquire a DHCP lease"
GUEST_IP=""
for _ in $(seq 1 60); do
  GUEST_IP="$(virsh domifaddr "$VM_NAME" 2>/dev/null | awk '/ipv4/ {print $4}' | cut -d/ -f1 | head -n1)"
  [[ -n "$GUEST_IP" ]] && break
  sleep 5
done
if [[ -z "$GUEST_IP" ]]; then
  err "guest never acquired an IP address (see ${EVIDENCE_DIR}/01-virt-install.log)"
  exit 1
fi
log "guest IP: ${GUEST_IP}"

SSH=(ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 "${VM_USER}@${GUEST_IP}")
SCP=(scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5)

log "waiting for SSH and cloud-init to finish"
SSH_UP=0
for _ in $(seq 1 60); do
  if "${SSH[@]}" -o BatchMode=yes true 2>/dev/null; then
    SSH_UP=1
    break
  fi
  sleep 5
done
if [[ "$SSH_UP" != "1" ]]; then
  err "SSH never became reachable at ${GUEST_IP}"
  exit 1
fi
"${SSH[@]}" 'cloud-init status --wait' | tee -a "${EVIDENCE_DIR}/02-cloud-init-status.log" || true

# ---------------------------------------------------------------------------
# 4. Copy the repository (this exact working tree, not a git clone of a
#    remote ref) and run bootstrap -> check -> install.
# ---------------------------------------------------------------------------

log "copying the repository to the guest"
TARBALL="${WORKDIR}/omes-repo.tar.gz"
git -C "$ROOT" archive --format=tar.gz -o "$TARBALL" HEAD
"${SCP[@]}" "$TARBALL" "${VM_USER}@${GUEST_IP}:/tmp/omes-repo.tar.gz"
"${SSH[@]}" 'mkdir -p ~/omes && tar -xzf /tmp/omes-repo.tar.gz -C ~/omes'

log "omes check (read-only)"
"${SSH[@]}" 'cd ~/omes && ./bin/omes check --json' | tee -a "${EVIDENCE_DIR}/03-check.json" || true

log "omes install --profile server --yes (real, as root via sudo)"
"${SSH[@]}" 'cd ~/omes && sudo ./bin/omes install --profile server --yes' \
  | tee -a "${EVIDENCE_DIR}/04-install-server.log"

if [[ "${OMES_VM_SKIP_HERMES:-0}" != "1" ]]; then
  log "omes install --module hermes,hermes-gateway --yes (as the VM user, real network to hermes-agent.nousresearch.com)"
  "${SSH[@]}" 'cd ~/omes && ./bin/omes install --module hermes --module hermes-gateway --yes' \
    | tee -a "${EVIDENCE_DIR}/05-install-hermes.log" || true
else
  log "OMES_VM_SKIP_HERMES=1: skipping Hermes install/verify"
fi

# ---------------------------------------------------------------------------
# 5. Real reboot, reconnect, post-reboot assertions.
# ---------------------------------------------------------------------------

log "rebooting the guest (real reboot -- the whole point of this harness)"
"${SSH[@]}" 'sudo reboot' || true

log "waiting for the guest to go down, then come back up"
sleep 10
SSH_UP=0
for _ in $(seq 1 60); do
  if "${SSH[@]}" -o BatchMode=yes true 2>/dev/null; then
    SSH_UP=1
    break
  fi
  sleep 5
done
if [[ "$SSH_UP" != "1" ]]; then
  err "guest never came back up after reboot"
  exit 1
fi
log "guest is back up after a real reboot"

"${SSH[@]}" 'cd ~/omes && ./bin/omes status --json' | tee -a "${EVIDENCE_DIR}/06-status-post-reboot.json" || true
"${SSH[@]}" 'sudo ./bin/omes doctor' | tee -a "${EVIDENCE_DIR}/07-doctor-post-reboot.log" || true

HERMES_OK=1
if [[ "${OMES_VM_SKIP_HERMES:-0}" != "1" ]]; then
  log "asserting hermes is present and the gateway unit is enabled after reboot"
  if ! "${SSH[@]}" 'command -v hermes' >"${EVIDENCE_DIR}/08-hermes-present.log" 2>&1; then
    HERMES_OK=0
    err "hermes binary not found on PATH after reboot"
  fi
  if ! "${SSH[@]}" 'systemctl --user is-enabled hermes-gateway 2>/dev/null || sudo systemctl is-enabled hermes-gateway' \
    >"${EVIDENCE_DIR}/09-hermes-gateway-enabled.log" 2>&1; then
    HERMES_OK=0
    err "hermes-gateway unit is not enabled after reboot"
  fi
fi

"${SSH[@]}" 'sudo journalctl -u hermes-gateway --no-pager -n 200 2>/dev/null; journalctl --user -u hermes-gateway --no-pager -n 200 2>/dev/null' \
  >"${EVIDENCE_DIR}/10-journal-hermes-gateway.log" 2>&1 || true

# ---------------------------------------------------------------------------
# 6. Evidence bundle: sha256sum of everything collected.
# ---------------------------------------------------------------------------

(cd "$EVIDENCE_DIR" && sha256sum -- * >SHA256SUMS 2>/dev/null || true)
log "evidence bundle: ${EVIDENCE_DIR}"

if [[ "$HERMES_OK" != "1" ]]; then
  err "one or more post-reboot Hermes assertions failed; see ${EVIDENCE_DIR}"
  exit 1
fi

log "VM run complete: reboot survived, hermes-gateway assertions passed (or skipped via OMES_VM_SKIP_HERMES)"
exit 0
