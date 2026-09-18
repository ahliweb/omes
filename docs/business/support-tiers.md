# OMES support tiers

Status: draft, pre-pilot. Response-time language in this document is deliberately non-binding
("target", "aim", "best-effort") — **no tier in this document carries a legally binding SLA for
the MVP stage.** Operational cost figures (hours/month) are ASSUMPTION values pending pilot
data from `docs/business/pilot-program.md`, and feed directly into
`docs/business/unit-economics.md`'s `support_hours_per_deployment_per_month` and
`infra_cost_usd_per_month` inputs.

OMES is an independent, MIT-licensed, Omarchy-inspired compatibility layer and deployment
toolkit for Ubuntu Server 24.04 LTS and Linux Mint 22.x, with Hermes Agent as the automation
layer. It is not the official Omarchy project, and no tier below implies otherwise.

## 1. Tier summary

| Tier | Who it is for | Paid? | Approx. operational cost (ASSUMPTION, hours/month) |
|---|---|---|---|
| Community | Anyone; self-service users | No | ~0 |
| Assisted | Individuals/small setups wanting install/config help | Yes | 0.5 per deployment |
| Professional | Users wanting guided troubleshooting and priority handling | Yes | 1.5 per deployment |
| Business | Small teams/agencies with multiple deployments | Yes | 4 per account (multi-deployment) |
| Enterprise | Organizations needing custom terms | Yes, negotiated | Not yet modeled — see section 2.5 |

All hours figures use `hourly_labor_cost_usd` from `docs/business/unit-economics.md` (base
scenario: 20 USD/hour) to translate into cost; they are not committed capacity, they are
planning assumptions to be replaced with pilot-measured `support_hours_per_deployment_per_month`.

## 2. Tier definitions

### 2.1 Community

- **Scope**: self-service installation and use of OMES using the published documentation
  (`docs/*.md`), `omes check`/`omes doctor` output, and public GitHub Issues/Discussions.
  No dedicated operator time is allocated per Community user.
- **Channels**: GitHub Issues, GitHub Discussions, documentation.
- **Response expectations**: best-effort only. There is no committed response time and no
  business-hours coverage promised at this tier. Issues are triaged opportunistically.
- **Exclusions**: no one-to-one setup help, no remote/screen-share sessions, no phone or chat,
  no guaranteed bug-fix timeline, no priority triage.
- **Operational cost assumption**: ~0 hours/month/deployment. This is consistent with
  `docs/business/unit-economics.md`'s design choice that non-subscribed (Community-tier)
  deployments carry no OMES-side recurring infrastructure or support cost.
- **Escalation**: security vulnerabilities reported by a Community user are handled per
  section 4 regardless of tier; everything else stays in the public issue tracker.

### 2.2 Assisted

- **Scope**: install and configuration help for the supported profiles (`server`, `desktop`,
  `hermes`) on the supported platform matrix; interpreting `omes check`/`omes doctor`/`omes
  status` output; help resolving a failed module named by its exit code (6 or 7).
- **Channels**: ticket/email, during business hours (operator's local business hours, stated
  at signup).
- **Response expectations**: aim for a first response within 2 business days. This is a
  target, not a guarantee and not an SLA.
- **Exclusions**: no custom code changes or module development, no after-hours or emergency
  response, no phone support, no on-site work.
