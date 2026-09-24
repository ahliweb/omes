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

## Validator subset and fail-closed keyword enforcement

`scripts/check-contracts.py` (core logic in
`lib/omes/py/jobs/schema.py`, unified with `lib/omes/py/agent/jsonschema_lite.py`)
implements a deliberately small, dependency-free subset of JSON Schema draft
2020-12 (issue #172):

### Supported validation keywords

```
type, required, properties, additionalProperties, enum, const,
pattern, minimum, maximum, minLength, maxLength, minItems, maxItems,
items, oneOf, anyOf
```

### Allowlisted metadata annotations

```
$schema, $id, title, description
```

### Fail-closed enforcement (issue #172)

Any other JSON Schema keyword (`$ref`, `format`, `if`/`then`/`else`, `allOf`,
`not`, `uniqueItems`, `patternProperties`, ...) is **rejected** with a
`SchemaError` naming the exact schema path and keyword (e.g. `$.properties.a:
unsupported JSON Schema keyword '$ref'`).

OMES deliberately fails closed in CI and runtime contract loading rather than
silently ignoring constraints. Schema authors must stay strictly within this
supported subset. If a contract requires semantics outside this subset, it must
either be rewritten within the supported keywords or trigger an explicit
architecture/ADR decision to expand the stdlib engine.

Standards-compliant Draft 2020-12 validation was evaluated for CI. In keeping
with ADR-0012's Python-stdlib-only policy, third-party PyPI dependencies (such as
`jsonschema`) are excluded from both runtime and CI to prevent supply-chain
risks and network dependencies. The stdlib validator provides deterministic,
air-gapped contract assurance across all platforms.

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
| `ai-egress/v1` | issue #214 (ADR-0029) | Metadata-only AI data-classification and model-egress decision request/response contracts. Implemented and consumed by the deterministic evaluator `lib/omes/py/privacy/egress_policy.py`. Restricted local-only runtime enforcement, privacy evidence, and Control Center projection of these decisions are tracked separately in #215-#218 and are not implemented yet. |
