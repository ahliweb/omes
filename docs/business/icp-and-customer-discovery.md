# OMES ICP and Customer Discovery

> Status: Hypothesis-stage. Nothing in this document is a confirmed market fact.
> Baseline: [docs/research-and-implementation-plan.md](../research-and-implementation-plan.md), section 5.1.
> Last updated: 2026-09-18.

## How to read this document

This document separates what OMES *knows* from what OMES *assumes*.

- **Validated** — backed by a completed interview, a pilot log, a support ticket, or a reproducible technical test. Cites its evidence.
- **Hypothesis** — a belief the team holds today. Every hypothesis that materially affects product or pricing decisions carries an `ASSUMPTION` tag and a stated validation method in the [Assumption Register](#assumption-register).

No market size, willingness-to-pay, or price point appears anywhere in this document as a fact. Where such a figure would normally go, it is written as `ASSUMPTION` with a validation method instead.

OMES is an independent, Omarchy-inspired, MIT-licensed project. It is not official Omarchy and must never be described as such. Hermes is a Nous Research product; OMES integrates with it but does not own or resell it.

---

## Validated

Nothing in the ICP space is validated yet. Issue #19 exists to produce the first validated evidence via the interview plan below; #1–#3 (scope, architecture) are implementation-track issues that inform feasibility but do not validate demand. This section will be populated with dated evidence citations (interview notes, pilot logs) as discovery proceeds. Until then, treat every claim below as Hypothesis.

---

## Hypothesis: ICP candidates

Order follows the plan's validation order (section 5.1). Each candidate lists jobs-to-be-done (JTBD), pains, current alternatives, buying trigger, and disqualifiers. All of it is `ASSUMPTION` pending interviews.

### ICP-1: ahliweb internal operators and workstations

- **Who**: ahliweb's own team members provisioning workstations and internal servers.
- **JTBD**: get a reproducible, keyboard-first dev/ops environment on Ubuntu Server or Mint without hand-rolling dotfiles each time; get Hermes running as an internal assistant/gateway.
- **Pains** (`ASSUMPTION`): re-provisioning a machine after a reinstall or a new hire takes a full day of manual setup; Hermes gateway configuration is fiddly to reproduce (Telegram allowlists, systemd units, PATH issues); no one owns "the standard workstation image."
- **Current alternatives**: ad hoc shell history, personal dotfiles repos, tribal knowledge, manual `apt install` lists.
- **Buying trigger**: a new hire or a wiped machine forces re-setup; an internal incident traces back to a misconfigured Hermes gateway.
- **Disqualifiers**: none expected — this is the lowest-friction segment because OMES controls its own adoption here first (dogfooding).
- **Why validate first**: zero acquisition cost, direct access to reality, fastest feedback loop; this is also where OMES will generate its first real unit-economics data (see business-model.md, once published).

### ICP-2: Freelancers and independent developers

- **Who**: solo developers/consultants who reinstall or provision machines periodically and want an agentic assistant without building one from scratch.
- **JTBD**: stand up a working dev environment fast on hardware they already own (often Ubuntu or Mint, not Arch); get an AI agent (Hermes) wired into their workflow without becoming a systemd/Telegram-bot expert.
- **Pains** (`ASSUMPTION`): Omarchy's workflow looks attractive but requires Arch, which they are unwilling or unable to migrate to; existing dotfiles repos solve config but not agent/gateway setup; time spent on environment setup is unbilled.
- **Current alternatives**: personal dotfiles, Omakub (if willing to run Ubuntu desktop), raw Hermes install docs, doing nothing (staying on an unstructured setup).
- **Buying trigger**: new laptop/VPS, or hearing about Omarchy/Hermes and wanting the workflow without the Arch migration.
- **Disqualifiers**: hard requirement for Arch-specific packages (AUR); no interest in AI agents at all; fully satisfied with an existing dotfiles setup.

### ICP-3: Web/digital agencies with multiple workstations or servers

- **Who**: small agencies (roughly 3–30 people) running several client servers and developer workstations.
- **JTBD**: standardize setup across many machines and clients; reduce onboarding time for new developers; offer clients a supportable, documented server baseline.
- **Pains** (`ASSUMPTION`): every client server is configured slightly differently; no dedicated DevOps hire; support tickets from inconsistent environments eat billable time.
- **Current alternatives**: Ansible/shell scripts maintained internally, managed hosting panels (e.g., cPanel-style), nothing formal (config drift).
- **Buying trigger**: onboarding a new client server, losing a key engineer who held tribal setup knowledge, a security or uptime incident traced to drift.
- **Disqualifiers**: already has a mature internal Ansible/Terraform pipeline they are happy with; exclusively manages Windows or managed-PaaS workloads with no server access.

### ICP-4: Small teams needing an internal Hermes assistant

- **Who**: teams of roughly 2–15 people (not necessarily developers) who want an internal AI assistant reachable via Telegram/chat for ops tasks, without building the integration themselves.
- **JTBD**: get a working, access-controlled Hermes gateway (Telegram allowlists, secrets handled correctly) without reading Hermes's full docs and without exposing the team to a security incident.
- **Pains** (`ASSUMPTION`): Hermes setup requires systemd, PATH, and allowlist details that are easy to get wrong (e.g., forgetting both `TELEGRAM_ALLOWED_CHATS` and `TELEGRAM_GROUP_ALLOWED_CHATS`); no internal Linux/ops expertise to safely run this unattended.
- **Current alternatives**: no assistant at all, a generic SaaS chatbot with less system access, DIY Hermes install with unknown security gaps.
- **Buying trigger**: a manual, repetitive ops task becomes painful enough to delegate; a security-conscious lead wants an audited, allowlisted setup instead of an ad hoc one.
- **Disqualifiers**: no willingness to run anything on owned infrastructure (cloud-only, no-ops mandate); data-sensitivity policy that forbids any agent with system access.

### ICP-5: Self-hosters

- **Who**: hobbyists and privacy-conscious individuals running personal servers (home lab, VPS) who value control and reversibility.
- **JTBD**: get an opinionated, keyboard-first, reversible setup on hardware/VPS they already run Ubuntu or Mint on; avoid vendor lock-in.
- **Pains** (`ASSUMPTION`): Omarchy is Arch-only, which many self-hosters running Ubuntu-family distros for stability reasons will not switch to; most "installer" projects are not reversible or auditable enough for a security-conscious hobbyist.
- **Current alternatives**: personal shell scripts, chezmoi, NixOS (for those willing to switch distros entirely), Omarchy itself (on a separate Arch box).
- **Buying trigger**: a fresh VPS/home-lab rebuild, discovering OMES via Omarchy/Hermes community channels.
- **Disqualifiers**: ideologically opposed to any bundled tooling ("I roll my own, always"); already deep into NixOS/Guix declarative tooling and unwilling to add a second paradigm.

### ICP-6: Education labs

- **Who**: university/bootcamp labs standardizing student or lab-server images on Ubuntu/Mint.
- **JTBD**: give every student/lab machine an identical, easy-to-reset baseline; teach modern terminal/AI-agent workflows without requiring Arch familiarity.
- **Pains** (`ASSUMPTION`): lab admins re-image machines often and want a fast, scriptable, reversible baseline; teaching Hyprland/Arch directly is out of scope for most curricula, but the *workflow* concepts are valuable to teach.
- **Current alternatives**: golden disk images, Ansible-provisioned lab images, doing nothing (students self-configure, causing support load).
- **Buying trigger**: start of a new term/cohort; a curriculum redesign toward AI-agent tooling.
- **Disqualifiers**: locked-down institutional imaging pipeline that cannot accept an external toolkit; strict compliance requirements OMES has not been evaluated against.

### ICP-7: Technical SMEs (small/medium enterprises)

- **Who**: small companies (roughly 10–100 people) with an internal engineering or IT function but no dedicated platform team.
- **JTBD**: apply a documented, supportable server/workstation baseline; get audit-friendly logging, backups, and an internal assistant without hiring a platform engineer.
- **Pains** (`ASSUMPTION`): IT function is generalist, not specialist; compliance/audit pressure exists but tooling budget is limited; "shadow AI" adoption (staff using unmanaged AI tools) is already a risk.
- **Current alternatives**: MSP contracts, ad hoc IT consultant engagements, unmanaged individual tool adoption.
- **Buying trigger**: an audit finding, a new compliance requirement, or a security incident caused by unmanaged tooling.
- **Disqualifiers**: already has a contracted MSP/platform team fully covering this; regulatory environment requiring certifications OMES does not hold (e.g., specific government accreditations).

---

## Prioritization scoring rubric

Score each ICP 1 (low) to 5 (high) on each dimension. This rubric is a planning tool, not a validated model — the weights are `ASSUMPTION` and should be revisited after the first 10 interviews.

| Dimension | What it measures | Weight |
|---|---|---|
| Reach | How many ahliweb can plausibly contact in 90 days | 2 |
| Pain intensity (claimed) | How strongly the interviewee describes the pain, unprompted | 3 |
| Access cost | Inverse of how hard/expensive it is to reach this segment (5 = cheap/easy) | 2 |
| Willingness-to-engage signal | Would they take a follow-up call / pilot, not just answer a survey | 3 |
| Strategic fit | Alignment with ahliweb's own Hermes/Ubuntu/Mint operating reality | 1 |

`Priority score = Σ(dimension score × weight)`. Recompute after each batch of interviews; do not treat the initial scores (not shown here, since none are validated yet) as final.

---

## Interview plan

**Target**: 10–15 interviews across segments before drafting positioning claims that depend on customer language (feeds issue #20).

**Segment allocation** (`ASSUMPTION`, adjust based on actual access):

| Segment | Target interviews |
|---|---|
| ICP-1 (ahliweb internal) | 2–3 (fastest, do first) |
| ICP-2 (freelancers/developers) | 3–4 |
| ICP-3 (agencies) | 2–3 |
| ICP-4 (small teams / Hermes assistant) | 2–3 |
| ICP-5 (self-hosters) | 1–2 |
| ICP-6 (education labs) | 1 (stretch) |
| ICP-7 (technical SMEs) | 1–2 |

**Recruiting channels** (`ASSUMPTION` — validate which actually convert): ahliweb's own network, Omarchy/Hermes community forums and Discord/Telegram channels, Indonesian developer communities, self-hosting and homelab forums, local agency contacts.

**Interview format**: 30–45 minutes, one interviewer + one note-taker where possible, recorded with consent, synthesized into the [evidence capture template](#evidence-capture-template) within 24 hours.

**Non-goals during discovery**: do not pitch OMES, do not ask about price, do not ask leading questions ("would you pay for X?"). The goal is to learn how the interviewee solves this problem *today* and how painful that is — not to validate a pre-built solution.

---

## Interview guide

Use open, non-leading questions. Avoid naming OMES features until the closing section.

### Warm-up

1. Tell me about the last time you set up a new Linux machine (workstation or server) — walk me through what you actually did.
2. How long did that take, start to finish?

### Problem exploration

3. What was the most annoying or time-consuming part of that process?
4. Have you tried to make that process repeatable (scripts, images, dotfiles, configuration management)? What did you build or use?
5. Where does that repeatable setup break down or need manual fixing?
6. Do you or your team use any AI agents/assistants for dev or ops work today? Which ones, and how did you set them up?
7. If you use (or tried) Hermes, Omarchy, or Omakub specifically — what worked, what didn't, what confused you?
8. Tell me about a time this setup process caused a real problem (lost time, an incident, a client complaint).

### Alternatives and workarounds

9. If this problem got bad enough, what would you do about it? Who would you ask, what would you search for?
10. What have you already tried that didn't work or that you abandoned? Why?

### Closing (only after problem exploration is exhausted)

11. If a tool existed that did [restate their own words for the pain back to them], how would that change what you do?
12. Who else on your team or in your network deals with this same problem?
13. Would you be open to a short follow-up when we have something to try?

**Interviewer notes**: never ask "would you pay $X for this" during discovery — that number would become an invented data point. If pricing comes up organically, record it verbatim in the evidence template as a quote, not as a validated willingness-to-pay figure.

---

## Evidence capture template

One row per interview. Store as a table (spreadsheet or `docs/business/` appendix — not yet created; track in the assumption register below).

| Field | Description |
|---|---|
| Interview ID | e.g., `INT-2026-09-001` |
| Date | ISO date |
| Segment (ICP-N) | Which candidate this person maps to |
| Interviewee role | e.g., "solo freelance developer", "agency ops lead" |
| Recruiting channel | Where they came from |
| Current setup process (verbatim/paraphrase) | What they actually do today |
| Time spent (claimed) | As stated by interviewee, marked "claimed" not measured |
| Pain quotes | Direct quotes describing frustration, with timestamp if recorded |
| Tools/alternatives named | Dotfiles, Ansible, Omakub, Hermes, etc. |
| Hermes/Omarchy familiarity | None / heard of it / tried it / uses it |
| Unprompted pricing signal (if any) | Verbatim quote only, never converted into a number we treat as fact |
| Disqualifier flags | Any disqualifier from the ICP definitions that applied |
| Follow-up interest | Yes/No/Maybe, and what they agreed to |
| Interviewer | Name |
| Notes link | Link to raw notes/recording |

---

## Assumption register

Every `ASSUMPTION` above is tracked here with its validation method. Update status as evidence arrives.

| ID | Assumption | Validation method | Status |
|---|---|---|---|
| A-19-01 | Manual Linux re-provisioning is a meaningfully painful, recurring task for ICP-1 through ICP-7 | Interview questions 1–2, 8; time-to-provision measured in pilots | Open |
| A-19-02 | Freelancers/agencies want Omarchy-style workflow but will not migrate to Arch | Interview question 7; count of interviewees citing Arch as a blocker | Open |
| A-19-03 | Hermes gateway/Telegram setup (allowlists, systemd, PATH) is a common source of friction or misconfiguration | Interview question 7; count of Hermes-specific pain quotes; support tickets once pilots start | Open |
| A-19-04 | Small teams want an internal AI assistant but lack the ops skill to run one safely | Interview question 6–7 for ICP-4; pilot onboarding time | Open |
| A-19-05 | Self-hosters value reversibility/auditability enough to adopt a third-party installer | Interview questions 4–5, 9 for ICP-5 | Open |
| A-19-06 | Education labs and technical SMEs are reachable within the 90-day window via ahliweb's existing network | Track actual interviews scheduled vs. target allocation table above | Open |
| A-19-07 | Prioritization rubric weights (Reach 2, Pain 3, Access 2, Willingness 3, Fit 1) reflect what should actually drive segment focus | Re-score after 10 interviews; compare predicted vs. actual willingness-to-engage | Open |
| A-19-08 | 10–15 interviews across these segment allocations is sufficient to inform positioning (#20) | Track interview count and diversity of segments reached before #20 work starts | Open |

No item in this register may be promoted to "Validated" without a cited interview ID, pilot log entry, or reproducible technical test linked in this document or its successor.
