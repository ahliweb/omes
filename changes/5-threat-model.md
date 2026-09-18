---
issue: 5
type: docs
---
Add the STRIDE threat model, security baseline, and repository vulnerability disclosure policy.

<!-- OMES-MERMAID: changes/5-threat-model.md -->

## Visual summary

```mermaid
flowchart LR
    Fragment[Change fragment] --> Review[Review in pull request]
    Review --> Release[Release compilation]
    Release --> Changelog[Changelog entry]
```

