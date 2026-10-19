#!/usr/bin/env python3
"""Decide whether a release commit has acceptable CI evidence (issue #315).

Used only by scripts/release.sh. The GitHub Actions workflows are manual-only
(issue #312), so "no failing check run" no longer means "CI passed": a commit
nobody dispatched the workflows on has zero check runs. This gate therefore
fails CLOSED. It accepts a release commit only when at least one of two
evidence sources proves that EVERY blocking job passed on that exact commit.

Rule that overrides both sources: any COMPLETED check run on the release commit
with conclusion failure, timed_out or action_required blocks the release,
whichever app posted it and whether or not its job is required (a failing
GitGuardian run means a leaked secret), even when valid local evidence exists.
The one exception is a failed GitHub Actions run of a known advisory workflow
job (continue-on-error by design). A still-running run that is not a required
job only produces a warning. Output starts with `BLOCKED` in that case.

Source 1 - GitHub check runs (`gh api .../commits/<sha>/check-runs`, fetched by
release.sh): for every blocking lint.yml / compatibility.yml job there is a
check run with the job's display name, created by GitHub Actions for this
commit, with status `completed` and conclusion `success`. A job that is
missing, failed, cancelled, skipped, neutral or still running is no evidence.

Source 2 - a local CI evidence file produced by
`scripts/ci-local.sh --profile full --json` (self-reported, bound to the
commit SHA, not enforced by GitHub). It must be strictly valid JSON for exactly
this commit with a clean tree, a non-shallow clone, result `pass`, exit code 0,
no failed or skipped blocking job, every blocking job of the full profile
present and `pass`, and a `generated_at` that is not in the future.

The required job list comes from `scripts/ci-local.sh --list --json --profile
full` (its job table, which its own workflow-sync job keeps equal to the
workflow YAML); this script never parses workflow YAML itself.

Usage:
  release-ci-gate.py --sha SHA --jobs LIST_JSON [--checks FILE | --checks-note TEXT]
                     [--evidence PATH]

Exit status: 0 evidence accepted, 1 no acceptable evidence, 2 usage or internal
error (callers must treat it as a failed gate). stdout is a short report:
`PASS <source>`, `BLOCKED` or `FAIL` followed by one `github:` / `local:` line per reason.
No token, header or raw API response is ever printed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

EVIDENCE_SCHEMA = "omes-ci-local/1"
LIST_SCHEMA = "omes-ci-local/1"
# Workflow files whose blocking jobs gate a release (the GitHub source).
GATED_WORKFLOWS = ("lint.yml", "compatibility.yml")
# Clock-skew allowance between the machine that wrote the evidence and this one.
FUTURE_SKEW_SECONDS = 300
MAX_JSON_BYTES = 4 * 1024 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
JOB_STATUSES = {"pass", "fail", "advisory_fail", "skipped"}
ACTIONS_APP_SLUG = "github-actions"
# A completed check run with one of these conclusions blocks a release (see scan_runs).
BLOCKING_CONCLUSIONS = {"failure", "timed_out", "action_required"}


class GateError(Exception):
    """Internal or usage error (exit status 2)."""


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate JSON key {key!r}")
        out[key] = value
    return out


def _reject_constant(name: str) -> object:
    raise ValueError(f"non-standard JSON constant {name}")


def strict_loads(text: str) -> object:
    return json.loads(text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)


def read_text(path: Path) -> str:
    if not path.is_file():
        raise OSError("not a regular file")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise OSError(f"larger than {MAX_JSON_BYTES} bytes")
    return path.read_text(encoding="utf-8")


def load_required(path: Path) -> tuple[list[str], list[str], list[str], set[str]]:
    """Return (local job names, GitHub check names, GitHub-name problems, advisory check names)."""
    try:
        data = strict_loads(read_text(path))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise GateError(f"cannot read the required-job list {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != LIST_SCHEMA or not isinstance(data.get("jobs"), list):
        raise GateError("the required-job list is not scripts/ci-local.sh --list --json output")
    local: list[str] = []
    github: list[str] = []
    problems: list[str] = []
    advisory: set[str] = set()
    for job in data["jobs"]:
        if not isinstance(job, dict):
            raise GateError("the required-job list holds a non-object job")
        if job.get("blocking") is False and job.get("workflow") != "-":
            check = job.get("check_name")
            if isinstance(check, str) and check:
                advisory.add(check)
        if job.get("selected") is not True or job.get("blocking") is not True:
            continue
        name = job.get("name")
        if not isinstance(name, str) or not name:
            raise GateError("the required-job list holds a job without a name")
        local.append(name)
        workflow = job.get("workflow")
        if workflow in GATED_WORKFLOWS:
            check = job.get("check_name")
            if isinstance(check, str) and check:
                github.append(check)
            else:
                problems.append(f"{name}: no GitHub check-run name could be derived from {workflow}")
    if not local:
        raise GateError("the required-job list names no blocking job")
    return local, github, problems, advisory


def parse_documents(text: str) -> list[object]:
    """Parse one or more concatenated JSON documents (`gh api --paginate`)."""
    decoder = json.JSONDecoder(object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
    docs: list[object] = []
    pos = 0
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        doc, pos = decoder.raw_decode(text, pos)
        docs.append(doc)
    return docs


def describe_run(run: dict) -> str:
    status = run.get("status")
    if status != "completed":
        return f"not finished (status: {status})"
    return f"completed with conclusion {run.get('conclusion')}"


def scan_runs(sha: str, required: list[str], advisory: set[str], checks: Path | None) -> tuple[list[str], list[str]]:
    """Return (blockers, warnings) from EVERY check run on the commit.

    A completed run with conclusion failure, timed_out or action_required blocks
    the release whichever app posted it and whether or not its job is required
    (a failing GitGuardian run means a leaked secret). The only exception is a
    failed GitHub Actions run of a known advisory workflow job (continue-on-error
    by design: shfmt, Tier 2, graphify smoke). A run that is still running and is
    not a required job only warns. An unreadable response yields nothing here:
    evaluate_github already reports it.
    """
    if checks is None:
        return [], []
    try:
        docs = parse_documents(read_text(checks))
    except (OSError, ValueError, UnicodeDecodeError):
        return [], []
    blockers: list[str] = []
    warnings: list[str] = []
    for doc in docs:
        if not isinstance(doc, dict) or not isinstance(doc.get("check_runs"), list):
            continue
        for run in doc["check_runs"]:
            if not isinstance(run, dict):
                continue
            head = run.get("head_sha")
            if head is not None and head != sha:
                continue
            name = str(run.get("name"))
            app = run.get("app") if isinstance(run.get("app"), dict) else {}
            slug = app.get("slug")
            if run.get("status") == "completed":
                if run.get("conclusion") in BLOCKING_CONCLUSIONS:
                    if slug == ACTIONS_APP_SLUG and name in advisory:
                        continue
                    blockers.append(f"{name}: completed with conclusion {run.get('conclusion')} (app: {slug})")
            elif name not in required:
                warnings.append(f"{name}: not finished (status: {run.get('status')}, app: {slug}); it does not block the release")
    return blockers, warnings


def evaluate_github(sha: str, required: list[str], name_problems: list[str], checks: Path | None, note: str) -> list[str]:
    """Return the reasons the GitHub check runs are NOT acceptable (empty = accepted)."""
    if checks is None:
        return [note or "GitHub check runs were not queried"]
    reasons: list[str] = list(name_problems)
    if not required:
        reasons.append("no GitHub check-run name could be derived for any blocking job")
    try:
        docs = parse_documents(read_text(checks))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return [f"the check-runs response could not be read: {exc}"] + reasons
    if not docs:
        return ["the check-runs response was empty"] + reasons
    runs: list[dict] = []
    total = 0
    for doc in docs:
        if not isinstance(doc, dict) or not isinstance(doc.get("check_runs"), list):
            return ["the check-runs response is not a GitHub check-runs document"] + reasons
        if _is_int(doc.get("total_count")):
            total = max(total, doc["total_count"])
        runs.extend(r for r in doc["check_runs"] if isinstance(r, dict))
    if total > len(runs):
        reasons.append(f"the check-runs response is incomplete ({len(runs)} of {total} runs)")
    if not runs:
        reasons.append("the commit has no GitHub check runs (the workflows are manual-only and were never dispatched on it)")

    for check in required:
        named = [r for r in runs if r.get("name") == check]
        actions = [
            r
            for r in named
            if isinstance(r.get("app"), dict) and r["app"].get("slug") == ACTIONS_APP_SLUG and r.get("head_sha") == sha
        ]
        if not actions:
            if named:
                reasons.append(f"{check}: only check runs that are not GitHub Actions runs for this commit")
            else:
                reasons.append(f"{check}: missing (no check run)")
            continue
        for run in actions:
            if run.get("status") != "completed" or run.get("conclusion") != "success":
                reasons.append(f"{check}: {describe_run(run)}")
                break
    return reasons


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def evaluate_evidence(sha: str, required: list[str], path: Path | None) -> list[str]:
    """Return the reasons the local evidence file is NOT acceptable (empty = accepted)."""
    if path is None:
        return ["no local CI evidence file was given"]
    if not path.exists():
        return [f"no local CI evidence file at {path}"]
    try:
        data = strict_loads(read_text(path))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return [f"{path} is not valid evidence: {exc}"]
    if not isinstance(data, dict):
        return [f"{path} is not a JSON object"]

    reasons: list[str] = []
    if data.get("schema") != EVIDENCE_SCHEMA:
        reasons.append(f"schema is not {EVIDENCE_SCHEMA}")
    if data.get("result") != "pass":
        reasons.append(f"result is {data.get('result')!r}, not 'pass'")
    if not _is_int(data.get("exit_code")) or data.get("exit_code") != 0:
        reasons.append("exit_code is not 0")
    if data.get("profile") != "full":
        reasons.append(f"profile is {data.get('profile')!r}, not 'full' (run scripts/ci-local.sh --profile full --json)")

    git = data.get("git")
    if not isinstance(git, dict):
        reasons.append("git information is missing")
    else:
        head = git.get("head")
        if not isinstance(head, str) or not SHA_RE.match(head):
            reasons.append("git.head is not a full commit SHA")
        elif head != sha:
            reasons.append(f"evidence is for commit {head[:12]}, not the release commit {sha[:12]}")
        if git.get("dirty") is not False:
            reasons.append("the working tree was not clean when the evidence was produced")
        if git.get("shallow") is not False:
            reasons.append("the clone was shallow (or its depth is unknown) when the evidence was produced")

    stamp = data.get("generated_at")
    if not isinstance(stamp, str) or not TIMESTAMP_RE.match(stamp):
        reasons.append("generated_at is missing or not a UTC timestamp (YYYY-MM-DDTHH:MM:SSZ)")
    else:
        try:
            when = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            reasons.append("generated_at is not a valid date")
        else:
            limit = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=FUTURE_SKEW_SECONDS)
            if when > limit:
                reasons.append(f"generated_at {stamp} is in the future")

    counts = data.get("counts")
    if not isinstance(counts, dict):
        reasons.append("counts are missing")
    else:
        for key in ("fail", "blocking_skipped"):
            if not _is_int(counts.get(key)) or counts.get(key) != 0:
                reasons.append(f"counts.{key} is not 0")

    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        reasons.append("jobs are missing")
        return reasons
    by_name: dict[str, list[dict]] = {}
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get("name"), str):
            reasons.append("a job entry is malformed")
            continue
        status = job.get("status")
        if status not in JOB_STATUSES or not isinstance(job.get("blocking"), bool):
            reasons.append(f"job {job['name']} has an unknown status or blocking flag")
            continue
        if job["blocking"] and status != "pass":
            reasons.append(f"blocking job {job['name']} is {status}")
        by_name.setdefault(job["name"], []).append(job)

    for name in required:
        entries = by_name.get(name)
        if not entries:
            reasons.append(f"blocking job {name} is missing from the evidence")
        elif any(j.get("blocking") is not True or j.get("status") != "pass" for j in entries):
            reasons.append(f"blocking job {name} did not pass")
    return reasons


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sha", required=True, help="full release commit SHA")
    parser.add_argument("--jobs", required=True, type=Path, help="scripts/ci-local.sh --list --json --profile full output")
    parser.add_argument("--checks", type=Path, help="file holding the gh api check-runs response")
    parser.add_argument("--checks-note", default="", help="why --checks is absent (printed as the GitHub reason)")
    parser.add_argument("--evidence", type=Path, help="local CI evidence file")
    args = parser.parse_args(argv)

    if not SHA_RE.match(args.sha):
        raise GateError("--sha must be a full lowercase 40-hex commit SHA")
    local, github, name_problems, advisory = load_required(args.jobs)

    # A red check run on the exact commit contradicts any other evidence, so it
    # blocks in both evidence paths before either is looked at.
    blockers, warnings = scan_runs(args.sha, github, advisory, args.checks)
    if blockers:
        print("BLOCKED")
        for blocker in blockers:
            print(f"blocked: {blocker}")
        for warning in warnings:
            print(f"WARN {warning}")
        return 1

    gh_reasons = evaluate_github(args.sha, github, name_problems, args.checks, args.checks_note)
    if not gh_reasons:
        print("PASS github-check-runs")
        for warning in warnings:
            print(f"WARN {warning}")
        return 0
    ev_reasons = evaluate_evidence(args.sha, local, args.evidence)
    if not ev_reasons:
        print(f"PASS local-evidence {args.evidence}")
        for warning in warnings:
            print(f"WARN {warning}")
        return 0

    print("FAIL")
    print(f"required: {len(local)} blocking job(s) for the local evidence, {len(github)} GitHub check run(s)")
    for reason in gh_reasons:
        print(f"github: {reason}")
    for reason in ev_reasons:
        print(f"local: {reason}")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except GateError as exc:
        print(f"release-ci-gate: {exc}", file=sys.stderr)
        sys.exit(2)
