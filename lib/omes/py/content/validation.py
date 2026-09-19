"""Platform-aware caption/cover/policy validation (issue #69). Stdlib
only (ADR-0012).

Per-platform constraints live in `lib/omes/py/content/platforms/<name>.json`
(see `generic.json` and `youtube.json`). Every numeric/boolean field in a
profile is either a bare value (author-asserted, treated as verified) or
an object `{"value": ..., "verified": bool, "source"/"note": "..."}`; a
field with `"verified": false` is never used to block a publish outright
- it only produces a `warning`, never an `error`, so an unverified,
possibly-wrong number can never silently stop a real publish or, worse,
be presented as an authoritative platform rule. `docs/content-
distribution.md` section 15 explains this rule and how to add a
platform profile responsibly.

`validate_platform()` is pure (no I/O beyond loading the profile once)
and returns a list of `ValidationIssue`. A caller (cli.py's `publish`
command) decides what to do with `error`-severity issues (block that one
platform's publish, per issue #69's acceptance criteria: "validation
failure blocks only that platform") - this module never touches job
state or the filesystem beyond the immutable `platforms/*.json` files.
"""
from __future__ import annotations

import re
import json
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_PLATFORMS_DIR = Path(__file__).resolve().parent / "platforms"

# Matches any `scheme://...` token, not only http(s), so a non-http(s)
# scheme (e.g. `ftp://`) is still *found* and can then be flagged as an
# invalid link by validate_links() rather than silently ignored.
_URL_RE = re.compile(r'\w+://[^\s)>\]"\'<]+')
_HASHTAG_RE = re.compile(r"(?<!\w)#(\w+)")


class UnknownPlatformProfileError(Exception):
    pass


class ValidationIssue:
    __slots__ = ("rule", "severity", "message", "field")

    def __init__(self, rule: str, severity: str, message: str, field: str | None = None):
        if severity not in ("error", "warning"):
            raise ValueError(f"severity must be 'error' or 'warning', got {severity!r}")
        self.rule = rule
        self.severity = severity
        self.message = message
        self.field = field

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "severity": self.severity, "message": self.message, "field": self.field}

    def __repr__(self) -> str:  # pragma: no cover - debug convenience only
        return f"ValidationIssue({self.severity!r}, {self.rule!r}, {self.message!r})"

    def __eq__(self, other):
        return isinstance(other, ValidationIssue) and self.to_dict() == other.to_dict()


def list_platform_profiles() -> list[str]:
    if not _PLATFORMS_DIR.is_dir():
        return []
    return sorted(p.stem for p in _PLATFORMS_DIR.glob("*.json"))


def load_platform_profile(platform: str) -> dict[str, Any]:
    """Loads `platforms/<platform>.json`, falling back to `generic.json`
    for a platform with no dedicated profile yet - a platform worker
    (#66) existing does not require a validation profile to also exist,
    but every platform gets at least the generic, unverified defaults."""
    path = _PLATFORMS_DIR / f"{platform}.json"
    if not path.is_file():
        path = _PLATFORMS_DIR / "generic.json"
        if not path.is_file():
            raise UnknownPlatformProfileError(f"no profile for {platform!r} and no generic.json fallback")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _value(field: Any) -> Any:
    if isinstance(field, dict) and "value" in field:
        return field["value"]
    return field


def _verified(field: Any) -> bool:
    if isinstance(field, dict):
        return bool(field.get("verified", False))
    return True  # a bare literal in a profile is author-asserted as fact


def _severity_for(field: Any) -> str:
    return "error" if _verified(field) else "warning"


def find_urls(text: str | None) -> list[str]:
    return _URL_RE.findall(text or "")


def find_hashtags(text: str | None) -> list[str]:
    return _HASHTAG_RE.findall(text or "")


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------


