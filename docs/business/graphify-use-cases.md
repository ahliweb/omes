# Graphify/Obsidian business use cases (issue #57)

Status: draft, pre-validation. This document follows the same rules as every other document in
this directory (see [README.md](README.md)): no market size, willingness-to-pay figure, or price
point is stated as fact; anything unvalidated carries an `ASSUMPTION` tag and a stated
validation method; "Validated" means cited evidence (interview, pilot log, or reproducible
test), "Hypothesis" means belief pending validation. Per issue #57's own instruction, this
document does not claim productivity or token savings without reproducible benchmarks and
customer evidence — no such benchmark exists yet, so no number is claimed anywhere below.

For what Graphify/Obsidian actually do and how OMES wraps them, see
[docs/graphify.md](../graphify.md) (technical reference) and
[docs/graphify-privacy.md](../graphify-privacy.md) (privacy/deletion). This document is the
business-value companion, not a restatement of the mechanics.

## 1. Use cases

Each use case names the OMES command(s) that realize it — every one already implemented
(#50–#55), not aspirational.

### 1.1 Onboarding

**Job-to-be-done (`ASSUMPTION`):** a new engineer joining a team, or a solo operator returning to
a codebase after months away, wants a map of "what exists and how it connects" faster than
reading the whole tree or waiting for a teammate's time.

**Commands:** `omes graphify run <path>` (code-only, local) → `omes graphify export <out> --vault
<vault>` → the new person opens the resulting notes in Obsidian and follows `[[wikilinks]]`
between files, or asks a Hermes session `/graphify <question>` (via `omes graphify skill
install`) instead of grep-ing the tree cold.

**Value vs. novelty (`ASSUMPTION`):** the value claim is *faster orientation than reading raw
source*, not "a pretty graph." A graph visualization with no navigable links back to actual
files, symbols, and commits is novelty; OMES's export explicitly writes source-file links and
an index/provenance note (docs/graphify.md §5.3) specifically so it stays a *navigation aid*, not
a picture to admire once.

### 1.2 Architecture discovery

**Job-to-be-done (`ASSUMPTION`):** an engineer needs to answer "what calls this?" / "what would
break if I change this?" before a refactor, in a codebase they did not fully design.

**Commands:** `omes graphify run` (produces `EXTRACTED`/`INFERRED` edges, docs/graphify.md §1.6),
`graphify query`/`graphify affected`/`graphify god-nodes` (upstream's own query surface, invoked
directly against the graph OMES produced — OMES does not reimplement these, per ADR-0014), or the
Obsidian export's `[[wikilinks]]`/canvas view for visual traversal.

**Value vs. novelty:** the differentiator OMES claims is that the graph is kept *current and
attributable* (re-sync via `omes graphify sync`, §1.3 below) and *provenance-tagged*
(`EXTRACTED` vs. `INFERRED`, so a reader knows which edges are ground truth from parsing vs. an
LLM's inference) — not the graph-rendering itself, which is upstream's.

### 1.3 Project memory

**Job-to-be-done (`ASSUMPTION`):** a small team or solo operator wants a persistent, low-effort
record of a codebase's structure that stays current without a manual wiki someone has to
remember to update.

**Commands:** `omes graphify sync <path> --min-interval <n>` on a scheduled systemd timer
(docs/graphify.md §6.6, never installed by OMES itself — an explicit operator opt-in), which
keeps `graphify-out/` current at near-zero manual effort (no-op when nothing changed, §6.2);
`omes graphify export` to refresh the Obsidian view; `omes graphify status` to check staleness
before trusting an answer.

**Value vs. novelty:** the claim is "lower effort than a hand-maintained wiki," not "smarter than
a wiki." A stale, one-time export is graph-visualization novelty; a scheduled `sync` that costs
nothing when the tree hasn't changed is what makes it *memory* rather than a snapshot.

### 1.4 Research (docs/papers, semantic mode)

**Job-to-be-done (`ASSUMPTION`):** a researcher or engineer wants a queryable graph over a mixed
corpus (code + docs + papers), where relationships an AST pass cannot see (e.g. "this function
implements the algorithm in that paper") are inferred by an LLM.

**Commands:** `omes graphify run <path> --mode semantic` (explicit opt-in, requires
`OMES_GRAPHIFY_PROVIDER_ENV`, docs/graphify.md §3.1) — the only use case in this document that
leaves the local machine, and only when the operator explicitly asks for it.

**Value vs. novelty:** this is the use case most at risk of being novelty rather than value — an
`INFERRED` edge is a model's guess, not ground truth (docs/graphify.md §1.6), and costs real
provider spend every re-run (docs/graphify-privacy.md §1's token/cost note). No claim is made
here that semantic mode is worth its cost for a given team; that is exactly the kind of claim
issue #57 forbids without a benchmark, and none exists yet (`ASSUMPTION`, validation: a
before/after time-to-answer pilot comparing code-only vs. semantic mode on the same repository,
docs/business/pilot-program.md).

### 1.5 Runbooks

**Job-to-be-done (`ASSUMPTION`):** an on-call engineer or new hire needs to find "how does X
actually work in this deployment" without an existing runbook, or wants the graph to anchor a
runbook they're writing (linking a procedure to the actual code/config it operates on).

**Commands:** `omes graphify export` produces per-file notes with source links an author can
`[[wikilink]]` from a hand-written runbook note in the same vault; `omes graphify run --vault`
combined with Hermes's `/graphify` skill lets an on-call session ask "how do I do X" and get an
answer grounded in the actual current code, not a runbook that drifted out of date.

**Value vs. novelty:** the claim is "runbooks that link to ground truth instead of drifting
prose," not "AI-generated runbooks" — OMES generates no runbook content itself; it only supplies
the graph/links a human-authored runbook can point at.

## 2. Measurement plan

Every metric below states, concretely, how it would be measured **from OMES's own logs/state**
— OMES has no telemetry and phones home to nothing (a structural property, not a privacy
add-on); every measurement here is either a local artifact the operator already has, or requires
opt-in pilot instrumentation per `docs/business/pilot-program.md` (which this document does not
own or redefine).

| Metric | What it measures | How measured, from which OMES logs/state | Status |
|---|---|---|---|
| Time-to-answer | How long it takes to answer an architecture/onboarding question with vs. without the graph | **Not automatically captured.** Requires a pilot-log timer (docs/business/pilot-program.md's existing instrumentation pattern) around a specific question, compared with/without `omes graphify`/Obsidian access. No OMES log records "a question was asked" — a `/graphify` Hermes session's own transcript (Hermes-owned, not OMES's) is the closest proxy, and reading it requires the operator's explicit consent per session. | `ASSUMPTION` — ungathered |
| Onboarding time | Time for a new team member to reach a defined competence milestone | Not an OMES-log metric at all — this is an HR/team-process measurement outside anything OMES records. A pilot could compare onboarding time for a new hire with vs. without a `omes graphify run`-produced vault available on day one. | `ASSUMPTION` — ungathered |
| Support effort | Volume/time of support requests that a good graph/vault would have deflected | `docs/business/support-tiers.md`'s existing ticket-tracking (external to OMES, per-tier operational-cost assumptions) is the only current instrumentation; a pilot would tag tickets as "would graphify/Obsidian have answered this" during triage — a human judgment call, not something OMES's own logs can classify automatically. | `ASSUMPTION` — ungathered |
| Repeat usage | Whether an operator keeps using `sync`/`export`/`run` after initial setup, rather than a one-time trial | **Measurable from local state directly, with operator consent**: `module.graphify.version_installed`/`module.graphify-mcp.version_installed` state keys show the module stayed installed; a `graphify-out/omes-sync.json` manifest's `last_run_at` field, read over time, shows whether `sync` keeps running or was a one-off; an OMES `--log-file` (if the operator enabled one) would show repeated `omes graphify {run,sync,export}` invocations. None of this is collected centrally — a pilot would need to ask the operator to voluntarily share their own `omes-sync.json`/log file, not something OMES uploads on its own. | `ASSUMPTION` — ungathered, but the local artifact needed to measure it already exists (unlike the other three) |

**Why repeat usage is the strongest candidate to validate first:** it is the only metric above
that OMES's own local, already-implemented state can answer directly (the manifest's
`last_run_at` field), rather than requiring new instrumentation or subjective judgment calls.

## 3. Privacy objections and willingness-to-pay interview questions

All `ASSUMPTION`-labeled; no numbers are invented. These are candidate interview questions for
`docs/business/icp-and-customer-discovery.md`'s existing interview process, not a new interview
program — see that document's section 5 (interview plan and guide) for how they'd actually be
run.

### Privacy objections to probe

1. "Would you run a tool that reads your entire codebase locally, with no network call, if it
   never phones home?" (tests whether the code-only/local-AST framing, docs/graphify-privacy.md
   §1, addresses the objection at all).
2. "Would that answer change if the tool optionally sent code to an LLM provider, only when you
   explicitly turn that on?" (tests whether explicit opt-in for semantic mode is sufficient, or
   whether *any* provider-backed mode is a hard no for this segment).
3. "Do you have private/client repositories where even local processing feels risky? What would
   need to be true (audit log, `.graphifyignore` review, air-gapped install) for you to trust it
   there?" (tests whether `omes graphify init-ignore`'s safe defaults, #55, are sufficient or
   merely necessary).
4. "Who in your organization would need to approve a tool that writes files into an existing
   Obsidian vault?" (tests the export-isolation value proposition — docs/graphify.md §5's
   never-touches-unrelated-notes guarantee — against actual approval friction).

