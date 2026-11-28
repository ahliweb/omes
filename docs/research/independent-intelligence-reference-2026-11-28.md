# Independent intelligence capability research

## Metadata
- Version: v1.3.0
- Last updated: 2026-11-28
- Changeset: takeover review: vendor-neutral wording, corrected references, unverified references marked, spike claim restated.
- Status: research/reference only (proposed); no product code, no approved dependency, no Gate 9/15 decision.
- Tracker: #409 (open)
- Evidence: main branch blob snapshots AGENTS ac23ee65a054 and README f136df86376d (verified against `main` at takeover; blob prefixes, not commit SHAs). Full code, migration and CI audit not performed.

## Evidence and intellectual property
This research was prompted by public descriptions of commercial products in the data-fusion, security-assessment and AI-assisted accounting field. Those descriptions are vendor self-report, were not independently tested, and are not evaluated here. The research uses only general functional concepts and open standards. No license to reproduce any third-party product has been established, so: no third-party code, assets, API, text, rules, datasets, brand, proprietary SDK or reverse engineering; no unauthorized testing; original requirements, specifications, design and implementation only.

UU 28/2014 articles 40 and 41 distinguish protected expression from ideas and methods ([Indonesian copyright law](https://peraturan.bpk.go.id/Details/38690/uu-no-28-tahun-2014); title identified at takeover, article text not read in full there; a WIPO copy of the same law lists articles 40 and 41). Patent, trademark, trade secret and contract review is still required. No dependencies are introduced; every proposed dependency, model or data source needs version/SHA, license/SPDX, commercial/SaaS rights, notice, transitive SBOM, owner approval and an exit plan before use. Licensed standards text is not reproduced.

## Repository disposition (proposed)
DELEGATE/ADAPT upstream first. OMES owns authorized host lifecycle, hardening, health and recovery; Hermes owns reasoning and dispatch. The open issues #376 (ADR, threat model, capability ownership), #377 (schemas), #378 (read-only posture collector), #379 (sanitized adapters), #380 (privacy gateway and zero-database-access tests), #381 (threat rules and an optional alert adapter) and #382 (analyst skill and RAG) already cover most of this scope (statuses read from GitHub on 2026-11-28: all open). Reconcile rather than duplicate.

## Controls, validation and evidence (proposed)
No database contents, application payloads or unapproved sources. Read-only metadata allowlist, redaction before egress, consent and revocation, synthetic fixtures, scope denial, offline restore and auditable typed requests. No new agent runtime and no unapproved external scans.

Record requirements and threats, then control, versioned contract, reproducible synthetic test, evidence, SOP and monitoring. Baselines and thresholds are hypotheses until measured. Use least privilege, default-deny RBAC/ABAC and FORCE RLS where applicable, no client-provided tenant trust, no secrets or PII in logs, audited approvals and recovery. No live third-party, customer or public targets are tested. Verify data purpose, retention, lawful basis and controller/processor roles. No cross-tenant data sharing without explicit scoped grants.

## Professional reference register
Status column: S = supported by the source page at takeover (2026-11-28), P = partly supported, N = **not verified at takeover**.

| Reference | URL | Status |
| --- | --- | --- |
| Hall and Llinas, data fusion, IEEE (year as given by the authoring session: 1997) | https://doi.org/10.1109/5.554205 | N |
| Lewis et al. (2020), NeurIPS, retrieval-augmented generation | https://papers.nips.cc/paper/2020/hash/6b493230205f780e1bc26945df7481e5-Abstract.html | S |
| W3C PROV-O Recommendation (30 April 2013) | https://www.w3.org/TR/prov-o/ | S |
| NIST AI RMF 1.0 | https://doi.org/10.6028/NIST.AI.100-1 | N (redirect not followed) |
| NIST CSF 2.0 | https://www.nist.gov/cyberframework | P (CSF 2.0 mentioned; the year 2024 not on the page) |
| NIST SP 800-218 SSDF v1.1 final (Feb 2022); Rev.1 initial public draft (17 Dec 2025, not final) | https://csrc.nist.gov/pubs/sp/800/218/final | S |
| NIST SP 800-218A (July 2024) | https://csrc.nist.gov/pubs/sp/800/218/a/final | S |
| NIST SP 800-61 Rev.3 (April 2025) | https://csrc.nist.gov/pubs/sp/800/61/r3/final | S |
| OASIS STIX 2.1 (10 June 2021) | https://docs.oasis-open.org/cti/stix/v2.1/os/stix-v2.1-os.html | S |
| OWASP ASVS 5.0.0 (published 30 May 2025 per the ASVS site) | https://owasp.org/projects/asvs ; https://asvs.owasp.org/ | S |
| WCAG 2.2 (W3C Recommendation, 12 December 2024) | https://www.w3.org/TR/WCAG22/ | S |
| ISO/IEC catalog (27001, 27002, 27005, 27017, 27018, 27034-1, 27701, 15408-1, 20000-1, 22301, 42001, 23894, 25010, 27035, 27036-3, 27037) | https://www.iso.org/standard/15408-1 and related catalog pages | N (catalog access refused; **edition years are not stated here as fact**; no certification, compliance or clause audit claimed) |
| UU 27/2022 (personal data protection) | https://peraturan.bpk.go.id/Details/229798/uu-no-27-tahun-2022 | P (law identified; article text, including the notification deadline in article 46, **not verified at takeover**: confirm with legal counsel) |
| PP 71/2019 (electronic systems and transactions) | https://jdih.komdigi.go.id/produk_hukum/view/id/695/t/common | S (title) |
| Perpres 82/2022 (vital information infrastructure protection) | https://peraturan.bpk.go.id/Details/211029/perpres-no-82-tahun-2022 | S (title) |

Applicable ITE law, sector laws and current implementing rules must be reviewed before release. A regulatory deadline not verified here must not be hard-coded.

### Scientific and standards register
- FIRST: [CVSS FAQ](https://www.first.org/cvss/faq) states that base scores are not risk (S); [EPSS](https://www.first.org/epss/) is the probability of exploitation in the wild within 30 days, not asset compromise (S); the [EPSS research page](https://www.first.org/epss/research) exists and lists foundational papers by Jacobs and colleagues (S). The Jacobs et al. 2021 paper ([DOI](https://doi.org/10.1145/3436242)) is **not verified at takeover** (publisher access refused); its prediction horizon is not stated here.
- CISA [Known Exploited Vulnerabilities catalog](https://www.cisa.gov/known-exploited-vulnerabilities-catalog) and [SSVC](https://www.cisa.gov/ssvc): pages exist (S); SSVC weighs exploitation, impact and prevalence.
- NIST: [SP 800-115](https://csrc.nist.gov/pubs/sp/800/115/final) (Sept 2008, S), [SP 800-52 Rev.2](https://csrc.nist.gov/pubs/sp/800/52/r2/final) (Aug 2019, S), [SP 800-137](https://csrc.nist.gov/pubs/sp/800/137/final) (Sept 2011, S), [SP 800-101 Rev.1](https://csrc.nist.gov/pubs/sp/800/101/r1/final) (May 2014, S), [SP 800-92](https://csrc.nist.gov/pubs/sp/800/92/final) (Sept 2006; Rev.1 still a draft, S), [SP 800-124 Rev.2](https://csrc.nist.gov/pubs/sp/800/124/r2/final) (May 2023, S), and SP 800-53 Rev.5 ([page](https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final); Release 5.2.0 of 27 August 2025 is mentioned on that page as the latest release, not described by it; AU-6, SI-7 and SI-4 are used as control families).
- TLS: RFC 9846 (July 2026) obsoletes RFC 8446 ([info](https://www.rfc-editor.org/info/rfc9846/), S). BCP 195 comprises RFC 8996, RFC 9325 and RFC 9852 ([info](https://www.rfc-editor.org/info/bcp195/), S); RFC 9325 is also updated by RFC 10015 (July 2026), which is **not** a member of BCP 195 ([RFC 10015](https://www.rfc-editor.org/rfc/rfc10015.html), S). RFC 9525 (Nov 2023) covers service identity ([RFC 9525](https://www.rfc-editor.org/rfc/rfc9525.html), S).
- Logging: [RFC 5424](https://www.rfc-editor.org/info/rfc5424/) (March 2009, S); [OpenTelemetry logs data model](https://opentelemetry.io/docs/specs/otel/logs/data-model/) (page exists, S; reference only).
- Denning (1987), intrusion detection model ([DOI](https://doi.org/10.1109/TSE.1987.232894)): **not verified at takeover**.
- Stored data distinguishes source, time and version; stale or unknown is distinct from zero. A human decision weighs asset impact, exposure and controls. No proprietary or uncalibrated production scoring formula. Proposed validation: coverage and efficiency, precision and recall, calibration (Brier score) on time-separated authorized or synthetic data against a severity-only baseline.

## Proposed guardrails and evidence
Explicit asset scope and authorization with expiry, revocation and recheck; tenant isolation; deny unauthorized destinations, redirects, SSRF and private targets; bounded job budgets and a stop switch; no intrusive or destructive scans, arbitrary templates or shell, credential harvesting or live target tests. Synthetic fixtures only. Restricted evidence has provenance and hash, redaction, retention and deletion; finding lifecycle, risk acceptance, retest and incident notification with approval. No secrets or private findings in public artifacts or GitHub tickets; scoped private integration only with explicit human authorization and deduplication. AI drafts only, no self-approval; prompt-injection, egress and tenant-negative tests. No real data, no dependencies, no Gate 9/15 authorization.

## Open-source license register (observed on the default branch, not a pinned release)
These are candidate observations, not approvals. Nothing is installed, linked or vendored.
- ZAP: Apache-2.0 (https://github.com/zaproxy/zaproxy/blob/main/LICENSE).
- Nuclei: MIT (https://github.com/projectdiscovery/nuclei/blob/dev/LICENSE.md); official templates: MIT (https://github.com/projectdiscovery/nuclei-templates/blob/main/LICENSE.md), with separate provenance, signature and safety review.
- testssl.sh: GPL-2.0 (https://github.com/testssl/testssl.sh); HOLD for distribution review.
- Wazuh (https://github.com/wazuh/wazuh/blob/main/README.md): engine AGPL-3.0-or-later; selected shared libraries GPL-2.0-only OR AGPL-3.0-or-later; the rest GPLv2; HOLD for per-component package and network/distribution review.
- MVT (https://github.com/mvt-project/mvt/blob/main/LICENSE): MVT License 1.1, adapted from MPL 2.0, with a consensual-use restriction; HOLD, requiring explicit informed non-coerced data-owner consent.

Before any use: pin tag, SHA or digest, SBOM, notices, commercial and SaaS rights, feed, data and plugin terms, integration and exit plan. Separate processes do not remove license obligations.

## Independent reconstruction proposal (2026-11-28)
Direction relayed by the authoring session (not an approval recorded in this repository): prefer original capabilities over unapproved dependencies. Proposed consequence: do not install, link, vendor, port, translate or copy source, structure, UI, rules, templates, indicators or datasets of the products listed above or of any commercial product, and do not use them as an oracle or reverse engineer them. GPL and AGPL are not commercial bans; independent implementation is a business and architecture choice, not a legal exemption. Informed, non-coerced device-owner consent is preserved regardless of implementation. ZAP and Nuclei remain unapproved candidates. Public functional descriptions and independent standards only; contributor provenance attestation, source register, independent requirements, reviewer, similarity and dependency/SBOM checks, and pinned version/SHA/license/notice/patent/data terms/exit plan are proposed; no formal clean-room or compliance claim. Other IP and contracts need review. No restricted material in agent prompts.

### Independent standards and scope (proposed)
Use approved runtime TLS APIs and never new cryptography; record negotiated version, chain, identity and expiry only, with unknown never treated as pass. A TLS 1.2 observation is not BCP compliance; no parity or exploit probes. NIST SP 800-101 Rev.1 and SP 800-124 Rev.2 mark scope boundaries and are not a forensic acquisition method. ISO catalog items are mapped as evidence only, not certification. UU PDP, UU ITE, PP 71/2019, purpose, retention, consent and legal review apply.

Provisional IDs (proposed, not allocated): IND-01 TLS posture (consumer worker), IND-02 host metadata (OMES), IND-03 event normalization and correlation (AWCMS contract, OMES summaries), IND-04 integrity evidence (OMES), IND-05 mobile posture (product discovery), IND-06 risk, evidence and cases (AWCMS or consumer). No full SIEM, XDR, forensics or spyware-detection claim. Explicit scoped grant, expiry and revocation, independent worker, synthetic data, no unauthorized I/O or private data, no actions without approval.

Test claim: the authoring session reported 19 of 19 synthetic tests passing in a reference spike on Node v22.16.0 outside this repository. It was not run in this repository, was not re-run at takeover, and is not evidence of implementation here. No Bun, CI, actual TLS, device, RLS or production test is claimed.

### Local requirements and acceptance (proposed)
Reconcile #376 to #382 first. IND-02 and IND-04: native read-only allowlisted host posture observations, local comparison with an approved configuration digest, bounded sanitized event summaries; no arbitrary paths, raw file content, database, payload, communications or automated blocking. Any third-party alert adapter (see the optional adapter in #381) is a candidate only and needs a license and architecture gate; no agent, decoder or rule port, no XDR promise and no second Hermes runtime. Tests to define: baseline authorization and version, unknown and changed state, revocation and source identity, metadata-only egress, rollback and resource limits. AWCMS owns business cases and correlation contracts; Hermes owns reasoning only. Evaluate dedicated versus shared collector isolation, retention, egress, resource cost and restore, with synthetic fixture replay. Distinguish 24/7 automated alerts from a staffed SLA.

## Next gate
Follow the internal cross-portfolio 18-step lifecycle: discovery and UX, privacy and threat model, data and authorization, algorithm and contracts before Gate 9; only the research issue is in scope now. Repository CI, index and changeset are validated in review; consumer UAT and Gate 15 come before production. Rollback is removing this document (no runtime effect). No CI or test execution is claimed for this research.

## Reference verification at takeover (2026-11-28)
Read-only check of source pages on 2026-11-28. Of the roughly 45 references reviewed, most were supported (marked S), a few partly (P), and the rest could not be verified (N): the ISO catalog (and all edition years), the article 46 text of UU 27/2022, Hall and Llinas, Denning, Jacobs et al., and NIST AI RMF. They stay cited and are marked "not verified at takeover"; no figure or edition year from them is stated as fact. Corrections made: RFC 10015 is outside BCP 195; SP 800-53 Rev.5 Release 5.2.0 attribution; ASVS 5.0.0 date taken from the ASVS site; all vendor names, prices, service hours, product comparisons and tool-usage claims about a commercial product were removed from this public repository (the vendor statement about which scanners it uses was not supported by the pages read). No reference was added. Repository claims checked: #409 and #376 to #382 are open; the AGENTS.md and README.md blob prefixes match `main`.

## Changelog
- v1.3.0 (2026-11-28): takeover review; vendor-neutral wording, corrected and marked references, test claim restated, verification section added.
- v1.2.0 (2026-11-28): independent reconstruction replacing unapproved components; proposed local acceptance.
- v1.1.0 (2026-11-28): follow-up research, local acceptance and license register.
- v1.0.0 (2026-11-28): initial research, local ownership and controls.
