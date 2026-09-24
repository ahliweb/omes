"""lib/omes/py/privacy - OMES AI data-classification and model-egress policy
(issue #214, ADR-0029).

This package is metadata-only: it never receives, logs, or persists prompt
text, response text, embeddings, retrieved documents, or credential values.
See `egress_policy.py` for the deterministic evaluator.
"""
