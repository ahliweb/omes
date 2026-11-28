# AI-assisted design and specification evidence — Control Center handoff and host evidence

- Version: 0.1.0
- Date: 2026-11-28
- Status: research proposal only
- Related issue: #343
- Evidence snapshot: main AGENTS.md blob prefix ac23ee65a054 (not commit SHA).

## Research evidence
Rapid review of primary public sources, not a systematic review or product trial. [NgodingPakeAI](https://ngodingpakeai.com/) presents planning and codebase synchronization; [DesainPakeAI](https://desainpakeai.com/updates) describes contextual review feedback. Vendor feature claims do not demonstrate effectiveness. [Cui et al. 2026](https://doi.org/10.1287/mnsc.2025.00535) pooled 4,867 developers across three field experiments, reporting 26.08% more completed tasks (SE 10.3%); [METR 2025](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/) randomized 246 tasks with 16 developers and reported 19% longer time, with [2026 design update](https://metr.org/blog/2026-02-24-uplift-update/). Outcomes differ; no pooled effect or local ROI prediction. [Perry et al.](https://arxiv.org/abs/2211.03622) found security and confidence risks in a bounded user study. [ISO 12207:2026](https://www.iso.org/standard/90219.html), [ISO 9241-210:2019](https://www.iso.org/standard/77520.html), [Spec Kit](https://github.com/github/spec-kit), [DTCG 2025.10](https://www.designtokens.org/tr/2025.10/format/) and [Stitch DESIGN.md](https://blog.google/innovation-and-ai/models-and-research/google-labs/stitch-design-md/) inform lifecycle, cross-spec review and vendor-neutral design artifacts.

## Scope and pilot
OMES owns host lifecycle, compatibility, security, rollback and evidence. Hermes owns agent runtime; Control Center UI remains on AWCMS surfaces.
Define a synthetic review evidence projection for requirement/permission/API/approval and host operation, read-only by default; test stale or forged evidence and revoked permissions. Avoid any new agent orchestration/runtime, UI platform or arbitrary commands.

## Evidence contract
Reuse existing requirements, ADRs and canonical artifacts. Trace requirement/AC → UI (if applicable) → data/permission/operation → test/evidence; record observed, approved, inferred, unknown and proposed separately. Pin actual source SHA before pilot. Record contextual review, synthetic cases, baseline and relevant manual/automated [WCAG 2.2](https://www.w3.org/TR/WCAG22/) checks; no automation-only compliance claim. Mark UI artifact N/A with reason if no UI ownership. Measure time including review/rework, cost, defect/security and task success; no invented result. Return conflicts to owning specification and applicable gate.
 
## Safety and ownership
Concept-only, no copying proprietary code/assets, no new dependency. Minimize context, synthetic fixtures, least privilege, redaction and provider/egress approval. [NIST SSDF](https://csrc.nist.gov/projects/ssdf/publications) v1.1 final, 1.2 draft. [Indonesia PDP](https://peraturan.bpk.go.id/Details/229798/uu-no-27-tahun-2022) and [copyright](https://peraturan.bpk.go.id/Details/38690/uu-no-28-tahun-2014) as applicable. No certification claim. No implementation or architecture approval; issue #343 owns deeper validation, repo-specific CI, and review. Index and governance checks before merge.


## GitHub Projects — lifecycle coordination (2026-11-28)

Owner direction: coordinate all applicable AWSES steps through GitHub Projects (v2); integrate with existing project management, not a second board or replacement for repo authority. Related #343. OMES host/deployment evidence and Control Center contracts; Hermes runtime ownership remains separate; no parallel orchestrator.

- Steps 1–8: track research/specification deliverables, dependency, reviewer, acceptance criteria and evidence URL; no implementation issue before applicable Gate 9. Step 9: gate tracker references recorded decision, never inferred from board stage.
- Steps 10–12: canonical issue per S/M unit, native parent/sub-issue and blocked-by relations where supported, milestone/priority/assignee preserved; PR references issue and CI evidence. Steps 13–15: track staging/UAT only if applicable, release-readiness evidence and decision. Steps 16–18: deployment, validation, monitoring and follow-up with release/incident evidence.
- Reuse approved project and its existing fields (e.g. Tahap, Pelaksana, Prioritas, Mulai, Target); proposed mapping Rencana = backlog, Siap = applicable DoR, Berjalan = claimed, Menunggu = dependency/review/approval, Selesai = verified outcome, Batal = cancelled with reason. Lifecycle step is separate from stage. Gate rejection remains recorded even if tracker closed. Do not mark closed-unmerged PR, duplicate/not-planned issue, or missing evidence as Selesai automatically. Preserve owner/assignee and target; do not invent deadlines.
- Discover live project owner/number, access and field IDs; add existing issue URL/node ID idempotently, dry-run diff, readback and reconcile; keep one canonical issue and distinguish PR items from work counts. No shared draft cards or confidential context. Review permission, avoid broad seeding or status automation conflicts, never move items across visibility boundaries automatically. Board is coordination only, not merge/deploy authority. Report verified vs pending; no membership checked in this research update.

References: https://docs.github.com/en/issues/planning-and-tracking-with-projects ; https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects ; https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-issue-dependencies ; https://docs.github.com/en/issues/tracking-your-work-with-issues/learning-about-issues/about-issue-statuses .
