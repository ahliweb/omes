"""lib/omes/py/domains/profiles/github.py - minimum GitHub App
permissions OMES needs, as DATA (issue #101).

Per AGENTS.md #101 ("prefer least-privilege GitHub App permissions") and
the issue's acceptance criteria ("minimum permissions listed"), this
module is the single place that names what OMES/awcms-one's GitHub App
installation actually needs, so a reviewer can audit it without reading
every call site. Permission names use GitHub App permission
terminology (fine-grained per-resource read/write), not OAuth scopes.

https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps
documents that a GitHub App's permissions are granted per-installation
(by the installing org/user) and enforced independently of any single
user's own access - this is why OMES must request the NARROWEST set
that supports its documented observation features (repository, commit,
release, workflow_run, deployment, environment) and nothing else.
"""
from __future__ import annotations

# read = observation only (issue #101's repository/commit/release/
# workflow_run/deployment/environment metadata); write is requested only
# where OMES/awcms-one creates a deployment status update as part of its
# own reporting - never to push code, manage branches, or alter
# repository settings.
MINIMUM_PERMISSIONS: dict[str, str] = {
    "metadata": "read",  # required by every GitHub App installation
    "contents": "read",  # commit/release metadata
    "actions": "read",  # workflow_run observations
    "deployments": "write",  # read deployment state and report OMES-driven deployment status
    "environments": "read",  # environment mapping/observation
    "checks": "read",  # CI status observation
}

# Explicitly NOT requested, and why - reviewed alongside MINIMUM_PERMISSIONS
# so a future change that widens scope is a visible diff, not a silent
# addition.
EXCLUDED_PERMISSIONS_RATIONALE: dict[str, str] = {
    "administration": "OMES never manages repository settings, branch protection, or collaborators.",
    "contents:write": "OMES never pushes code or creates commits/branches.",
    "secrets": "OMES never reads or writes repository/organization Actions secrets.",
    "issues": "Issue tracking is out of scope for the domain/billing integration.",
    "pull_requests": "PR management is out of scope for the domain/billing integration.",
}
