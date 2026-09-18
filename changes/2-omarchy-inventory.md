---
issue: 2
type: docs
---
Add docs/omarchy-compatibility-inventory.md mapping upstream Omarchy capabilities to PORT/ADAPT/DEFER/REJECT decisions with rationale and sources.

<!-- OMES-MERMAID: changes/2-omarchy-inventory.md -->

## Visual summary

```mermaid
flowchart LR
    Fragment[Change fragment] --> Review[Review in pull request]
    Review --> Release[Release compilation]
    Release --> Changelog[Changelog entry]
```

