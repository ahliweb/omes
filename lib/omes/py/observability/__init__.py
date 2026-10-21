"""lib/omes/py/observability - OMES cross-plane correlation/event envelope
(issue #272, ADR-0032 rule 5) and policy/capability decision envelope
(issue #274, ADR-0032 rule 7).

Evidence, not authority: the envelope carries identifiers, a closed status,
freshness, classification and redaction state. It never carries prompts,
transcripts, commands, tool arguments/results, credentials or reasoning, and
it never grants permission. See `envelope.py`.

`policy_decision.py` validates the decision envelope and composes decisions
already made by existing authorities. It is not a policy engine and creates
no approvals.

Standard library only (ADR-0012).
"""