- **Operational cost assumption (ASSUMPTION)**: 0.5 hours/deployment/month, matching
  `docs/business/unit-economics.md`'s base-scenario `support_hours_per_deployment_per_month`.
  Update both documents together once pilot data exists (see that document's section 6).
- **Escalation**: if an issue is not resolved after two Assisted-tier exchanges, the operator
  flags it for a Professional-tier-style guided session even if the customer has not upgraded,
  rather than looping indefinitely over ticket/email.

### 2.3 Professional

- **Scope**: everything in Assisted, plus a scheduled guided-troubleshooting session (remote
  screen-share or terminal session with the customer driving), and minor configuration
  changes within already-supported modules (e.g., adjusting a supported module's documented
  options — not writing new modules).
- **Channels**: ticket/email plus one scheduled call per issue, during business hours.
- **Response expectations**: aim for a first response within 1 business day. Target, not an
  SLA.
- **Exclusions**: no custom module development, no support for unsupported platforms (section
  6), no 24/7 or on-call coverage.
- **Operational cost assumption (ASSUMPTION)**: 1.5 hours/deployment/month.
- **Escalation**: any issue that touches secrets, tokens, or suspected unauthorized access is
  handled under section 4 (security-sensitive support), never as an ordinary Professional
  ticket, even if it was opened as one.

### 2.4 Business

- **Scope**: everything in Professional, plus support scoped to an account with multiple
  deployments, a named point of contact, and a quarterly review of the account's `omes status`
  health across its deployments. Reported bugs from Business-tier accounts get priority
  triage labeling internally (not a guaranteed fix time).
- **Channels**: ticket/email plus scheduled calls, named point of contact, during business
  hours.
- **Response expectations**: aim for a first response within 1 business day, and same-business-
  day acknowledgment (not resolution) for a reported full outage of an OMES-managed service.
  Target language only; no uptime or resolution-time guarantee is made.
- **Exclusions**: no legally binding uptime commitment, no after-hours on-call, no support for
  deployments outside the account's registered list.
- **Operational cost assumption (ASSUMPTION)**: 4 hours/month per account, independent of how
  many deployments are registered under it up to a small number (assume up to 5 deployments
  per account at this rate; re-baseline once real accounts exist).
- **Escalation**: outage-class reports go directly to the owner/operator (ahliweb) the same
  business day; security-sensitive reports go to section 4 regardless of channel.

### 2.5 Enterprise

- **Scope**: not yet a standard product. Enterprise-tier engagements require a custom,
  individually reviewed agreement (scope, response expectations, and any commitments beyond
  "target/best-effort" language must be negotiated and documented outside this file, e.g. in a
  signed contract) before any Enterprise-tier terms are sold.
- **Channels**: negotiated per agreement.
- **Response expectations**: negotiated per agreement; this document does not pre-commit to
  any Enterprise response time, because doing so without a reviewed contract would create an
  unintended binding commitment.
- **Exclusions**: the "never supported" list in section 6 applies regardless of tier, including
  Enterprise, unless a future signed agreement explicitly and separately extends it (tracked
  as a decision outside this document).
- **Operational cost assumption**: not modeled — ASSUMPTION placeholder only. Model per deal
  using `docs/business/unit-economics.md`'s formulas before quoting a price.
- **Escalation**: owner-level only (ahliweb); no Enterprise ticket is handled by anyone else
  without the owner's sign-off while OMES is at MVP stage.

## 3. Cross-tier operational cost note

The hours/month figures above are ASSUMPTION values for planning, not commitments to
customers. They must be reconciled against `docs/business/pilot-program.md`'s instrumentation
(operator worksheet time-spent column) and folded back into
`docs/business/unit-economics.md`'s `support_hours_per_deployment_per_month` input using that
document's section 6 update procedure. Do not let this document and the unit-economics model
drift apart — a change to one tier's assumed hours should trigger a review of the other
document's base/downside/stress scenarios.

## 4. Security-sensitive support

This section is intentionally separated from sections 2 and 3: it covers handling of secrets,
suspected compromise, and access boundaries, which are never treated as an ordinary support
ticket regardless of the customer's tier.

### 4.1 Incident handling

1. **Contain first.** If a secret leak, unauthorized Telegram access, or unexpected privilege
   use is suspected, the operator's first action is to stop the affected service (e.g.
   `hermes gateway stop`, or disabling the affected module) before investigating further, to
   limit ongoing exposure.
