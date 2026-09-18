# OMES Business Model

> Status: Hypothesis-stage. No price point, market size, or willingness-to-pay figure in this document is a fact — every number is either a formula, a labor-hour-linked range, or explicitly marked `ASSUMPTION`.
> Baseline: [docs/research-and-implementation-plan.md](../research-and-implementation-plan.md), sections 5.3–5.5; builds on [icp-and-customer-discovery.md](icp-and-customer-discovery.md), [positioning.md](positioning.md), and [competitive-landscape.md](competitive-landscape.md).
> Last updated: 2026-09-18.

OMES is an independent, Omarchy-inspired, MIT-licensed project. It is never described as official Omarchy. Hermes Agent is a Nous Research product; OMES integrates with it and does not own, resell, or take a cut of Hermes itself.

## Validated

Nothing here is validated yet — no pilot has run, no invoice has been sent, no support ticket has been logged. This section exists to be filled in as pilots produce actual labor-hour and cost data (see [icp-and-customer-discovery.md](icp-and-customer-discovery.md)'s evidence template and the forthcoming `unit-economics.md`, which is the authoritative place for measured numbers once pilots run). Until then, every model below is Hypothesis with a pricing *logic*, not a pricing *figure*.

## How to read the pricing-logic sections

Per the plan (section 5.4) and the hard rule against inventing numbers: candidate pricing below is expressed as **formulas and ranges tied to labor hours**, never as a specific dollar or rupiah figure. Any variable named `<hourly_rate>` is deliberately left unset — it is a business decision to be made later (informed by ahliweb's own cost structure and, eventually, discovery evidence on willingness to pay), not something this document invents.

---

## Business models evaluated

### 1. Open-core

- **Customer value**: the installer, profiles, module contract, docs, and tests are free and inspectable — this is what earns trust before anyone pays for anything (per [positioning.md](positioning.md)'s "reversible, respects the host platform" claim).
- **Delivery cost drivers**: ongoing maintenance of the compatibility matrix (Ubuntu/Mint versions, Hyprland packaging drift per the plan's section 2.2), CI/lint/test upkeep, community issue triage.
- **Risks**: maintenance cost grows with the support matrix (plan section 5.5: "Ubuntu/Mint/Hyprland maintenance matrix becomes too expensive"); free tier cannibalizes paid interest if it already fully solves the customer's problem; project abandonment risk if maintenance isn't funded by anything downstream.
- **Candidate pricing logic**: no direct price — this is the top-of-funnel free layer. Its "price" is measured in maintenance hours per release cycle, which should be tracked against the paid models below to check it isn't a net cost sink with no funnel effect.
- **Dependencies**: requires the core installer (issues in the engineering track, #1 onward) to actually be reliable enough to be worth giving away; requires a compatibility matrix (tracked separately) to keep maintenance bounded.

### 2. Paid setup and migration (one-time service)

- **Customer value**: a working, verified OMES install (server and/or Hermes profile) on the customer's own infrastructure without the customer's team spending the time; migration from an existing manual setup to an OMES-managed one with a documented rollback if it doesn't work out.
- **Delivery cost drivers**: labor hours per engagement (discovery call, preflight/compatibility check against the customer's actual hardware/OS, install, verification, handoff/documentation); travel or remote-access setup if applicable; any custom module work required for a non-standard environment.
- **Risks**: scope creep on "migration" engagements (existing config is rarely clean); a bad install on a production host damages trust disproportionately (mitigated by OMES's backup/rollback contract, but only if actually exercised and tested first); low repeatability if every customer's starting state is different enough to require bespoke work each time.
- **Candidate pricing logic**:
  ```text
  setup_price = (estimated_labor_hours × <hourly_rate>) + fixed_overhead
  estimated_labor_hours = discovery_hours + install_hours + verification_hours + handoff_hours
  ```
  `estimated_labor_hours` should be measured per pilot (see [icp-and-customer-discovery.md](icp-and-customer-discovery.md) evidence template) before any range is quoted externally. Complexity multipliers (e.g., non-standard hardware, multiple profiles) should be tracked as observed multipliers on `estimated_labor_hours`, not invented up front.
- **Dependencies**: depends on the Starter/Team setup service package definitions below; depends on a working, tested preflight/rollback path (engineering track) so the delivery risk is actually bounded.

### 3. Managed deployment (ongoing, OMES operates the install)

- **Customer value**: the customer never touches the Linux box; OMES (or ahliweb) keeps it updated, monitored, and healthy.
- **Delivery cost drivers**: per-deployment operational time (monitoring, patching, incident response), infrastructure costs if OMES-side tooling is involved, on-call coverage.
- **Risks**: this is the closest model to "hosting," which the plan explicitly recommends **against** starting with (section 5.3: "do not start with SaaS or hosting"); it concentrates operational risk and liability on ahliweb for infrastructure it doesn't own; support burden scales roughly linearly with deployment count unless heavily automated, which is unproven at this stage.
- **Candidate pricing logic**:
  ```text
  monthly_price = (expected_monthly_support_hours × <hourly_rate>) + infrastructure_pass_through + margin
  support_burden = support_hours / active_deployment / month   (per the plan's section 5.4 formula)
  ```
  `expected_monthly_support_hours` is unknown until measured across at least a few deployments; do not price this before that data exists.
- **Dependencies**: depends on the support-subscription model below (managed deployment is effectively support-subscription-plus-operational-access); depends on monitoring/alerting tooling that does not yet exist in the engineering track.
- **Recommendation**: **do not pursue as an MVP model** — consistent with the plan's explicit guidance. Revisit only after setup/support models have produced real unit-economics data.

### 4. Support subscription

- **Customer value**: updates, backups, incident response, and troubleshooting for an OMES install the customer already has (self-run or OMES-installed), without the customer needing in-house Linux/Hermes expertise.
- **Delivery cost drivers**: support hours per active deployment per month (the plan's own tracked metric); ticket volume and severity mix; whether backups/rollback are pre-verified (reduces incident-response time) or ad hoc (increases it).
- **Risks**: support cost is invisible until measured — a subscription priced before any pilot data risks under- or over-pricing; "unlimited support" framing invites scope creep; churn risk if the underlying OMES install is unstable enough to generate frequent tickets (a product-quality problem, not a pricing problem).
- **Candidate pricing logic**:
  ```text
  monthly_price = (expected_support_hours_per_month × <hourly_rate>) + margin
  gross_contribution = revenue - delivery_labor - infrastructure - variable_support_cost   (per plan section 5.4)
  ```
  `expected_support_hours_per_month` must come from the Ops retainer package's actual pilot usage, not an estimate made now.
- **Dependencies**: depends on the Ops retainer service package (below); depends on unit-economics tracking (parallel `unit-economics.md`) actually being instrumented during pilots.

### 5. Business hardening (paid, project-based or subscription add-on)

- **Customer value**: allowlists, isolated profiles, logging, backups, and policy configuration for a business that needs to trust an agent (Hermes) with real operational access — directly addresses ICP-4 and ICP-7's pains in [icp-and-customer-discovery.md](icp-and-customer-discovery.md).
- **Delivery cost drivers**: labor to review the customer's actual Telegram/Hermes access model, configure allowlists correctly (per the engineering brief's Telegram allowlist rules — both `TELEGRAM_ALLOWED_CHATS` and `TELEGRAM_GROUP_ALLOWED_CHATS` must be set, allowlists are read only at gateway start), verify logging/backups, and document the resulting policy for the customer's own audit needs.
- **Risks**: a hardening engagement that misses a real gap creates outsized reputational and liability risk if a customer later has an incident; scope is easy to under-specify ("harden it" means different things to different customers) without a clear checklist.
- **Candidate pricing logic**: same labor-hour formula as paid setup, scoped to a hardening checklist rather than a full install:
  ```text
  hardening_price = (checklist_review_hours + remediation_hours + verification_hours) × <hourly_rate>
  ```
- **Dependencies**: depends on a documented hardening checklist (not yet written — candidate follow-up for the engineering/security track, referenced in the plan's section 2.4); depends on the "Hardened Hermes" service package below.

### 6. Training and workshops

- **Customer value**: teaches a small team's own staff to run Linux/AI-agent operations themselves (Omarchy-inspired keyboard-first workflow, Hermes basics, OMES's check/apply/verify/rollback model) — relevant to ICP-6 (education labs) and ICP-3/ICP-7 (agencies/SMEs building internal capability).
- **Delivery cost drivers**: instructor prep and delivery time; whether delivered live vs. as reusable recorded/async material (async has near-zero marginal delivery cost per additional attendee once built, but higher upfront authoring cost).
- **Risks**: content goes stale as the compatibility matrix and Hermes itself evolve, requiring recurring update effort; low differentiation risk if the same material is available for free in OMES's own docs (workshops should sell facilitation/practice/Q&A, not information that's already public).
- **Candidate pricing logic**:
  ```text
  workshop_price = (prep_hours + delivery_hours) × <hourly_rate> / expected_attendee_count
  ```
  For async/recorded material, amortize `prep_hours` across an estimated attendee count rather than pricing per live session.
- **Dependencies**: depends on stable-enough documentation (engineering track docs/*.md) to teach from without constant rewrites.

### 7. Custom integrations

- **Customer value**: connecting Hermes/OMES to a customer's specific stack (internal tools, provider routing, GitHub, Docker workflows) beyond what ships in the core modules.
- **Delivery cost drivers**: bespoke development time per integration; ongoing maintenance if the integration must track upstream API changes (this is a recurring cost driver, not a one-time one).
- **Risks**: bespoke work doesn't scale — each integration is closer to consulting than product; maintenance burden accumulates silently unless each integration has an explicit support/versioning commitment attached.
- **Candidate pricing logic**:
  ```text
  integration_price = (design_hours + build_hours + test_hours) × <hourly_rate>
  ongoing_maintenance_price = expected_annual_maintenance_hours × <hourly_rate>   (quoted separately, not bundled silently)
  ```
- **Dependencies**: depends on the module contract (engineering track — `MODULE_REQUIRES`, `check`/`apply`/`verify`/`rollback`) being stable enough that a custom integration module doesn't need to be rewritten on every core change.

---

## Service package definitions (scope, not price)

These are candidate packages for the paid-setup and hardening models above. Scope only — no price is attached here; pricing is derived per engagement using the formulas above once labor-hour data exists.

### Starter setup

- **Scope**: single Ubuntu Server or Mint host, one profile (`server`, `desktop`, or `hermes`), default module set, `hermes doctor` passing at handoff, one documented backup taken before any change, a short handoff walkthrough.
- **Excludes**: custom modules, multi-host rollout, Telegram gateway hardening beyond documented defaults, ongoing support (see Ops retainer).
- **Good fit**: ICP-1 (internal), ICP-2 (freelancers), ICP-5 (self-hosters) doing a single-machine setup.

### Team setup

- **Scope**: multiple hosts (workstations and/or servers) for one team/agency, consistent profile application across hosts, a written summary of what was installed and where state/backups live per host, a short knowledge-transfer session for the team's own future re-runs.
- **Excludes**: per-host bespoke customization beyond the standard module set (quoted separately if needed); ongoing support (see Ops retainer).
- **Good fit**: ICP-3 (agencies), ICP-4 (small teams standardizing multiple machines).

### Hardened Hermes

- **Scope**: Hermes gateway setup with a reviewed Telegram allowlist configuration (DM and group allowlists both set correctly), secrets stored per the `.env` boundary (0600, never logged), systemd service with explicit PATH, `hermes doctor` verification, and a written summary of what access the agent has and how to revoke it.
- **Excludes**: a full security audit of the customer's broader infrastructure (out of scope — this package covers the Hermes/OMES-managed surface only); compliance certification of any kind.
- **Good fit**: ICP-4 (small teams needing a trustworthy internal assistant), ICP-7 (technical SMEs under audit/compliance pressure).

### Ops retainer

- **Scope**: a recurring (e.g., monthly) commitment covering update application, backup verification, incident response for OMES-managed paths, and a bounded number of support requests per period; explicitly does not include unlimited scope-creep support.
- **Excludes**: managed deployment (OMES does not take over day-to-day operation of infrastructure it doesn't own — see the "Managed deployment" evaluation above, which this project recommends against for the MVP); new-feature custom integration work (quoted separately per the Custom integrations model).
- **Good fit**: any ICP once a Starter/Team/Hardened Hermes setup has been delivered and the customer wants ongoing coverage rather than being on their own.

---

## MVP monetization recommendation

Consistent with the plan (section 5.3): **do not start with SaaS or hosting.** Use OMES as a **productized-service delivery engine** first:

1. Offer **Starter setup** and **Team setup** as the first paid engagements — they have the clearest scope, the shortest feedback loop, and the least operational risk (no ongoing liability for infrastructure ahliweb doesn't own).
2. Instrument every engagement using the plan's tracked metrics (section 5.4): preflight time, installation time, troubleshooting time, manual interventions, rollback count, Hermes onboarding time, monthly maintenance time, and willingness-to-pay signals/objections (captured as quotes, per [icp-and-customer-discovery.md](icp-and-customer-discovery.md)'s evidence template — never converted into an invented number).
3. Only after several engagements produce real labor-hour data, evaluate **Hardened Hermes** and **Ops retainer** as the next layer — both depend on having a track record of what setup and support actually cost in hours.
4. Treat **Managed deployment** as explicitly out of scope for the MVP; revisit only if Ops retainer data suggests customers want OMES to take over operational access entirely, and only with a clear liability and infrastructure-ownership model worked out first.
5. Treat **Training/workshops** and **Custom integrations** as opportunistic, not primary — pursue them when a specific customer asks, not as a planned product line, until there's evidence of repeat demand.

### Kill criteria

Stop or substantially revise the productized-service approach if, after a defined batch of pilots (`ASSUMPTION`: e.g., the first 5 paid engagements — the exact count is a business decision, not asserted here as fact):

- `gross contribution` (per the plan's formula: `revenue - delivery labor - infrastructure - variable support cost`) is negative across multiple engagements with no clear path to positive as delivery time drops with practice.
- `support burden` (`support hours / active deployment / month`) is trending up rather than down as the compatibility matrix and documentation mature — a sign the product itself, not the go-to-market, is the problem.
- Customers consistently object to the service model itself (not the price) — e.g., they want a self-serve product with no engagement, which would indicate the open-core + self-serve docs path should be prioritized over services instead.
- The Ubuntu/Mint/Hyprland compatibility matrix (plan section 5.5) proves too expensive to maintain relative to the paid engagements it supports — i.e., maintenance cost is outpacing what services revenue can plausibly fund.
- Repeated installer incidents damage a customer's existing host (plan section 5.5) — this is a hard stop requiring an engineering-side root-cause fix before any further paid engagements, regardless of commercial pressure to continue.

## Assumption register (business-model specific)

| ID | Assumption | Validation method | Status |
|---|---|---|---|
| A-22-01 | Starter/Team setup engagements can be delivered profitably at a labor-hour cost low enough to be viable once `<hourly_rate>` is set | Track `estimated_labor_hours` vs. actual hours per pilot engagement | Open |
| A-22-02 | Support burden decreases over time as the compatibility matrix and docs mature, rather than staying flat or growing | Track `support_hours / active_deployment / month` across successive months | Open |
| A-22-03 | Customers will accept a bounded-scope Ops retainer rather than expecting unlimited support | Direct customer feedback during retainer pilots; track scope-creep requests | Open |
| A-22-04 | Managed deployment demand exists but is correctly deferred past MVP | Ask about it directly in post-engagement follow-ups; do not build it until asked for repeatedly | Open |
| A-22-05 | The first 5 paid engagements (or whatever count is chosen) is a large enough sample to evaluate kill criteria fairly | Revisit sample size after the first 2–3 engagements; adjust if labor-hour variance is too high to draw conclusions | Open |
