---
issue: 16
type: ci
---
Add ShellCheck/shfmt/yamllint/actionlint linting, a full-history gitleaks secret scan, an OS compatibility matrix, and supply-chain/link-check guardrails to CI.

<!-- OMES-MERMAID: changes/16-lint-secrets-supply-chain.md -->

## Visual summary

```mermaid
flowchart LR
    Fragment[Change fragment] --> Review[Review in pull request]
    Review --> Release[Release compilation]
    Release --> Changelog[Changelog entry]
```

