# OMES Competitive Landscape

> Status: Hypothesis-stage research summary. Confidence levels below describe source reliability, not certainty that OMES will win or lose against each alternative.
> Baseline: [docs/research-and-implementation-plan.md](../research-and-implementation-plan.md), sections 2.1–2.2; [positioning.md](positioning.md) for the messaging that follows from this comparison.
> All sources checked 2026-09-18 unless noted otherwise.

OMES is an independent, Omarchy-inspired, MIT-licensed project. It is never described as official Omarchy. Hermes Agent is a Nous Research product; OMES integrates with it but does not own, resell, or speak for it.

## Validated

The facts in the comparison table's "Source / confidence / date checked" columns are the validated portion of this document — each is backed by a fetched, dated source. Everything in the "why choose/not choose OMES" sections remains Hypothesis (no interviews or pilots have run yet; see [icp-and-customer-discovery.md](icp-and-customer-discovery.md)).

## Comparison matrix

Dimensions: features, distribution (how you get it and stay current), support, lock-in, security posture, maintenance burden, cost model. Confidence reflects how directly the source supports the stated fact, not a judgment of the competitor's quality.

### Omarchy (Arch-based)

| Dimension | Notes |
|---|---|
| Features | Full opinionated desktop distribution: Arch base, Hyprland tiling window manager, Quickshell "desktop construction kit," Neovim, terminal/TUI workflows, themes, AI CLIs, update channels, snapshots. Not a package list — an integrated, keyboard-first workflow. |
| Distribution | ISO-based install onto Arch; documented dual-boot and unattended install paths. |
| Support | Community-driven (GitHub issues, manual/docs); no vendor SLA identified. |
| Lock-in | High at the OS level — adopting Omarchy means adopting Arch and Hyprland, not layering onto an existing Ubuntu/Mint host. |
| Security posture | Described as security-by-default with update channels and system snapshots (per the plan's section 2.4); disk encryption/bootloader claims are Omarchy's own and not independently verified here. |
| Maintenance burden | Borne by the Omarchy maintainers for the whole stack (Arch rolling release + Hyprland + Quickshell); end users get that maintenance "for free" as long as they stay on Arch. |
| Cost model | Open-source; no published pricing found. |
| Source | [omarchy.org/manual](https://omarchy.org/manual/) | Confidence: **high** (direct fetch of the manual) | Date checked: 2026-09-18 |

### Omakub (Ubuntu desktop setup) — retired, folded into Omarchy

| Dimension | Notes |
|---|---|
| Features | A one-command script that turned a fresh Ubuntu 24.04 install into a configured web-development desktop (Git, Docker, Neovim/VS Code, curated app set). |
| Distribution | Single install command on top of stock Ubuntu + GNOME; no ISO. |
| Support | **Retired as of the time of this research** — the original repositories are archived, and the project's own site now explains that its creator moved on to build Omarchy instead of continuing to layer onto Ubuntu/GNOME. A community fork, "Omabuntu," continues the Ubuntu-based approach. |
| Lock-in | Low — it configured an existing Ubuntu install rather than replacing the OS; easy to walk away from since it only applied dotfiles/package choices. |
| Security posture | Not a security-focused project; standard Ubuntu/GNOME security model, no additional hardening claimed beyond app/tool choices. |
| Maintenance burden | Now zero from the original maintainer's side (archived); Omabuntu's maintenance burden is unverified from this research. |
| Cost model | Was free/open-source; no pricing model existed. |
| Source | ["Omakub" — omarchy.org/omakub](https://omarchy.org/omakub) and [github.com/omacom/omakub](https://github.com/omacom/omakub) | Confidence: **high** (direct fetch + corroborating GitHub repo description: "Retired — the story lives at omarchy.org/omakub") | Date checked: 2026-09-18 |

This is a material, dated finding: at research time, Omakub is not an actively maintained alternative. Messaging that treats Omakub as a live competitor should instead point to (a) Omarchy itself, now Arch-based, and (b) Omabuntu, the community continuation, and (c) vanilla Ubuntu/Mint, which is what most people comparing "Omakub-style setup" are actually choosing between today.

### Vanilla Ubuntu Server LTS / Linux Mint

| Dimension | Notes |
|---|---|
| Features | Whatever the base distribution ships: no opinionated dev/AI-agent workflow, no bundled Hermes integration, no keyboard-first desktop layer by default. |
| Distribution | Ubuntu Server ships schema-validated autoinstall YAML for provisioning; official ISO/cloud images. Linux Mint is an Ubuntu derivative with its own LTS cadence and Cinnamon as the default desktop. |
| Support | Ubuntu: commercial support available from Canonical; community support otherwise. Mint: community-driven, no Canonical-equivalent commercial support tier identified. |
| Lock-in | Lowest of any option in this table — it's the base platform everything else, including OMES, builds on. |
| Security posture | Standard distro security model (apt security updates, AppArmor on Ubuntu); no agent/Hermes-specific hardening out of the box. |
| Maintenance burden | Borne entirely by the user/operator; nothing beyond the OS is opinionated or automated. |
| Cost model | Free; Ubuntu Pro / commercial support is a separate, priced offering from Canonical (price not restated here — see Canonical's own pricing, not verified in this research pass). |
| Source | [ubuntu.com/about/release-cycle](https://ubuntu.com/about/release-cycle) | Confidence: **medium** (release-cycle page, not a full feature/support audit) | Date checked: 2026-09-18 |

### Dotfiles repositories (e.g., chezmoi)

| Dimension | Notes |
|---|---|
| Features | Manages personal configuration files (templating for per-machine differences, secrets integration via a password manager, encryption via age/gpg/git-crypt). Does not manage packages, services, or an AI-agent gateway. |
| Distribution | Single-command bootstrap from a user's own dotfiles git repo (`chezmoi init --apply <user>`); cross-platform (macOS, Linux, Windows). |
| Support | Community/open-source project; no commercial support tier identified. |
| Lock-in | Very low — it's your own dotfiles repo; switching tools mostly means re-templating files. |
| Security posture | Handles secrets deliberately (password manager integration, encryption support) but has no opinion on OS-level hardening, services, or an agent's access boundaries. |
| Maintenance burden | User maintains their own dotfiles content; the tool itself is low-maintenance to run. |
| Cost model | Free/open-source. |
| Source | [chezmoi.io](https://www.chezmoi.io/) | Confidence: **high** (official project site) | Date checked: 2026-09-18 |

### Ansible / NixOS-style configuration management

| Dimension | Notes |
|---|---|
| Features | Full configuration-management/orchestration: provisioning, app deployment, multi-host orchestration (Ansible); or fully declarative, reproducible whole-system state (NixOS, via `/etc/nixos/configuration.nix` and `nixos-rebuild switch`). Both are far more general-purpose than OMES. |
| Distribution | Ansible: installed as a control-node tool, agentless over SSH. NixOS: a full Linux distribution built around the Nix package manager and declarative configuration. |
| Support | Ansible Core is community-supported and free; Red Hat sells **Ansible Automation Platform**, which bundles a dozen-plus upstream projects and adds enterprise security hardening, event-driven automation/generative-AI features, and end-to-end Red Hat technical support (vs. community support for the open-source core). NixOS: community-driven (wiki, manual), no known commercial support tier equivalent to Red Hat's. |
| Lock-in | Ansible: low at the OS level (works on top of most Linux distros); learning-curve lock-in (playbook/YAML expertise) is real. NixOS: high — adopting it means adopting the Nix language and a different distro entirely. |
| Security posture | Both are neutral infrastructure; security posture is whatever the operator's playbooks/configuration declare — no built-in agent-specific hardening for something like a Hermes gateway. |
| Maintenance burden | Requires configuration-management or Nix-language expertise most solo devs/small teams (OMES's priority ICPs) do not have on staff — this is the gap OMES's simpler, opinionated module contract targets. |
| Cost model | Ansible Core: free. Ansible Automation Platform: paid, enterprise-priced (no figure restated here — not verified in this research pass). NixOS: free. |
| Source | [redhat.com/en/ansible-collaborative](https://www.redhat.com/en/ansible-collaborative) (Ansible); [nixos.org](https://nixos.org/) and [nixos.org/manual/nixos/stable/](https://nixos.org/manual/nixos/stable/) (NixOS) | Confidence: **high** for Ansible's OSS/paid split (vendor's own page); **medium** for NixOS support-model claims (wiki/manual describe the tool, not a support-market analysis) | Date checked: 2026-09-18 |

### Managed AI-agent platforms / hosted assistants

| Dimension | Notes |
|---|---|
| Features | Typically a polished UI, a hosted runtime, and vendor-managed model/tool access — no local install, no server ops needed. |
| Distribution | SaaS — the customer never touches the underlying host. |
| Support | Vendor SLA, typically tiered by paid plan. |
| Lock-in | High — data, workflows, and integrations live inside the vendor's platform; migrating out is the vendor's own portability story, not something OMES has evaluated. |
| Security posture | Data leaves the customer's own infrastructure by design; posture depends entirely on the specific vendor's certifications and practices. |
| Maintenance burden | Near-zero for the customer; entirely the vendor's responsibility. |
| Cost model | Recurring subscription/usage-based billing, set by the vendor. |
| Source | No specific vendor was named in this research pass (category-level claim only); category behavior is a general, well-known SaaS pattern, not sourced to one company. | Confidence: **low** (no named source; general market knowledge) | Date checked: 2026-09-18 |

This category is included because it's the credible alternative for a buyer who wants "an AI assistant" without wanting to run anything themselves — the opposite trade-off from OMES.

### DIY Hermes install (no OMES)

| Dimension | Notes |
|---|---|
| Features | Whatever Hermes itself provides: Linux installer, `hermes setup`, `hermes doctor`, `HERMES_HOME` profiles, `config.yaml` + `.env` secret separation, a messaging gateway (Telegram, with `TELEGRAM_ALLOWED_USERS`, `TELEGRAM_GROUP_ALLOWED_USERS`, `TELEGRAM_GROUP_ALLOWED_CHATS`, and a guest mode for non-allowlisted groups reachable only via explicit mention). |
| Distribution | Self-hosted only, per Hermes's own Telegram documentation — there is no managed/hosted Hermes service; the operator runs the installer and gateway on their own infrastructure. |
| Support | Nous Research's own documentation and community channels; no OMES-specific support layer without OMES. |
| Lock-in | Low to Hermes itself (it's the operator's own host and config); the "lock-in" risk here is operational — misconfiguration risk, not vendor lock-in. |
| Security posture | Hermes documents allowlist and secret-separation primitives, but applying them correctly (e.g., remembering that Telegram allowlists are read only at gateway start, requiring a restart after edits) is left entirely to the operator. |
| Maintenance burden | Full burden on the operator: PATH configuration for the gateway service (Node, ffmpeg, launchers), systemd unit management, lingering for headless user services, and correct allowlist maintenance. |
| Cost model | Free (Hermes itself); the "cost" is operator time and the risk of a misconfiguration incident. |
| Source | [hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram) | Confidence: **high** (direct fetch of the vendor's own docs) | Date checked: 2026-09-18 |

## Substitutes

Beyond direct comparisons, a buyer with this problem might instead:

- Do nothing and keep re-provisioning manually (the default "alternative" for most of the ICPs in [icp-and-customer-discovery.md](icp-and-customer-discovery.md)).
- Hire a contractor/MSP to set up and maintain servers and workstations (shifts the labor, doesn't remove it).
- Standardize on a single managed cloud IDE/dev-environment product (e.g., a cloud workspace) that sidesteps local OS setup entirely — different trade-off, no local Hermes/server ops story.
- Buy pre-configured hardware/VPS images from a hosting provider that bundles a base stack — narrower scope than OMES (no Hermes integration, no Omarchy-inspired workflow).

## Why someone would NOT choose OMES

Stated honestly, per the hard rule against overselling:

- **They're happy with vanilla Ubuntu/Mint and don't want the workflow layer at all.** OMES adds a dependency and a thing to trust; if the current manual process isn't actually painful for them, OMES is net overhead.
- **They want the real Omarchy experience (Arch/Hyprland/Quickshell) and can migrate.** OMES explicitly does not attempt to replicate Arch-level package freshness or Hyprland's full integration on Ubuntu/Mint — someone who wants that should run actual Omarchy.
- **They already have a mature Ansible/NixOS/Terraform pipeline.** OMES is not a fleet-scale configuration-management replacement; teams with working CM at scale have little to gain and a new tool to evaluate for no clear benefit.
- **They need only personal dotfiles, not system/package/service/agent setup.** A dedicated dotfiles tool (chezmoi) is simpler and more portable for that narrower need.
- **They want a fully managed, zero-ops AI assistant.** OMES (and Hermes itself) requires the customer to run infrastructure; a hosted platform removes that burden entirely, at the cost of data locality and recurring fees.
- **They cannot accept any third-party installer touching production hosts**, e.g., due to a compliance or change-control policy that requires all provisioning to go through an already-approved pipeline.
- **OMES's compatibility matrix doesn't cover their exact OS/version/hardware** — per the engineering brief, unsupported platforms exit early (code 3) rather than attempting a best-effort install; that's a deliberate safety choice, but it does mean some hosts are out of scope entirely.

## Assumption register (competitive-landscape specific)

| ID | Assumption | Validation method | Status |
|---|---|---|---|
| A-21-01 | Omakub's retirement (folded into Arch-based Omarchy) means most "Ubuntu setup" comparisons should target Omabuntu or vanilla Ubuntu instead | Re-check omarchy.org/omakub and github.com/omacom/omakub periodically; re-date this table before any external publication | Open (dated fact, may change) |
| A-21-02 | Buyers evaluating "managed AI-agent platforms" as a substitute are a real, sizeable share of ahliweb's addressable interviews | Ask directly in discovery interviews (icp-and-customer-discovery.md question 6–7); tag responses that name a specific hosted-assistant product | Open |
| A-21-03 | Teams with mature Ansible/NixOS pipelines are a disqualifier rather than an upsell opportunity | Interview any agency/SME with existing CM tooling; ask what would make OMES relevant despite it | Open |

Any external-facing comparison content must re-check the sources above before reuse — this table is a snapshot dated 2026-09-18, and both Omarchy/Omakub's status and vendor support-tier details are things that change.
