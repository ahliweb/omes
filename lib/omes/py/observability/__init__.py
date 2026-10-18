"""lib/omes/py/observability - OMES cross-plane correlation/event envelope
(issue #272, ADR-0032 rule 5).

Evidence, not authority: the envelope carries identifiers, a closed status,
freshness, classification and redaction state. It never carries prompts,
transcripts, commands, tool arguments/results, credentials or reasoning, and
it never grants permission. See `envelope.py`.

Standard library only (ADR-0012).
"""
