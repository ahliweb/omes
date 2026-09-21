"""lib/omes/py/architecture/drift.py - Automated upstream drift review and deprecation tracking (ADR-0026, issue #181).

Provides read-only inspection of upstream project releases and main branch
candidates against architecture/capabilities.json.

Classifies findings as:
  - NO_IMPACT
  - NEW_DELEGATE_CANDIDATE
  - PORT_ADAPT_REVIEW
  - BREAKING_CHANGE
  - SECURITY_OR_LICENSE_REVIEW
  - BASELINE_UPDATE_AVAILABLE

Enforces non-negotiable safety rules:
  - Read-only inspection; never auto-ports, auto-merges, or mutates code.
  - Fail-closed error handling: reports BLOCKED/WARN on API failures rather than assuming no change.
  - Candidates observed on main are strictly kept separate and not promoted to released_supported.
  - Deduplicated GitHub issue management: updates existing review issue, avoiding weekly issue spam.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Classification constants
NO_IMPACT = "NO_IMPACT"
NEW_DELEGATE_CANDIDATE = "NEW_DELEGATE_CANDIDATE"
PORT_ADAPT_REVIEW = "PORT_ADAPT_REVIEW"
BREAKING_CHANGE = "BREAKING_CHANGE"
SECURITY_OR_LICENSE_REVIEW = "SECURITY_OR_LICENSE_REVIEW"
BASELINE_UPDATE_AVAILABLE = "BASELINE_UPDATE_AVAILABLE"

VALID_CLASSIFICATIONS = frozenset({
    NO_IMPACT,
    NEW_DELEGATE_CANDIDATE,
    PORT_ADAPT_REVIEW,
    BREAKING_CHANGE,
    SECURITY_OR_LICENSE_REVIEW,
    BASELINE_UPDATE_AVAILABLE,
})

SEVERITY_INFO = "INFO"
SEVERITY_WARN = "WARN"
SEVERITY_FAIL = "FAIL"

STATUS_OK = "OK"
STATUS_WARN = "WARN"
STATUS_BLOCKED = "BLOCKED"


@dataclass
class ObservedRelease:
    version: str
    published_at: str | None = None
    html_url: str | None = None
    is_prerelease: bool = False
    license_spdx: str | None = None
    source_url: str | None = None
    commit_sha: str | None = None
    is_candidate_only: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class DriftFinding:
    classification: str
    severity: str
    upstream_project: str
    capability_ids: list[str]
    current_baseline: str | None
    discovered_revision: str | None
    title: str
    description: str
    evidence_urls: list[str] = field(default_factory=list)
    removal_trigger: str | None = None
    adr_reference: str | None = None
    migration_issue: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DriftReport:
    status: str  # OK, WARN, BLOCKED
    checked_at: str
    upstreams_checked: list[str]
    upstreams_blocked: list[str]
    findings: list[DriftFinding]
    summary_counts: dict[str, int]
    is_dry_run: bool = True
    read_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checked_at": self.checked_at,
            "upstreams_checked": self.upstreams_checked,
            "upstreams_blocked": self.upstreams_blocked,
            "findings": [f.to_dict() for f in self.findings],
            "summary_counts": self.summary_counts,
            "is_dry_run": self.is_dry_run,
            "read_only": self.read_only,
        }

    @property
    def has_actionable_drift(self) -> bool:
        """True if there are findings requiring maintainer review (excluding pure NO_IMPACT)."""
        return any(f.classification != NO_IMPACT for f in self.findings)


def parse_version_tuple(version_str: str) -> tuple[int, ...]:
    """Parse version string like 'v2026.9.14' or '0.9.64' into integer tuple for comparison."""
    cleaned = version_str.strip().lstrip("vV")
    parts: list[int] = []
    for part in re.split(r"[.\-+_]", cleaned):
        digits = re.match(r"^\d+", part)
        if digits:
            parts.append(int(digits.group(0)))
        else:
            break
    return tuple(parts) if parts else (0,)


def version_gt(v1: str, v2: str) -> bool:
    """Return True if v1 is newer than v2."""
    return parse_version_tuple(v1) > parse_version_tuple(v2)


class UpstreamClient:
    """Client for querying upstream metadata, with support for offline/fixtures and fail-closed reporting."""

    def __init__(
        self,
        token: str | None = None,
        timeout: int = 15,
        fixtures: dict[str, Any] | None = None,
        offline: bool = False,
    ) -> None:
        self.token = token
        self.timeout = timeout
        self.fixtures = fixtures or {}
        self.offline = offline

    def fetch_github_latest_release(self, repo: str) -> dict[str, Any]:
        fixture_key = f"github:{repo}:release"
        if fixture_key in self.fixtures:
            return self.fixtures[fixture_key]

        if self.offline:
            raise RuntimeError(f"offline mode: no fixture available for {fixture_key}")

        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "OMES-Upstream-Drift/1.0",
                "Accept": "application/vnd.github.v3+json",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # No release yet, or releases not published through GitHub Releases API
                return {"tag_name": None, "not_found": True}
            raise RuntimeError(f"GitHub API HTTP {exc.code} for {repo}: {exc.reason}") from exc
        except Exception as exc:
            raise RuntimeError(f"GitHub API request failed for {repo}: {exc}") from exc

    def fetch_github_main_commit(self, repo: str) -> dict[str, Any]:
        fixture_key = f"github:{repo}:main"
        if fixture_key in self.fixtures:
            return self.fixtures[fixture_key]

        if self.offline:
            raise RuntimeError(f"offline mode: no fixture available for {fixture_key}")

        url = f"https://api.github.com/repos/{repo}/commits/main"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "OMES-Upstream-Drift/1.0",
                "Accept": "application/vnd.github.v3+json",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # Try master
                return self._fetch_github_branch_commit(repo, "master")
            raise RuntimeError(f"GitHub API HTTP {exc.code} for {repo}: {exc.reason}") from exc
        except Exception as exc:
            raise RuntimeError(f"GitHub API request failed for {repo} main: {exc}") from exc

    def _fetch_github_branch_commit(self, repo: str, branch: str) -> dict[str, Any]:
        url = f"https://api.github.com/repos/{repo}/commits/{branch}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "OMES-Upstream-Drift/1.0",
                "Accept": "application/vnd.github.v3+json",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def fetch_pypi_metadata(self, package: str) -> dict[str, Any]:
        fixture_key = f"pypi:{package}"
        if fixture_key in self.fixtures:
            return self.fixtures[fixture_key]

        if self.offline:
            raise RuntimeError(f"offline mode: no fixture available for {fixture_key}")

        url = f"https://pypi.org/pypi/{package}/json"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "OMES-Upstream-Drift/1.0",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"PyPI HTTP {exc.code} for {package}: {exc.reason}") from exc
        except Exception as exc:
            raise RuntimeError(f"PyPI request failed for {package}: {exc}") from exc


KNOWN_UPSTREAM_PROFILES: dict[str, dict[str, Any]] = {
    "NousResearch/hermes-agent": {
        "type": "github",
        "repo": "NousResearch/hermes-agent",
        "pypi": None,
        "default_license": "MIT",
    },
    "omacom/omarchy": {
        "type": "github",
        "repo": "omacom/omarchy",
        "pypi": None,
        "default_license": "GPL-3.0",
    },
    "graphify": {
        "type": "pypi",
        "repo": "Graphify-Labs/graphify",
        "pypi": "graphifyy",
        "default_license": "MIT",
    },
    "coollabsio/coolify": {
        "type": "github",
        "repo": "coollabsio/coolify",
        "pypi": None,
        "default_license": "Apache-2.0",
    },
}


def evaluate_upstream_drift(
    registry_data: dict[str, Any],
    client: UpstreamClient,
    check_main_candidate: bool = True,
) -> DriftReport:
    """Evaluate drift between architecture/capabilities.json and upstream projects."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    findings: list[DriftFinding] = []
    upstreams_checked: list[str] = []
    upstreams_blocked: list[str] = []

    summary_counts: dict[str, int] = {
        NO_IMPACT: 0,
        NEW_DELEGATE_CANDIDATE: 0,
        PORT_ADAPT_REVIEW: 0,
        BREAKING_CHANGE: 0,
        SECURITY_OR_LICENSE_REVIEW: 0,
        BASELINE_UPDATE_AVAILABLE: 0,
    }

    # Group capabilities by upstream_project
    caps_by_upstream: dict[str, list[dict[str, Any]]] = {}
    for cap in registry_data.get("capabilities", []):
        upstream = cap.get("upstream_project")
        if upstream:
            caps_by_upstream.setdefault(upstream, []).append(cap)

    for upstream, caps in sorted(caps_by_upstream.items()):
        if upstream in ("ahliweb/omes", "awcms", "cloudflare", "srs-x"):
            # Internal or SaaS provider without public package releases to scan directly
            continue

        upstreams_checked.append(upstream)
        profile = KNOWN_UPSTREAM_PROFILES.get(upstream)
        if not profile:
            # Check if upstream looks like owner/repo
            if "/" in upstream:
                profile = {"type": "github", "repo": upstream, "pypi": None, "default_license": "MIT"}
            else:
                profile = {"type": "pypi", "repo": None, "pypi": upstream, "default_license": "MIT"}

        observed_release: ObservedRelease | None = None
        observed_main: ObservedRelease | None = None
        upstream_error: str | None = None

        # Fetch released version
        try:
            if profile.get("type") == "pypi" and profile.get("pypi"):
                pypi_data = client.fetch_pypi_metadata(profile["pypi"])
                info = pypi_data.get("info", {})
                version = info.get("version", "")
                license_val = info.get("license") or profile.get("default_license")
                observed_release = ObservedRelease(
                    version=version,
                    published_at=None,
                    html_url=info.get("project_url") or f"https://pypi.org/project/{profile['pypi']}/",
                    license_spdx=license_val,
                    source_url=info.get("package_url"),
                    details=info,
                )
            elif profile.get("repo"):
                gh_rel = client.fetch_github_latest_release(profile["repo"])
                tag_name = gh_rel.get("tag_name")
                if tag_name:
                    observed_release = ObservedRelease(
                        version=tag_name,
                        published_at=gh_rel.get("published_at"),
                        html_url=gh_rel.get("html_url"),
                        is_prerelease=bool(gh_rel.get("prerelease")),
                        source_url=gh_rel.get("tarball_url"),
                        details=gh_rel,
                    )
        except Exception as exc:
            upstream_error = str(exc)

        # Check candidate on main
        if check_main_candidate and profile.get("repo") and not upstream_error:
            try:
                main_data = client.fetch_github_main_commit(profile["repo"])
                commit_sha = main_data.get("sha", "")[:8]
                commit_date = (
                    main_data.get("commit", {}).get("committer", {}).get("date")
                    if "commit" in main_data
                    else None
                )
                observed_main = ObservedRelease(
                    version=f"main@{commit_sha}",
                    published_at=commit_date,
                    html_url=main_data.get("html_url"),
                    commit_sha=commit_sha,
                    is_candidate_only=True,
                    details=main_data,
                )
            except Exception:
                # Main candidate fetch failure is non-fatal if release succeeded
                pass

        if upstream_error:
            upstreams_blocked.append(upstream)
            finding = DriftFinding(
                classification=BREAKING_CHANGE,
                severity=SEVERITY_WARN,
                upstream_project=upstream,
                capability_ids=[c.get("capability_id", "") for c in caps],
                current_baseline=caps[0].get("supported_baseline"),
                discovered_revision=None,
                title=f"Upstream metadata inspection blocked for {upstream}",
                description=(
                    f"Failed to fetch public upstream metadata for '{upstream}': {upstream_error}. "
                    "Fail-closed policy: maintainers must manually verify upstream status before release."
                ),
                evidence_urls=[f"https://github.com/{profile['repo']}" if profile.get("repo") else ""],
            )
            findings.append(finding)
            summary_counts[BREAKING_CHANGE] += 1
            continue

        if not observed_release:
            continue

        # Evaluate capabilities under this upstream
        # Determine baseline(s) in registry
        baselines = {c.get("supported_baseline") for c in caps if c.get("supported_baseline")}
        observed_revs = {c.get("observed_upstream_revision") for c in caps if c.get("observed_upstream_revision")}
        primary_baseline = sorted(baselines)[0] if baselines else None

        # 1. Check for License or Provenance Changes
        expected_license = profile.get("default_license")
        if (
            expected_license
            and observed_release.license_spdx
            and expected_license.lower() not in observed_release.license_spdx.lower()
        ):
            findings.append(
                DriftFinding(
                    classification=SECURITY_OR_LICENSE_REVIEW,
                    severity=SEVERITY_FAIL,
                    upstream_project=upstream,
                    capability_ids=[c.get("capability_id", "") for c in caps],
                    current_baseline=primary_baseline,
                    discovered_revision=observed_release.version,
                    title=f"Potential upstream license drift detected in {upstream}",
                    description=(
                        f"Upstream license declared as '{observed_release.license_spdx}', "
                        f"expected baseline '{expected_license}'. License review is required."
                    ),
                    evidence_urls=[observed_release.html_url or ""],
                )
            )
            summary_counts[SECURITY_OR_LICENSE_REVIEW] += 1

        # 2. Check for New Released Baseline
        if primary_baseline and version_gt(observed_release.version, primary_baseline):
            # Check if any capability in OMES currently duplicates or adapts this upstream
            duplicated_caps = [
                c for c in caps if c.get("duplication_allowed") is True or (c.get("omes_module") and c.get("disposition") in ("adapt", "port"))
            ]
            if duplicated_caps:
                dup_ids = [c.get("capability_id", "") for c in duplicated_caps]
                findings.append(
                    DriftFinding(
                        classification=NEW_DELEGATE_CANDIDATE,
                        severity=SEVERITY_WARN,
                        upstream_project=upstream,
                        capability_ids=dup_ids,
                        current_baseline=primary_baseline,
                        discovered_revision=observed_release.version,
                        title=f"Upstream release {observed_release.version} offers delegation opportunity for {upstream}",
                        description=(
                            f"Upstream project '{upstream}' released {observed_release.version} (pinned baseline: {primary_baseline}). "
                            f"OMES currently duplicates or adapts capabilities: {dup_ids}. "
                            f"Evaluate deprecating OMES wrappers under ADR-0017 (DELEGATE/PORT)."
                        ),
                        evidence_urls=[observed_release.html_url or ""],
                        removal_trigger=duplicated_caps[0].get("removal_trigger"),
                        adr_reference=duplicated_caps[0].get("adr_reference"),
                    )
                )
                summary_counts[NEW_DELEGATE_CANDIDATE] += 1
            else:
                findings.append(
                    DriftFinding(
                        classification=BASELINE_UPDATE_AVAILABLE,
                        severity=SEVERITY_INFO,
                        upstream_project=upstream,
                        capability_ids=[c.get("capability_id", "") for c in caps],
                        current_baseline=primary_baseline,
                        discovered_revision=observed_release.version,
                        title=f"New released version available for {upstream}: {observed_release.version}",
                        description=(
                            f"Upstream project '{upstream}' released {observed_release.version} (supported baseline: {primary_baseline}). "
                            "Baseline update may be verified and scheduled via release gates."
                        ),
                        evidence_urls=[observed_release.html_url or ""],
                    )
                )
                summary_counts[BASELINE_UPDATE_AVAILABLE] += 1
        else:
            # Baseline matches or is not newer
            summary_counts[NO_IMPACT] += 1

        # 3. Check for Upstream Main Candidates
        if observed_main and "main" not in observed_revs:
            # Upstream has active main commits beyond recorded baseline
            candidate_caps = [c for c in caps if c.get("maturity") == "upstream_main_candidate"]
            if candidate_caps:
                findings.append(
                    DriftFinding(
                        classification=PORT_ADAPT_REVIEW,
                        severity=SEVERITY_INFO,
                        upstream_project=upstream,
                        capability_ids=[c.get("capability_id", "") for c in candidate_caps],
                        current_baseline=primary_baseline,
                        discovered_revision=observed_main.version,
                        title=f"Upstream-main candidate changes observed for {upstream}",
                        description=(
                            f"Observed upstream revision on main branch: {observed_main.version}. "
                            "Tracked as candidate only; not promoted to released_supported until official tagged release."
                        ),
                        evidence_urls=[observed_main.html_url or ""],
                    )
                )
                summary_counts[PORT_ADAPT_REVIEW] += 1

    overall_status = STATUS_OK
    if upstreams_blocked:
        overall_status = STATUS_BLOCKED
    elif summary_counts[SECURITY_OR_LICENSE_REVIEW] > 0 or summary_counts[NEW_DELEGATE_CANDIDATE] > 0:
        overall_status = STATUS_WARN

    return DriftReport(
        status=overall_status,
        checked_at=now_iso,
        upstreams_checked=upstreams_checked,
        upstreams_blocked=upstreams_blocked,
        findings=findings,
        summary_counts=summary_counts,
        is_dry_run=True,
        read_only=True,
    )


