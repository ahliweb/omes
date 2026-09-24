---
issue: 213
type: docs
summary: define the canonical AI data privacy boundary and local/cloud model security policy
---

### Summary of changes

1. Added the canonical AI data privacy and model-security architecture covering local-only
   inference and local sensitive-data plane + cloud reasoning/control plane patterns.
2. Defined PUBLIC, INTERNAL, CONFIDENTIAL, and RESTRICTED classifications with fail-closed
   egress rules, sanitization/tokenization guidance, RAG/embedding treatment, and bounded
   audit evidence.
3. Added ADR-0029 to preserve Hermes provider-routing ownership while assigning OMES the
   host-side policy, hardening, verification, evidence, and recovery boundary.
4. Mapped the design to current ISO/NIST/OWASP guidance and Indonesian privacy/electronic
   system/AI ethics context without claiming certification or legal compliance.
5. Linked atomic implementation work in issues #214-#218 and marked runtime controls as not
   implemented yet.
