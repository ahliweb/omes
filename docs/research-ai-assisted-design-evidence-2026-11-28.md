# AI-assisted design and specification evidence — Control Center handoff and host evidence

- Version: 0.2.0
- Date: 2026-11-28
- Status: research proposal only (proposed; nothing here is an adopted rule, a gate decision, or approval to implement)
- Related issue: #343
- Evidence snapshot: main AGENTS.md blob prefix ac23ee65a054 (not commit SHA).

## Research evidence
Rapid review of primary public sources, not a systematic review or product trial. [NgodingPakeAI](https://ngodingpakeai.com/) lists planning and codebase synchronization among its navigation labels without describing them (its design feature is marked as upcoming); [DesainPakeAI](https://desainpakeai.com/updates) release notes describe contextual review feedback. Both are vendor self-report, snapshot 2026-11-28, and do not demonstrate effectiveness. [Cui et al.](https://doi.org/10.1287/mnsc.2025.00535) report field experiments on developer productivity (not verified at takeover: the publisher page was not accessible, so year, sample size and effect figures are not stated here); [METR 2025](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/) randomized 16 developers over 246 issues and reported 19% longer time (the page itself marks the result as not current), with a [2026-02-24 update](https://metr.org/blog/2026-02-24-uplift-update/). Outcomes differ; the statement that no pooled effect or local ROI can be concluded is this document's inference, not a quotation from either source. [Perry et al.](https://arxiv.org/abs/2211.03622) found, in a bounded user study, that participants were more inclined to believe their code was secure while it was less so (paraphrase). ISO 12207 ([catalog page](https://www.iso.org/standard/90219.html)) and ISO 9241-210 ([catalog page](https://www.iso.org/standard/77520.html)) are not verified at takeover (catalog access refused; edition years are not stated here), [Spec Kit](https://github.com/github/spec-kit), the [DTCG 2025.10](https://www.designtokens.org/tr/2025.10/format/) format (a Community Group Report, not a W3C Recommendation) and the [Stitch DESIGN.md](https://blog.google/innovation-and-ai/models-and-research/google-labs/stitch-design-md/) post (2026-04-21, a draft specification) inform lifecycle, cross-spec review and vendor-neutral design artifacts.

## Scope and pilot
OMES owns host lifecycle, compatibility, security, rollback and evidence. Hermes owns agent runtime; Control Center UI remains on AWCMS surfaces.
Define a synthetic review evidence projection for requirement/permission/API/approval and host operation, read-only by default; test stale or forged evidence and revoked permissions. Avoid any new agent orchestration/runtime, UI platform or arbitrary commands.

## Evidence contract
Reuse existing requirements, ADRs and canonical artifacts. Trace requirement/AC → UI (if applicable) → data/permission/operation → test/evidence; record observed, approved, inferred, unknown and proposed separately. Pin actual source SHA before pilot. Record contextual review, synthetic cases, baseline and relevant manual/automated [WCAG 2.2](https://www.w3.org/TR/WCAG22/) (W3C Recommendation, 12 December 2024) checks; no automation-only compliance claim. Mark UI artifact N/A with reason if no UI ownership. Measure time including review/rework, cost, defect/security and task success; no invented result. Return conflicts to owning specification and applicable gate.
 
## Safety and ownership
Concept-only, no copying proprietary code/assets, no new dependency. Minimize context, synthetic fixtures, least privilege, redaction and provider/egress approval. [NIST SSDF](https://csrc.nist.gov/projects/ssdf/publications) v1.1 final (2022), 1.2 draft (2025-12-17). [Indonesia PDP law](https://peraturan.bpk.go.id/Details/229798/uu-no-27-tahun-2022) (page identified; article text not verified at takeover) and [copyright law](https://peraturan.bpk.go.id/Details/38690/uu-no-28-tahun-2014) (title identified: UU 28/2014 on Copyright) as applicable; this is not legal advice. No certification claim. No implementation or architecture approval; issue #343 owns deeper validation, repo-specific CI, and review. Index and governance checks before merge.


## GitHub Projects — lifecycle coordination (2026-11-28)

Direction relayed by the authoring session (substance only, no approval recorded): coordinate the applicable lifecycle steps of the internal cross-portfolio development flow through GitHub Projects (v2), integrated with existing project management rather than a second board or a replacement for repository authority. Everything below is proposed. Related #343. OMES host/deployment evidence and Control Center contracts; Hermes runtime ownership remains separate; no parallel orchestrator.

- Steps 1–8: track research/specification deliverables, dependency, reviewer, acceptance criteria and evidence URL; no implementation issue before applicable Gate 9. Step 9: gate tracker references recorded decision, never inferred from board stage.
- Steps 10–12: canonical issue per S/M unit, native parent/sub-issue and blocked-by relations where supported, milestone/priority/assignee preserved; PR references issue and CI evidence. Steps 13–15: track staging/UAT only if applicable, release-readiness evidence and decision. Steps 16–18: deployment, validation, monitoring and follow-up with release/incident evidence.
- Reuse an existing project and its existing fields where one exists (field names and project membership not verified at takeover); proposed mapping Rencana = backlog, Siap = applicable DoR, Berjalan = claimed, Menunggu = dependency/review/approval, Selesai = verified outcome, Batal = cancelled with reason. Lifecycle step is separate from stage. Gate rejection remains recorded even if tracker closed. Do not mark closed-unmerged PR, duplicate/not-planned issue, or missing evidence as Selesai automatically. Preserve owner/assignee and target; do not invent deadlines.
- Discover live project owner/number, access and field IDs; add existing issue URL/node ID idempotently, dry-run diff, readback and reconcile; keep one canonical issue and distinguish PR items from work counts. No shared draft cards or confidential context. Review permission, avoid broad seeding or status automation conflicts, never move items across visibility boundaries automatically. Board is coordination only, not merge/deploy authority. Report verified vs pending; no membership checked in this research update.

References: https://docs.github.com/en/issues/planning-and-tracking-with-projects ; https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects ; https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-issue-dependencies ; https://docs.github.com/en/issues/tracking-your-work-with-issues/learning-about-issues/about-issue-statuses .

## Reference verification at takeover (2026-11-28)

Read-only check of the source pages on 2026-11-28. Of the 15 research references in the evidence paragraph, 11 were supported (two with corrections), 1 was supported only in part, and 3 could not be verified. Two further pages (WCAG 2.2, the copyright law title) were identified at takeover. Corrections made: METR counts 246 issues (not tasks) and its page marks the result as not current; the vendor pages are described as navigation labels and release notes, labelled vendor self-report; DTCG is a Community Group Report; the Perry finding is a paraphrase. Marked **not verified at takeover** and stated without figures or edition years: Cui et al., ISO 12207, ISO 9241-210 (the PDP law article text is also not verified). ISO 29148 was not checked. No new references were added. Repository claims were checked against the clone: issue #343 is open, and the `AGENTS.md` blob prefix `ac23ee65a054` matches `main`. No test was run for this document.

## Changelog

- 0.2.0 (2026-11-28): takeover review; corrected references, marked unverified ones, neutralised internal-standard wording, added verification section.
- 0.1.0 (2026-11-28): initial research draft.