2. **Assess scope.** Identify which secrets/tokens and which hosts/deployments are
   potentially affected. Use `omes status`/state file records to identify exactly which
   managed paths and modules are involved — do not guess beyond what is recorded.
3. **Rotate.** See section 4.2.
4. **Notify.** Inform the affected participant/customer of what happened, what was rotated,
   and what they should independently verify, in plain language, as soon as containment and
   rotation are complete.
5. **Document.** Record a timeline (detection time, containment time, rotation time,
   notification time) and retain it per section 4.5.

### 4.2 Secret rotation

- Rotate any secret that may have been exposed: `TELEGRAM_BOT_TOKEN`, any value in
  `$HERMES_HOME/.env`, and SSH keys if host access is implicated.
- Never rotate or share a secret over an unencrypted channel (plain email, unencrypted chat).
- Never paste a live secret value into a support ticket, chat log, or this repository. If a
  secret must be referenced, reference it by name/purpose only (e.g. "the Telegram bot token
  for deployment X"), never by value.
- After rotation, confirm the old value no longer works (e.g., old bot token rejected) before
  closing the incident.

### 4.3 Access boundaries

- Only the assigned support operator for that engagement (currently ahliweb) may access a
  customer's host, and only for the duration needed to resolve the specific reported issue —
  no standing/blanket access is granted by any tier.
- Every remote access session is logged on the operator worksheet (`docs/business/pilot-program.md`
  section 5 structure reused for support incidents): timestamp, reason, what was
  accessed/changed.
- Granting `--allow-docker-group` to any account is a root-equivalent privilege grant (per the
  engineering brief) and must be flagged explicitly to the customer before it is used during a
  support session, not applied silently.
- Participant/customer consent is obtained before each access session, not assumed from a
  general support agreement.

### 4.4 Who may hold tokens

- Only the operator of record for an engagement may hold a temporary copy of a customer's
  token or secret, and only when strictly necessary to reproduce or fix the reported issue.
- Any such copy is deleted immediately after the session, is never stored in this repository,
  in plaintext chat history, or in any ticketing system field that is not access-controlled.
- No token or secret is ever requested or held for Community-tier support (which has no
  dedicated operator access at all).

### 4.5 Evidence handling

- Incident evidence (logs, screenshots, terminal output) is redacted to remove secret values
  before it is stored or shared with anyone, including internally.
- Redacted evidence is stored separately from any live secret material, never alongside it.
- Retain incident evidence for 90 days after resolution (ASSUMPTION retention period, to be
  revisited if a compliance requirement demands otherwise), then delete it.

## 5. Escalation summary

| Trigger | Escalates to |
|---|---|
| Unresolved after 2 Assisted-tier exchanges | Guided troubleshooting session (Professional-style), regardless of the customer's paid tier |
| Any secret/token/suspected-compromise issue, any tier | Section 4 security-sensitive process, immediately, bypassing normal ticket flow |
| Full-outage report, Business tier | Owner/operator (ahliweb), same business day acknowledgment |
| Any Enterprise-tier request | Owner/operator (ahliweb) only, no Enterprise terms sold without a reviewed agreement |

## 6. What is never supported, at any tier

- **Unsupported OS tiers**: any platform outside the compatibility matrix in
  `docs/research-and-implementation-plan.md` (i.e., anything other than Ubuntu Server 24.04
  LTS amd64, Ubuntu 22.04 amd64, and Linux Mint 22.x amd64; arm64 remains tier-3 best-effort
  and is explicitly not a supported commitment at any paid tier).
- **Source builds**: building OMES's upstream dependencies (e.g., Hyprland) from source
  instead of the supported package sources. If a fragile source build is required for a
  target environment, that environment is out of scope until the platform's packaged path is
  available.
- **Modified OMES trees**: a customer's fork or hand-edited copy of `bin/omes`, `lib/omes/*`,
  `modules/*`, or profile files. Support on a modified tree first requires reverting to a
  clean, pinned OMES ref; only then can normal troubleshooting proceed.
