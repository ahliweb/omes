# Branding and trademarks

> Status: accepted baseline (Phase 0 — Foundation and design decisions)
> Related: [docs/scope.md](scope.md), [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md), [Issue #26](https://github.com/ahliweb/omes/issues/26)

## 1. Why this document exists

OMES is inspired by Omarchy's workflow and integrates Hermes Agent, a
separate Nous Research product. Neither relationship is an affiliation,
endorsement, or partnership, and OMES must never be described in a way that
implies otherwise. This document sets the naming rules, required disclaimer
text, and logo policy that every doc, README, and release note must follow.

## 2. Naming rules

- **Always** describe OMES as "Omarchy-inspired." Acceptable phrasings:
  "an Omarchy-inspired compatibility layer," "inspired by Omarchy's
  workflow," "brings an Omarchy-inspired experience to Ubuntu Server and
  Linux Mint."
- **Never** use "Omarchy for Ubuntu," "Ubuntu Omarchy," "Omarchy Edition,"
  "official Omarchy port," or any phrasing that reads as an affiliation
  claim, a rebrand, or an official derivative.
- **Never** state or imply that OMES is published, endorsed, reviewed, or
  supported by the Omarchy project, its maintainers, Canonical (Ubuntu),
  the Linux Mint project, Docker Inc., Telegram, or Nous Research (Hermes
  Agent's publisher), beyond the factual, nominative statement that OMES
  targets or integrates with their software.
- When naming a target OS, use "Ubuntu Server," "Ubuntu," or "Linux Mint"
  only as plain, factual descriptions of a supported platform — never as
  part of a product name (e.g., write "OMES for Ubuntu Server," not "Ubuntu
  OMES").
- Hermes Agent is always referred to by its own name, "Hermes Agent" or
  "Hermes," and identified as a Nous Research product on first mention in
  any document that discusses it (README, architecture docs, install docs).
  See the required disclaimer text in §4.

## 3. Logo and visual identity policy

- OMES does not reproduce, adapt, or bundle the Omarchy logo, wordmark, or
  any Omarchy visual asset.
- OMES does not reproduce, adapt, or bundle the Ubuntu, Linux Mint, Docker,
  Telegram, or Nous Research/Hermes logos or wordmarks.
- OMES may use plain text references to these names in documentation and
  may link to the upstream projects' own sites/pages.
- OMES's own logo/icon (if and when one is created) must be visually
  distinct from all of the above and must not incorporate or imitate their
  marks.

## 4. Required disclaimer text

Use this exact text (or a direct, unmodified quotation of it) in the README
and in any other document that introduces OMES to a new audience (e.g. a
project landing page, a release announcement):

> OMES is an independent, MIT-licensed project. It is inspired by the
> Omarchy workflow but is not official Omarchy, and it is not affiliated
> with, endorsed by, or sponsored by the Omarchy project, Canonical
> (Ubuntu), the Linux Mint project, Docker, Inc., Telegram, or Nous
> Research. Hermes Agent is a product of Nous Research; OMES integrates
> with it as an external dependency and does not develop or maintain it.

A shorter inline variant, for places where space is limited (e.g. a
one-line project description):

> Omarchy-inspired compatibility layer for Ubuntu Server and Linux Mint —
> independent project, not affiliated with Omarchy.

## 5. Hermes Agent-specific guidance

- Hermes Agent is developed and published by Nous Research
  (`https://github.com/NousResearch/hermes-agent`). OMES integrates Hermes
  as the automation/agentic-operations layer for both profiles; OMES does
  not fork, rebrand, or redistribute Hermes's source.
- Documentation that walks through Hermes setup must link to Hermes's own
  docs (e.g. `https://hermes-agent.nousresearch.com/docs/...`) rather than
  restate Hermes's own claims about itself as if OMES made them.
- OMES must never present Hermes configuration, defaults, or behavior it
  did not itself set (e.g. Hermes's model routing or provider choices) as
  an OMES feature.

## 6. Where this applies

This policy applies to: `README.md`, everything under `docs/`, PR
descriptions, issue templates, release notes, `CHANGELOG.md` entries, and
any external-facing material (blog posts, pilot proposals) derived from
this repository. Contributors should treat a naming/branding slip as a
documentation bug — see [CONTRIBUTING.md](../CONTRIBUTING.md) for how docs
changes are reviewed.

## 7. Related documents

- [docs/scope.md](scope.md) — the underlying product-boundary rationale for
  why OMES is not official Omarchy (§2, "What OMES is not").
- [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) — license and usage
  notices for every third-party project named above.
- [CONTRIBUTING.md](../CONTRIBUTING.md) — contribution workflow, including
  documentation-accuracy requirements.
