---
issue: 323
type: docs
---
Added design/redesain-ui-ux-2026-10, a reference-only follow-up to the Control Center v3 proposal that places it inside the AWCMS admin shell where the functional `/admin/omes/*` screens live: an OMES workspace with its own Pantau / Ubah & pulihkan / Agen / Tata kelola navigation, a production banner, a Bootstrap → Rollback lifecycle strip, data freshness per authority that shows silent sources as unknown rather than healthy, a risk-sorted fleet with multi-select, and an operation approval screen with a four-eyes rule, a required note and typed host confirmation, plus a per-screen spec of the 15 Control Center screens (search, filters, sort and paging for each), a jobs log with exact job/correlation ID search and keyset paging without a total, host search and paging on the fleet overview, and the family's shared data-page pattern; data comes from sample-fleet.json and the operation-request fixture, and nothing in it is implemented or calls an API.
