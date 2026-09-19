"""Telegram approval front end for the content distribution workflow
(issue #65). Stdlib only (ADR-0012).

This module is **outbound only**: it sends the approval-request preview
(`notify_job`) and status replies (`status_text`/`send_message`). It
never calls Telegram's long-polling read endpoint (see
`_PROHIBITED_METHOD` below and docs/telegram-security.md section 7 for
why - that endpoint's name is intentionally not spelled out anywhere in
this file, mirroring `modules/hermes-gateway/telegram-allowlist.sh`) and
never authenticates an inbound webhook - Hermes owns Telegram
messaging/channels (AGENTS.md section 2) and receives inbound chat
commands through its own gateway. `skills/content/SKILL.md` documents
how a Hermes skill maps an inbound chat command (`/approve <job>`, etc.)
to `omes content <verb> <job> --actor <telegram-user-id> --channel
telegram`; this module only implements the OMES side of that contract:
authorization (`is_authorized_approver`) and the outbound message calls.

Token handling mirrors `modules/hermes-gateway/telegram-allowlist.sh`'s
`_ta_api_call` exactly: `TELEGRAM_BOT_TOKEN` is read from
`$HERMES_HOME/.env` in-process, the only place the token ever appears is
inside a `0600` temp `curl -K` config file created immediately before the
call and deleted immediately after - never in argv, never in a printed/
logged URL, never in this module's return values (redacted defensively
too, matching workers/base.py's redaction rule).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

_SECRET_KEY_RE = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|COOKIE)", re.IGNORECASE)

DEFAULT_API_BASE = "https://api.telegram.org"

# Telegram's long-polling read endpoint. Deliberately referenced only as
# a rejected/forbidden name, never called - see
# docs/telegram-security.md section 7 for why (409 conflict, steals
# updates from a running poller). This module must never construct a URL
# containing this string; the grep-guard test asserts that directly on
# this file's source rather than only on runtime behavior.
_PROHIBITED_METHOD = "get" + "Updates"


class TelegramError(Exception):
    """Base class for telegram.py errors."""


def _hermes_home() -> Path:
    override = os.environ.get("HERMES_HOME") or os.environ.get("OMES_HERMES_HOME")
    return Path(override).expanduser() if override else Path.home() / ".hermes"


def _env_file() -> Path:
    return _hermes_home() / ".env"


def _env_get(key: str, file: Path) -> str | None:
    """Mirrors telegram-allowlist.sh's `_ta_env_get`: last occurrence of
    `KEY=...` wins; returns None if the key or file is absent."""
    if not file.is_file():
        return None
    value = None
    try:
        with file.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith(f"{key}="):
                    value = line[len(key) + 1 :].rstrip("\n")
    except OSError:
        return None
    return value


def _api_base() -> str:
    return os.environ.get("OMES_CONTENT_TELEGRAM_API_BASE") or DEFAULT_API_BASE


def _read_bot_token() -> str:
    token = _env_get("TELEGRAM_BOT_TOKEN", _env_file())
    if not token:
        raise TelegramError(f"TELEGRAM_BOT_TOKEN is not set in {_env_file()}")
    return token


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if _SECRET_KEY_RE.search(str(k)) else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _api_call(
    method: str,
    form_fields: dict[str, str] | None = None,
    file_field: str | None = None,
    file_path: str | None = None,
    timeout: int = 15,
) -> dict[str, Any]:
    """Calls `<base>/bot<TOKEN>/<method>` without ever placing the token
    in argv or a logged URL - see module docstring. `form_fields`/
    `file_field`+`file_path` become `-F`-style multipart form fields via
    a `curl -K` config file's `form = "..."` lines."""
    if method == _PROHIBITED_METHOD:
        raise TelegramError(f"{_PROHIBITED_METHOD} is prohibited (docs/telegram-security.md section 7)")

    if not shutil.which("curl"):
        raise TelegramError("curl is required for the Telegram API but was not found")

    token = _read_bot_token()
    base = _api_base()

    fd, cfg_path = tempfile.mkstemp(prefix="omes-content-telegram-")
    try:
        os.chmod(cfg_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f'url = "{base}/bot{token}/{method}"\n')
            fh.write("silent\n")
            fh.write("show-error\n")
            fh.write(f"max-time = {int(timeout)}\n")
            for key, value in (form_fields or {}).items():
                escaped = str(value).replace('"', '\\"')
                fh.write(f'form = "{key}={escaped}"\n')
            if file_field and file_path:
                fh.write(f'form = "{file_field}=@{file_path}"\n')

        try:
            proc = subprocess.run(
                ["curl", "-K", cfg_path],
                capture_output=True,
                text=True,
                timeout=timeout + 5,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TelegramError("Telegram API call timed out") from exc
    finally:
        try:
            os.unlink(cfg_path)
        except OSError:
            pass

    if proc.returncode != 0:
        raise TelegramError(f"curl exited {proc.returncode}: {proc.stderr.strip()}")

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise TelegramError("Telegram API returned non-JSON output") from exc

    return _redact(result)


def send_message(chat_id: str | int, text: str) -> dict[str, Any]:
    return _api_call("sendMessage", form_fields={"chat_id": str(chat_id), "text": text})


def send_photo(chat_id: str | int, photo_path: str, caption: str) -> dict[str, Any]:
    return _api_call(
        "sendPhoto",
        form_fields={"chat_id": str(chat_id), "caption": caption},
        file_field="photo",
        file_path=photo_path,
    )


# ---------------------------------------------------------------------------
# Thumbnails (opt-in, ffmpeg only, never required)
# ---------------------------------------------------------------------------


def make_thumbnail(source_path: str | Path, out_path: str | Path, timeout: int = 10) -> bool:
    """Generates a small preview frame from a video source via ffmpeg, if
    ffmpeg is installed. Returns False (never raises) when ffmpeg is
    unavailable or extraction fails, so a caller always falls back to a
    text-only preview - a thumbnail is a nice-to-have, never a
    requirement (issue #65: "media preview only as a small thumbnail...
    if ffmpeg is available, otherwise text only")."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source_path),
                "-frames:v",
                "1",
                "-vf",
                "scale=320:-1",
                str(out_path),
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and Path(out_path).is_file()


# ---------------------------------------------------------------------------
# Preview / status text - never includes secrets or session paths
# ---------------------------------------------------------------------------


def build_preview_text(record: dict[str, Any]) -> str:
    """Caption + target platforms + planned actions + artifact hash, per
    issue #65's acceptance criteria. Never includes anything from
    content/sessions/ (this function never reads that directory and is
    never given a path into it - it only reads the job record)."""
    caption = record["plan"].get("caption") or "(no caption set)"
    targets = record["plan"].get("targets") or []
    lines = [
        f"Content approval requested: {record['job_id']}",
        f"State: {record['state']}",
        f"Artifact sha256: {record['source']['sha256']}",
        f"Planned targets: {', '.join(targets) if targets else '(none set)'}",
        "Caption:",
        caption,
        "",
        "Reply via the content skill: approve | reject | edit | retry | cancel | status " + record["job_id"],
    ]
    return "\n".join(lines)


def status_text(record: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Job {record['job_id']}: {record['state']}",
            f"Platform: {record.get('platform') or '(none)'}",
            f"Resulting URL: {record['publish'].get('resulting_url') or '(none)'}",
            f"Attempts: {record['publish'].get('attempts', 0)}",
        ]
    )


# ---------------------------------------------------------------------------
# Approver authorization (must be a subset of TELEGRAM_ALLOWED_USERS)
# ---------------------------------------------------------------------------


def _parse_csv_ids(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def load_approvers() -> set[str]:
    """OMES_CONTENT_APPROVERS: comma-separated numeric Telegram user ids
    this OMES install treats as authorized content approvers. Read from
    the process environment (an OMES_CONTENT_* operator variable, like
    OMES_CONTENT_ROOT), not from Hermes's .env."""
    return _parse_csv_ids(os.environ.get("OMES_CONTENT_APPROVERS"))


def load_telegram_allowed_users() -> set[str]:
    """TELEGRAM_ALLOWED_USERS from $HERMES_HOME/.env - Hermes's own DM
    allowlist (docs/telegram-security.md section 3). Read by key only, as
    instructed - this never reads TELEGRAM_BOT_TOKEN or any other key
    from that file."""
    return _parse_csv_ids(_env_get("TELEGRAM_ALLOWED_USERS", _env_file()))


def authorized_approvers() -> set[str]:
    """The effective authorized-approver set: OMES_CONTENT_APPROVERS
    intersected with TELEGRAM_ALLOWED_USERS. An id present in
    OMES_CONTENT_APPROVERS but NOT in TELEGRAM_ALLOWED_USERS is never
    authorized - fail closed on the "must be a subset" rule rather than
    trusting OMES_CONTENT_APPROVERS alone."""
    return load_approvers() & load_telegram_allowed_users()


def is_authorized_approver(actor: str) -> bool:
    return str(actor) in authorized_approvers()


# ---------------------------------------------------------------------------
# Notify (send the approval request)
# ---------------------------------------------------------------------------


def notify_job(record: dict[str, Any], root, chat_id: str | int) -> dict[str, Any]:
    """Sends the approval-request preview for `record` to `chat_id`.
    Attempts a small ffmpeg-generated thumbnail from the job's
    processing/ source only if ffmpeg is installed; falls back to a
    text-only message otherwise. Never reads content/sessions/."""
    from . import paths  # local import: keep this module importable standalone

    text = build_preview_text(record)
    source_path = paths.job_processing_dir(record["job_id"], root)
    candidates = list(source_path.glob("source.*")) if source_path.is_dir() else []

    if candidates:
        tmp_dir = Path(tempfile.mkdtemp(prefix="omes-content-telegram-thumb-"))
        thumb_path = tmp_dir / "thumb.jpg"
        try:
            if make_thumbnail(candidates[0], thumb_path):
                result = send_photo(chat_id, str(thumb_path), text)
                return {"sent": True, "thumbnail_used": True, "result": result}
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    result = send_message(chat_id, text)
    return {"sent": True, "thumbnail_used": False, "result": result}
