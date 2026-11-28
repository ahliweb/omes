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