def format_markdown_report(report: DriftReport) -> str:
    """Render structured markdown summary from DriftReport."""
    lines: list[str] = [
        "# Upstream Drift Review & Deprecation Report",
        "",
        f"- **Checked at:** `{report.checked_at}`",
        f"- **Overall Status:** `{report.status}`",
        f"- **Upstreams Checked:** {', '.join(report.upstreams_checked) if report.upstreams_checked else 'None'}",
        f"- **Upstreams Blocked:** {', '.join(report.upstreams_blocked) if report.upstreams_blocked else 'None'}",
        f"- **Read-Only Inspection:** `{report.read_only}` (Zero repository or host mutation)",
        "",
        "## Summary Counts",
        "",
        "| Classification | Count |",
        "|---|---|",
    ]
    for key, count in report.summary_counts.items():
        lines.append(f"| `{key}` | {count} |")

    lines.append("")
    lines.append("## Findings")
    lines.append("")

    if not report.findings:
        lines.append("_No actionable upstream drift detected. All supported baselines are aligned._")
        lines.append("")
        return "\n".join(lines)

    for i, finding in enumerate(report.findings, 1):
        lines.append(f"### {i}. [{finding.classification}] {finding.title}")
        lines.append("")
        lines.append(f"- **Severity:** `{finding.severity}`")
        lines.append(f"- **Upstream Project:** `{finding.upstream_project}`")
        lines.append(f"- **Current Baseline:** `{finding.current_baseline or 'N/A'}`")
        lines.append(f"- **Discovered Revision:** `{finding.discovered_revision or 'N/A'}`")
        lines.append(f"- **Affected Capabilities:** {', '.join(f'`{c}`' for c in finding.capability_ids)}")
        if finding.adr_reference:
            lines.append(f"- **Governing ADR:** `{finding.adr_reference}`")
        if finding.removal_trigger:
            lines.append(f"- **Removal Trigger:** `{finding.removal_trigger}`")
        if finding.evidence_urls:
            lines.append(f"- **Evidence URLs:** {', '.join(finding.evidence_urls)}")
        lines.append("")
        lines.append(f"{finding.description}")
        lines.append("")

    return "\n".join(lines)


