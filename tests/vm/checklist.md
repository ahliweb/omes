# Linux Mint desktop VM checklist (manual)

> Status: manual only. Not automated (issue #15) - see
> [`docs/testing.md`](../../docs/testing.md) "Known gaps" for why, and
> [`tests/vm/run.sh`](run.sh)'s header comment for the automated
> Ubuntu-Server-only VM path this checklist complements.

Linux Mint does not publish an official cloud image (the format
[`tests/vm/run.sh`](run.sh) automates against), only installer ISOs meant
for an interactive or fully-scripted (preseed/autoinstall) install. Building
and maintaining a reliable unattended Mint ISO installer is out of scope for
this issue; instead, this is a **short, human-executable checklist** an
operator runs once per Mint 22.x point release (or before a beta/public
release per `docs/business/release-gates.md`).

## Preconditions

- A Linux Mint 22.x ISO (get it from <https://linuxmint.com/download.php>;
  verify its published SHA256 yourself before use).
- A VM (libvirt/virt-manager, VirtualBox, or bare metal) with at least 8GB
  RAM and 20GB disk free (per `docs/compatibility-matrix.md` section 4.1's
  desktop-profile minimums) and a GPU that resolves to Tier 1/2 in
  `docs/compatibility-matrix.md` section 4.2 (a virtual GPU is Desktop
  Tier 3 / best-effort only - expect some checklist items below to be
  informational-only under virtio-gpu/QXL, noted per item).
- This repository checked out on the Mint guest (e.g. `git clone` the PR
  branch, or copy the working tree over - same principle as
  `tests/vm/run.sh`'s "copy the exact tree under test", not a curl of
  `main`).

## Procedure

For each box below: perform the step, record **PASS** or **FAIL** plus a
one-line note, and save any relevant screenshot/terminal capture into
`tests/vm/evidence/<timestamp>-mint-desktop/` (create the directory; it is
gitignored, same as the automated run's evidence bundles - see
`docs/testing.md` "Evidence and release gates" for how this is referenced
from a PR body / release review instead of committed to the repo).

- [ ] `omes check` reports tier1 (desktop) for this Mint version - not
      "unsupported", not silently defaulting to the server profile.
- [ ] `sudo omes install --profile server --dry-run --yes` then
      `sudo omes install --profile server --yes` (root-scope modules)
      complete without error.
- [ ] `omes install --profile desktop --yes` (user-scope modules; today
      this is `apt-base` only per `profiles/desktop.profile` - the
      Hyprland session modules are tracked in issue #8 and this box may
      currently just confirm there is nothing further to apply. Re-check
      this box once #8 merges, per the file-scope note in this issue's
      brief).
- [ ] Log out of the current session.
- [ ] **Login screen shows a session selector** with both the OMES-managed
      Hyprland entry and the stock Cinnamon entry (once #8 ships this
      entry; until then this box is "not yet applicable, tracked in #8").
- [ ] Log in to the Hyprland session; it starts without falling back to a
      failsafe/black screen.
- [ ] Log out of Hyprland, log back in to **Cinnamon** (the fallback
      session) - confirms the fallback is not broken by the Hyprland
      session's install (`docs/business/release-gates.md` section 3 lists
      "Cinnamon fallback not guaranteed to work" as a desktop-release delay
      trigger).
- [ ] Suspend the machine (`systemctl suspend` or the session menu) and
      resume; the session is usable afterward (no black screen requiring a
      hard reset).
- [ ] Multi-monitor: with two displays attached (or two virtual outputs via
      the hypervisor), both are usable and correctly positioned in at least
      one of the two sessions above. Under a virtual GPU (Tier 3 desktop,
      per the compatibility matrix), note whether this is even exercisable
      in your VM setup - a FAIL here on virtio-gpu is a known best-effort
      limitation, not a release blocker by itself.
- [ ] Screen sharing (e.g. a WebRTC test page, or `wf-recorder`/`grim` if
      installed) works under the Hyprland session via
      `xdg-desktop-portal-wlr` (per `docs/compatibility-matrix.md` section
      5.2).
- [ ] `omes uninstall --profile desktop --dry-run` then
      `--yes` complete without error and the login screen still offers
      Cinnamon afterward.

## Recording the result

Append a one-line summary per box to the evidence directory's `RESULT.md`
(box name, PASS/FAIL/NOT-YET-APPLICABLE, one-line note), and reference that
evidence directory from the PR body per `docs/testing.md`'s evidence
conventions. A FAIL on any box that `docs/business/release-gates.md` section
3 lists as a desktop-release delay trigger blocks a desktop beta date, per
that document's re-evaluation trigger.