### Willingness-to-pay questions

1. "If this saved your team meaningful onboarding time, what would you expect to pay: nothing
   (open-source, self-hosted), a one-time setup fee, or a recurring per-seat/per-repo fee?"
2. "Would you pay more for a managed version that keeps the graph in sync automatically (a
   supervised `omes graphify sync` on a schedule) versus running it yourself?"
3. "Would semantic (LLM-backed) extraction change your willingness to pay, given it has an
   ongoing provider-API cost on top of any OMES fee?"
4. "Is the Obsidian vault export itself valuable to you, or would a CLI-only `graphify
   query`/Hermes-skill workflow be just as useful without it?" (tests whether the vault/UI is
   core value or optional presentation — directly informs section 4's packaging recommendation).

No numeric price point, market size, or conversion-rate figure is stated anywhere in this
section — all of it is validation methodology, pending actual interviews.

## 4. Packaging recommendation

Tied to [docs/business/support-tiers.md](support-tiers.md)'s existing tier definitions —
this section does not invent new tiers, only maps Graphify/Obsidian into the ones that exist.

| Tier | Graphify/Obsidian scope | Rationale (`ASSUMPTION` unless cited) |
|---|---|---|
| **Community** | Full self-service: `omes install --module graphify`, `omes graphify run/sync/export/status/init-ignore/purge`, all code-only by default. No provider cost, no OMES-side cost (support-tiers.md §2.1's "~0 hours/month" already applies unchanged). | Matches OMES's existing open-core posture (docs/business/business-model.md) — there is no reason to gate a local-only, already-implemented CLI feature behind a paid tier; doing so would contradict the "community = self-service on the published docs" definition support-tiers.md already commits to. |
| **Professional** (`ASSUMPTION`) | Guided setup of a scheduled `sync` timer + semantic-mode backend configuration (choosing/configuring a provider, understanding the cost tradeoff from §1.4) + troubleshooting a stale/corrupted `graphify-out/` (docs/graphify.md §6.4's recovery path, explained rather than self-served). | Matches support-tiers.md §2.3's existing "guided troubleshooting" scope — Graphify's semantic-mode cost/backend decision is exactly the kind of judgment call that tier already exists to help with; no new tier is needed. |
| **Managed** (`ASSUMPTION`, maps to support-tiers.md's Business/Enterprise tiers) | OMES-managed hosts where the operator runs `omes graphify sync` on a supervised schedule on the customer's behalf, plus periodic `omes graphify purge`/re-index hygiene and privacy-control review (`.graphifyignore` audit) as part of an existing managed-deployment relationship. | This is the only tier where "someone else runs `sync` for you" is plausibly worth paying for beyond the Professional tier's one-time guidance — consistent with support-tiers.md §2.4/§2.5's existing multi-deployment/negotiated scope, not a new business-model shape. |

**Recommendation (`ASSUMPTION`, pending interviews in section 3):** ship Graphify/Obsidian
capability at the Community tier unconditionally (it already is, as of #50–#55) and validate
whether Professional-tier customers actually ask for semantic-mode/scheduling guidance before
building any dedicated managed offering — do not build a managed Graphify-specific product ahead
of that evidence.

## Assumption register

Every `ASSUMPTION` tag above, in one place, for tracking:

| # | Assumption | Validation method |
|---|---|---|
| 1 | Onboarding/architecture-discovery/project-memory/research/runbook jobs-to-be-done as stated in section 1 | Interviews per docs/business/icp-and-customer-discovery.md |
| 2 | Semantic mode's value exceeds its provider-API cost for some segment | Before/after time-to-answer pilot, code-only vs. semantic mode |
| 3 | Time-to-answer, onboarding-time, and support-effort metrics (section 2) | Pilot instrumentation per docs/business/pilot-program.md; none exists yet |
| 4 | Repeat-usage metric is measurable from `omes-sync.json`'s `last_run_at` | Ask a pilot operator to voluntarily share their own manifest/log file |
| 5 | Privacy-objection and willingness-to-pay answers (section 3) | Interviews per docs/business/icp-and-customer-discovery.md |
| 6 | Packaging recommendation (section 4) | Professional-tier request volume for semantic-mode/scheduling help, post-launch |