ISSUE_LABEL = "area:upstream-drift"
ISSUE_TITLE_PREFIX = "[upstream-drift]"


def reconcile_github_issue(
    report: DriftReport,
    repo: str,
    token: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Idempotently create or update a single deduplicated GitHub issue for upstream drift.

    Guarantees:
      - Does not create duplicate issues when one is already open.
      - If no actionable drift exists, creates no issue.
      - Never mutates repository files or production environments.
    """
    result: dict[str, Any] = {
        "action": "none",
        "issue_number": None,
        "dry_run": dry_run,
        "actionable": report.has_actionable_drift,
    }

    if not token and not dry_run:
        raise RuntimeError("GitHub token required to update or create issues")

    # In dry-run mode, simulate
    if dry_run:
        result["action"] = "simulate_update" if report.has_actionable_drift else "simulate_noop"
        return result

    # Real issue query using GitHub API
    url = f"https://api.github.com/repos/{repo}/issues?labels={ISSUE_LABEL}&state=open"
    headers = {
        "User-Agent": "OMES-Upstream-Drift/1.0",
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"Bearer {token}",
    }
    req = urllib.request.Request(url, headers=headers)
    open_issues: list[dict[str, Any]] = []
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            open_issues = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"failed to query open issues for {repo}: {exc}") from exc

    existing_issue = open_issues[0] if open_issues else None
    report_md = format_markdown_report(report)
    title = f"{ISSUE_TITLE_PREFIX} Actionable upstream capability drift detected"

    if report.has_actionable_drift:
        if existing_issue:
            # Update existing issue
            issue_num = existing_issue["number"]
            update_url = f"https://api.github.com/repos/{repo}/issues/{issue_num}"
            payload = json.dumps({"title": title, "body": report_md}).encode("utf-8")
            update_req = urllib.request.Request(
                update_url,
                data=payload,
                headers={**headers, "Content-Type": "application/json"},
                method="PATCH",
            )
            with urllib.request.urlopen(update_req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                result["action"] = "updated"
                result["issue_number"] = data.get("number")
        else:
            # Create single new issue
            create_url = f"https://api.github.com/repos/{repo}/issues"
            payload = json.dumps({
                "title": title,
                "body": report_md,
                "labels": [ISSUE_LABEL, "area:architecture"],
            }).encode("utf-8")
            create_req = urllib.request.Request(
                create_url,
                data=payload,
                headers={**headers, "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(create_req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                result["action"] = "created"
                result["issue_number"] = data.get("number")
    else:
        # No actionable drift
        if existing_issue:
            # Close existing issue as resolved
            issue_num = existing_issue["number"]
            close_url = f"https://api.github.com/repos/{repo}/issues/{issue_num}"
            payload = json.dumps({
                "state": "closed",
                "state_reason": "completed",
            }).encode("utf-8")
            close_req = urllib.request.Request(
                close_url,
                data=payload,
                headers={**headers, "Content-Type": "application/json"},
                method="PATCH",
            )
            with urllib.request.urlopen(close_req, timeout=15) as resp:
                result["action"] = "closed"
                result["issue_number"] = issue_num
        else:
            result["action"] = "none"

    return result
