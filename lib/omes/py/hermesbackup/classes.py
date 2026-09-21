"""hermesbackup.classes - Hermes data-class -> path mapping (issue #82).

Paths are relative to $HERMES_HOME (default ~/.hermes) and verified
against the upstream Hermes documentation:

  https://hermes-agent.nousresearch.com/docs/user-guide/configuration
  https://hermes-agent.nousresearch.com/docs/

(fetched 2026-09-19). Every path below is one the fetched documentation
names explicitly. Where a documented file/directory did not exist on a
given install (e.g. verification_evidence.db when verify_on_stop is
disabled), it is silently skipped rather than treated as an error - see
Manifest.build in manifest.py.

If a future Hermes release renames or adds paths, update this mapping and
docs/hermes-backup.md together; do not guess undocumented paths.
"""
from __future__ import annotations

# Recovery classes defined under ADR-0020 (issue #176):
RECOVERY_CLASSES = ("portable-profile", "full-runtime-dr", "omes-host")
DEFAULT_RECOVERY_CLASS = "portable-profile"


def validate_recovery_class(name: str) -> str:
    if name not in RECOVERY_CLASSES:
        raise ValueError(
            f"unknown recovery class '{name}' (valid: {', '.join(RECOVERY_CLASSES)})"
        )
    return name


# Every class name accepted by --class (retained for legacy archive compatibility).
# Order here is the canonical display/report order.
CLASS_NAMES = ("config", "skills", "memory", "sessions", "runtime-state", "secrets")

# Classes included in `omes agent-backup create` when --class is not
# given at all (issue #82 acceptance criterion: "Memory, skills, sessions,
# and runtime state require explicit, visible opt-in where appropriate").
DEFAULT_CLASSES = ("config", "skills")

# Relative paths (files or directories) per class, under $HERMES_HOME.
CLASS_PATHS = {
    "config": (
        "config.yaml",   # primary settings: model, terminal backend, compression, toolsets
        "SOUL.md",       # primary agent identity (slot #1 in system prompt)
    ),
    "skills": (
        "skills",        # agent-created skills (managed via skill_manage tool)
    ),
    "memory": (
        "memories",      # persistent memory storage, incl. MEMORY.md and USER.md
    ),
    "sessions": (
        "sessions",      # gateway sessions storage
        "state.db",      # SQLite: sessions, messages, gateway routing
    ),
    "runtime-state": (
        "logs",                     # errors.log, gateway.log (secrets auto-redacted upstream)
        "cache",                    # e.g. cache/terminal - session temp artifacts
        "cron",                     # scheduled jobs
        "state-snapshots",          # pre-update backup snapshots (Hermes's own, not OMES's)
        "modal_snapshots.json",     # Modal backend filesystem snapshots
        "verification_evidence.db",  # coding verification ledger (when verify_on_stop enabled)
    ),
    "secrets": (
        ".env",          # API keys, secrets, environment variables
        "auth.json",      # OAuth provider credentials (Nous Portal, etc.)
    ),
}

# Note: Hermes's own "backups/" directory (full HERMES_HOME zips + config
# history) is intentionally NOT included in any class above - backing up
# Hermes's own backups would duplicate data indefinitely across nested
# backup generations. An operator who wants that can add it manually via
# a custom path outside this tool's scope.


def validate_class_name(name: str) -> str:
    if name not in CLASS_NAMES:
        raise ValueError(
            f"unknown backup class '{name}' (valid: {', '.join(CLASS_NAMES)})"
        )
    return name


def resolve_classes(requested):
    """Validates and returns a de-duplicated, canonically ordered tuple of
    class names. `requested` may be None/empty (-> DEFAULT_CLASSES)."""
    if not requested:
        return tuple(DEFAULT_CLASSES)
    seen = set()
    for name in requested:
        validate_class_name(name)
        seen.add(name)
    return tuple(n for n in CLASS_NAMES if n in seen)
