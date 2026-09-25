"""hermesbackup.restricted_scope - default-deny preflight gate that stops
Restricted-class prompt/session/context data from silently entering a
default `omes agent-backup create` (issue #235; ADR-0020; see
docs/ai-data-privacy-and-model-security.md section 12).

Enforcement is by DECLARED SCOPE, never by content inspection:

- For the legacy per-class engine (`classes.py`/`archive.py`), OMES itself
  enumerates every path under each class (`CLASS_PATHS`), so the classes
  known to hold prompt/session/context data (`sessions`, `memory`) are a
  closed, upstream-documented, reviewed list - see
  `docs/hermes-backup.md` section 2.
- For the native recovery classes (ADR-0020), OMES never reads or parses
  the archive `hermes profile export`/`hermes backup` produce (that would
  mean re-deriving Hermes's own export format, which OMES does not own
  and must not duplicate - AGENTS.md section 2/ADR-0017 DELEGATE). Instead
  this module treats "does this recovery class include session state" as
  a STATIC fact already documented by ADR-0020 itself (`portable-profile`
  -> "skills, memory, configurations, and session state";
  `full-runtime-dr` -> a strict superset, the full runtime including
  sessions/state.db). Fail-closed default-deny: unless the caller passes
  the explicit `--allow-restricted-scope` opt-in, backup creation (and
  restoring such a backup back onto a live `$HERMES_HOME`) is refused
  with a stable reason code - never silently produced or restored.

This module never reads Hermes's `messages.db`/`.hermes/` content, never
opens a native artifact, and never receives prompt/session/message text -
only recovery-class names and legacy class-name tuples that the rest of
`hermesbackup` already resolved.
"""
from __future__ import annotations

from typing import Iterable, Tuple

#: Legacy `classes.py` class names (see `CLASS_PATHS`) whose declared
#: paths hold Restricted-class prompt/session/context data per
#: docs/ai-data-privacy-and-model-security.md section 12:
#:   - "sessions" -> sessions/ (gateway sessions storage) + state.db
#:     (SQLite: sessions, messages, gateway routing)
#:   - "memory"   -> memories/ (persistent memory, incl. MEMORY.md/USER.md)
#: "runtime-state" (logs/cache/cron) and "secrets" (credentials) are
#: deliberately NOT included here - they are separate, already-gated
#: concerns (secrets already require --include-secrets; runtime-state
#: logs are upstream auto-redacted and are not primarily prompt/session
#: data by design).
RESTRICTED_LEGACY_CLASSES: Tuple[str, ...] = ("sessions", "memory")

#: ADR-0020 recovery classes that, by upstream Hermes's OWN documented
#: behavior (ADR-0020 "Context" section; `docs/hermes-backup.md` section
#: 3), always include session state:
#:   - portable-profile -> `hermes profile export`: skills, memory,
#:     configurations, AND session state.
#:   - full-runtime-dr   -> `hermes backup`: complete runtime disaster
#:     recovery archive - a strict superset of portable-profile's scope.
#: "omes-host" is intentionally excluded: it is OMES's own legacy
#: per-class engine, gated independently via RESTRICTED_LEGACY_CLASSES
#: above (an "omes-host" request that resolves to `sessions`/`memory`
#: goes through create_legacy() and hits that gate instead).
RESTRICTED_RECOVERY_CLASSES: Tuple[str, ...] = ("portable-profile", "full-runtime-dr")

#: Stable reason code (docs/ai-data-privacy-and-model-security.md section
#: 11's "policy decision and stable reason code" evidence shape). One
#: code covers both enforcement points (legacy classes, native recovery
#: classes) because the operator-facing remediation is identical in both
#: cases: pass --allow-restricted-scope as an explicit, reviewed decision.
REASON_RESTRICTED_SCOPE_REQUIRES_OPT_IN = "BACKUP_RESTRICTED_SCOPE_REQUIRES_OPT_IN"


class RestrictedScopeError(Exception):
    """Raised when a backup/restore request would include Restricted-scope
    prompt/session/context data without the explicit, reviewed opt-in.

    `reason_code` is always `REASON_RESTRICTED_SCOPE_REQUIRES_OPT_IN`
    today (kept as an attribute, not a hardcoded string, so a future
    additional reason code does not require call-site changes)."""

    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        super().__init__(message)


def legacy_classes_requiring_opt_in(resolved_classes: Iterable[str]) -> Tuple[str, ...]:
    """Returns the subset of `resolved_classes` that are Restricted-scope,
    in canonical `RESTRICTED_LEGACY_CLASSES` order."""
    requested = set(resolved_classes)
    return tuple(c for c in RESTRICTED_LEGACY_CLASSES if c in requested)


def require_legacy_opt_in(resolved_classes: Iterable[str], allow_restricted_scope: bool) -> None:
    """Fail-closed preflight for the legacy per-class engine. Raises
    RestrictedScopeError (never silently excludes AND never silently
    includes) when a Restricted-scope class was requested without the
    explicit opt-in."""
    matched = legacy_classes_requiring_opt_in(resolved_classes)
    if matched and not allow_restricted_scope:
        raise RestrictedScopeError(
            REASON_RESTRICTED_SCOPE_REQUIRES_OPT_IN,
            "backup class(es) "
            + ", ".join(matched)
            + " contain Restricted-class prompt/session/context data "
            "(docs/ai-data-privacy-and-model-security.md section 12) and "
            "require --allow-restricted-scope as an explicit, reviewed "
            "policy decision; refusing to include them in this backup "
            f"[{REASON_RESTRICTED_SCOPE_REQUIRES_OPT_IN}]",
        )


def require_recovery_class_opt_in(recovery_class: str, allow_restricted_scope: bool) -> None:
    """Fail-closed preflight for the native recovery-class engine (ADR-0020).
    Raises RestrictedScopeError when the requested recovery class always
    includes session state (per ADR-0020's own documented behavior) and
    the caller has not explicitly opted in."""
    if recovery_class in RESTRICTED_RECOVERY_CLASSES and not allow_restricted_scope:
        raise RestrictedScopeError(
            REASON_RESTRICTED_SCOPE_REQUIRES_OPT_IN,
            f"recovery class '{recovery_class}' includes Restricted-class "
            "session state by upstream Hermes's own documented design "
            "(ADR-0020) and requires --allow-restricted-scope as an "
            "explicit, reviewed policy decision; refusing to create/restore "
            f"it as a default backup [{REASON_RESTRICTED_SCOPE_REQUIRES_OPT_IN}]",
        )
