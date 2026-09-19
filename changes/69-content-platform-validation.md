---
issue: 69
type: feature
---
Add platform-aware caption/cover/policy validation:
`lib/omes/py/content/validation.py` plus per-platform constraint files
`lib/omes/py/content/platforms/{generic,youtube}.json`. Every profile
field is either author-asserted or explicitly tagged
`{"value", "verified", "source"/"note"}`; an unverified field can only
produce a warning, never a publish-blocking error, so no fabricated
number is ever presented as platform fact (`youtube.json`'s title/
description limits are sourced from an official Google Support page;
everything else in it is explicitly marked unverified). Detects
unsupported absolute claims ("guaranteed", "cures", ...), missing
sponsorship disclosures, and malformed/oversized links. `omes content
plan` shows a per-target validation preview; `omes content publish
--platform <p>` validates before invoking a worker and blocks only that
one platform on a blocking issue (`--force-validation` overrides).
`omes content edit <job> --platform <p> --caption-file <f>` saves a
versioned, never-overwritten `caption.v<n>` under
`processing/<job>/variants/<platform>/`, keeping the original
`plan.caption` and the immutable `source.<ext>` untouched.
