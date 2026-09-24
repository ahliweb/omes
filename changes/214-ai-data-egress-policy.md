---
issue: 214
type: feat
summary: add a machine-readable AI data-classification and model-egress policy contract and evaluator
---

### Summary of changes

1. **Versioned wire contracts (`contracts/ai-egress/v1/`)**:
   - `egress-decision-request.schema.json` - metadata-only input (policy version, classification,
     destination, purpose, provider posture, authentication-material flag, sanitization evidence
     reference). Never carries prompt text, response text, embeddings, retrieved documents, or a
     real credential value.
   - `egress-decision-response.schema.json` - bounded decision output (`allow` / `deny` /
     `approval_required` plus a closed, stable reason-code vocabulary). Never carries a command,
     path, or anything executable.
   - Valid/invalid fixtures under `contracts/ai-egress/v1/fixtures/` covering every data class
     (`PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `RESTRICTED`) and every destination (`local_only`,
     `private_endpoint`, `cloud_sanitized`, `deny`), verified by `scripts/check-contracts.py`.
     No fixture contains a real or realistic-looking secret value.

2. **Deterministic evaluator (`lib/omes/py/privacy/egress_policy.py`)**:
   - Pure, stdlib-only Python function implementing the decision matrix from
     `docs/ai-data-privacy-and-model-security.md` section 6 (ADR-0029). No network I/O, no
     logging, no persistence.
   - Fails closed on missing/unknown policy version, classification, destination, or provider
     posture.
   - `RESTRICTED` -> `cloud_sanitized` is always denied. Authentication material
     (credentials/tokens/private keys/secrets) is always denied for any destination other than
     `local_only`, regardless of classification or provider posture.
   - Unit tests in `tests/py/privacy/test_egress_policy.py` (32 tests) cover every fail-closed
     path, the full decision matrix, and structural bounds on the response shape.

3. **Architecture registry**:
   - Added the `omes.privacy.egress_policy` capability entry to `architecture/capabilities.json`
     mapping the new `privacy` module (disposition `adapt`, ADR-0029), keeping
     `scripts/check-architecture.py` module-coverage checks green.

4. **Documentation**:
   - `docs/ai-data-privacy-and-model-security.md`, `docs/architecture.md`, `docs/security.md`,
     and ADR-0029 now link the implemented contract/evaluator and no longer say #214 is
     unimplemented. #215-#218 remain explicitly marked "Not implemented yet (tracked in #N)".
   - `contracts/README.md` documents the new `ai-egress/v1` area.

Restricted local-only runtime enforcement, privacy-posture evidence without prompt capture,
Control Center projection, and negative/regression bypass tests remain out of scope for this
change and are tracked in #215-#218.
