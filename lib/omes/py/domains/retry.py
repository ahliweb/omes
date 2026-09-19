"""lib/omes/py/domains/retry.py - safe-retry classification for domain
provider operations (issue #100: "Support provider response polling/
reconciliation and retry only when request status is known to be safe to
retry").

This mirrors `lib/omes/py/jobs/runner.py`'s retry-classification table
style, but for provider-level exceptions/results rather than `bin/omes`
exit codes. The rule that matters: a classification of "unknown" or
"ambiguous" (see SRS-X's shared 1001 code for both "pending document"
and "failed" - kb.srs-x.com/en/api/domain/register-domain) must NEVER be
treated as automatically retryable, because retrying a registration that
actually already exists at the provider risks a duplicate/duplicate-cost
operation.
"""
from __future__ import annotations

from typing import Any

from . import fake_provider


def classify_exception(exc: Exception) -> dict[str, Any]:
    """Classifies an exception raised by a `FakeRegistrar` (or, for a
    live adapter, its real equivalent) call."""
    if isinstance(exc, fake_provider.DuplicateRequestError):
        return {"code": "duplicate_request", "retryable": False}
    if isinstance(exc, fake_provider.TransportError):
        return {"code": "transport_error", "retryable": True}
    if isinstance(exc, fake_provider.ProviderError):
        return {"code": "provider_error", "retryable": False}
    return {"code": "unknown_error", "retryable": False}


def classify_srsx_result_code(result_code: int, *, document_pending: bool) -> dict[str, Any]:
    """Classifies an SRS-X API result code (1000/1001 - see
    lib/omes/py/domains/profiles/srsx.py's docstring for the source
    citation).

    `document_pending` must come from the CALLER's own tracked
    `document-lifecycle` state, never inferred from the result code
    alone - SRS-X's `1001` is documented to mean EITHER "pending
    document" OR "creation failure", and the two are not distinguishable
    from the result code by itself. Treating an ambiguous 1001 as
    retryable without that extra context would risk re-submitting a
    registration that is actually just waiting on a human to upload a
    document.
    """
    from .profiles import srsx

    if result_code == srsx.API_RESULT_CODE_SUCCESS:
        return {"code": "succeeded", "retryable": False}
    if result_code == srsx.API_RESULT_CODE_FAILED_OR_PENDING:
        if document_pending:
            return {"code": "documents_required", "retryable": False}
        return {"code": "failed_ambiguous", "retryable": False}
    return {"code": "unknown_result_code", "retryable": False}
