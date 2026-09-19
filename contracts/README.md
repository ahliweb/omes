# OMES contracts

This directory holds versioned JSON Schema contracts for boundaries between
OMES and external control planes (currently: the planned AWCMS-based
Control Center, issue [#89](https://github.com/ahliweb/omes/issues/89)).
These are **wire contracts, not implementation**. A schema existing here
does not mean the producing/consuming service exists yet; see
[docs/control-center-and-integrations.md](../docs/control-center-and-integrations.md)
section 11 for what is actually implemented.

## Layout convention

```
contracts/<area>/v<major>/<schema-name>.schema.json
contracts/<area>/v<major>/fixtures/<schema-name>/valid-*.json
contracts/<area>/v<major>/fixtures/<schema-name>/invalid-*.json
contracts/<area>/v<major>/fixtures/<schema-name>/invalid-*.reason.txt   (optional)
```

- `<area>` is a bounded contract domain (e.g. `control-center`).
- `<major>` is the contract's major version (`v1`, `v2`, ...). A directory
  is immutable once published outside this repository's own history —
  see "Compatibility rules" below.
- Every schema file must have at least one `valid-*.json` fixture that
  passes and at least one `invalid-*.json` fixture that fails.
  `scripts/check-contracts.py` enforces this and fails the build if either
  is missing.
- An `invalid-*.json` fixture may have a sibling `invalid-*.reason.txt`
  file containing a substring that must appear in the validator's error
  output. This lets tests assert an invalid fixture fails for the
  *intended* reason (e.g. "additional properties not allowed", not some
  unrelated typo) rather than merely failing for any reason.

## Validator subset

`scripts/check-contracts.py` (core logic in
`lib/omes/py/jobs/schema.py`) implements a deliberately small,
dependency-free subset of JSON Schema draft 2020-12:

```
type, required, properties, additionalProperties, enum, const,
pattern, minimum, maximum, minItems, maxItems, items, oneOf, anyOf
```

Any other JSON Schema keyword (`$ref`, `if`/`then`, `patternProperties`,
`format`, ...) is not evaluated. Contract authors must stay inside this
subset; a schema that relies on an unsupported keyword will silently not
enforce that keyword; it will not raise "unsupported keyword" — this is a
known trade-off of a minimal, stdlib-only implementation (ADR-0012) and is
mitigated by every schema having positive and negative fixtures that
exercise its actual constraints.

The validator additionally runs two checks that are **not** part of JSON
Schema and cannot be turned off by a schema:

- **Key-based:** no field whose name matches
  `token|password|secret|credential|api_key|passphrase|cookie|authorization`
  (case-insensitive) may hold a raw scalar value anywhere in an instance.
  The only allowed shape behind such a field name is a `secret_ref`
  object (`{"store": ..., "key": ...}`) or `null`.
- **Pattern-based:** any string value, regardless of its field name,
  that matches a well-known secret-value shape (Stripe, GitHub, AWS,
  Slack, or a generic bearer token prefix) is rejected too — a
  key-name-only ban would miss a secret placed under a misleadingly
  generic field name (e.g. `notes`).

See [docs/control-center-contracts.md](../docs/control-center-contracts.md)
"Authentication and secret-reference rules".

## Compatibility rules

- Within a `v<major>` directory, only **additive, backward-compatible**
  changes are allowed: new optional properties, new enum values that
  existing consumers can ignore, widened `pattern`/`minimum`/`maximum`
  ranges. A change that removes a property, narrows an enum, tightens
  `additionalProperties` from `true`/schema to `false` on an existing
  producer's shape, or otherwise could break an existing valid message is
  a **breaking change** and requires a new `v<major+1>` directory.
- A `v<major>` directory, once referenced by a merged PR outside this
  change, is never renamed or deleted. Deprecation is documented, not
  silent removal.
- Every schema file's fixtures are the compatibility contract in
  executable form: a proposed change that breaks an existing `valid-*`
  fixture (without that fixture being deliberately updated in the same
  PR, with the reason explained in the PR description) is a compatibility
  regression.

## Areas

| Area | Owner | Status |
|---|---|---|
| `control-center/v1` | issue #89 | Contracts defined; no implementation of the AWCMS producer/OMES consumer sides exists in this repository yet, except the OMES-side job runner (`lib/omes/py/jobs/`, issue #90), which validates `deployment.request` against this contract before running an operation. |