def validate_caption_length(caption: str | None, profile: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    caption_cfg = profile.get("caption", {})
    max_len_field = caption_cfg.get("description_max_length", caption_cfg.get("max_length"))
    if max_len_field is None:
        return issues
    max_len = _value(max_len_field)
    if max_len is None:
        return issues
    length = len(caption or "")
    if length > max_len:
        issues.append(
            ValidationIssue(
                "caption_length",
                _severity_for(max_len_field),
                f"caption is {length} characters, exceeds the "
                f"{'verified' if _verified(max_len_field) else 'unverified'} limit of {max_len}",
                field="caption",
            )
        )
    return issues


def validate_title_length(title: str | None, profile: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if title is None:
        return issues
    title_field = profile.get("caption", {}).get("title_max_length")
    if title_field is None:
        return issues
    max_len = _value(title_field)
    if max_len is None:
        return issues
    if len(title) > max_len:
        issues.append(
            ValidationIssue(
                "title_length",
                _severity_for(title_field),
                f"title is {len(title)} characters, exceeds the "
                f"{'verified' if _verified(title_field) else 'unverified'} limit of {max_len}",
                field="title",
            )
        )
    return issues


def validate_hashtags(caption: str | None, profile: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    max_count_field = profile.get("hashtags", {}).get("max_count")
    if max_count_field is None:
        return issues
    max_count = _value(max_count_field)
    if max_count is None:
        return issues
    count = len(find_hashtags(caption))
    if count > max_count:
        issues.append(
            ValidationIssue(
                "hashtag_count",
                _severity_for(max_count_field),
                f"{count} hashtags exceeds the {'verified' if _verified(max_count_field) else 'unverified'} "
                f"limit of {max_count}",
                field="caption",
            )
        )
    return issues


def validate_unsupported_claims(caption: str | None, profile: dict[str, Any]) -> list[ValidationIssue]:
    """Detects absolute/unverifiable-factual-claim language (issue #69:
    "no unverified factual claim should be auto-published"). Always
    `error` severity, regardless of profile verification status - this
    rule is about the caption's own content, not a platform limit, so
    "unverified platform data" does not apply to it."""
    issues: list[ValidationIssue] = []
    text = caption or ""
    for pattern in profile.get("unsupported_claim_patterns", []):
        if re.search(pattern, text, re.IGNORECASE):
            issues.append(
                ValidationIssue(
                    "unsupported_claim",
                    "error",
                    f"caption matches an unsupported-claim pattern ({pattern!r}); "
                    "AI-generated/operator captions are suggestions until reviewed - "
                    "no unverified factual claim may be auto-published",
                    field="caption",
                )
            )
    return issues


def validate_required_disclosures(caption: str | None, profile: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    text = caption or ""
    for disclosure in profile.get("required_disclosures", []):
        trigger = disclosure.get("trigger_pattern")
        require = disclosure.get("require_pattern")
        message = disclosure.get("message", "missing a required disclosure")
        if not trigger:
            continue
        if re.search(trigger, text, re.IGNORECASE) and not (require and re.search(require, text, re.IGNORECASE)):
            issues.append(ValidationIssue("missing_disclosure", "error", message, field="caption"))
    return issues


def _check_reachable(url: str, timeout: float) -> bool:
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - opt-in, operator-controlled
            return 200 <= response.status < 400
    except Exception:  # noqa: BLE001 - a network probe failing is never a crash
        return False


def validate_links(
    caption: str | None,
    profile: dict[str, Any],
    check_reachability: bool = False,
    timeout: float = 5.0,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    urls = find_urls(caption)

    max_count_field = profile.get("links", {}).get("max_count")
    if max_count_field is not None:
        max_count = _value(max_count_field)
        if max_count is not None and len(urls) > max_count:
            issues.append(
                ValidationIssue(
                    "link_count",
                    _severity_for(max_count_field),
                    f"{len(urls)} links exceeds the "
                    f"{'verified' if _verified(max_count_field) else 'unverified'} limit of {max_count}",
                    field="caption",
                )
            )

    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            issues.append(ValidationIssue("invalid_link_syntax", "error", f"malformed URL: {url!r}", field="caption"))
            continue
        if check_reachability and not _check_reachable(url, timeout):
            # A failed reachability probe is a warning, not an error: a
            # transient network hiccup on the validating host must never
            # silently block a publish the same way a real syntax error
            # does (issue #69: "optional HEAD check ... opt-in").
            issues.append(
                ValidationIssue(
                    "link_unreachable",
                    "warning",
                    f"URL did not respond to a HEAD request within {timeout}s: {url!r}",
                    field="caption",
                )
            )

    return issues


def validate_cover(cover_path: str | Path | None, profile: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    cover_cfg = profile.get("cover", {})

    required_field = cover_cfg.get("required")
    if required_field is not None and _value(required_field) and not cover_path:
        issues.append(
            ValidationIssue(
                "cover_required",
                _severity_for(required_field),
                "this platform's profile marks a cover image as required and none was provided",
                field="cover",
            )
        )

    if cover_path:
        formats_field = cover_cfg.get("formats")
        if formats_field is not None:
            formats = _value(formats_field)
            if formats:
                ext = Path(cover_path).suffix.lower().lstrip(".")
                if ext not in formats:
                    issues.append(
                        ValidationIssue(
                            "cover_format",
                            _severity_for(formats_field),
                            f"cover format {ext!r} is not one of {formats}",
                            field="cover",
                        )
                    )

    return issues


def validate_platform(
    caption: str | None,
    profile: dict[str, Any],
    title: str | None = None,
    cover_path: str | Path | None = None,
    check_link_reachability: bool = False,
) -> list[ValidationIssue]:
    """Runs every rule and returns the combined issue list. Never raises
    on a "bad" caption/cover - only on a malformed profile (missing
    generic.json), which is a repository defect, not operator input."""
    issues: list[ValidationIssue] = []
    issues += validate_caption_length(caption, profile)
    issues += validate_title_length(title, profile)
    issues += validate_hashtags(caption, profile)
    issues += validate_unsupported_claims(caption, profile)
    issues += validate_required_disclosures(caption, profile)
    issues += validate_links(caption, profile, check_reachability=check_link_reachability)
    issues += validate_cover(cover_path, profile)
    return issues


def has_blocking_issues(issues: list[ValidationIssue]) -> bool:
    return any(i.severity == "error" for i in issues)
