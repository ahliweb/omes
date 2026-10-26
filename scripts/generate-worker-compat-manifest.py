#!/usr/bin/env python3
"""scripts/generate-worker-compat-manifest.py - produce and verify the OMES
worker compatibility manifest (issue #352, ADR-0036 section 4).

The manifest (schema: contracts/compatibility/v1/worker-compatibility-manifest
.schema.json) is a non-secret, deterministic description of one OMES release:
product version, worker protocol, the pinned `contracts/control-center/v1`
worker schemas (per-file sha256 plus one digest), supported platforms and the
capability posture. It is derived only from repository facts: VERSION, the
contract files, architecture/capabilities.json, jobs/worker.py. Nothing is
invented; fields that need release infrastructure (installer asset, signature,
verified AWCMS builds) are reported as absent.

Usage:
  generate-worker-compat-manifest.py                       # unreleased manifest to stdout
  generate-worker-compat-manifest.py --out PATH
  generate-worker-compat-manifest.py --channel stable --tag v0.6.0 \\
      --release-time 2026-09-28T00:23:38Z --commit <40-hex> [--out PATH]
  generate-worker-compat-manifest.py --check               # self-consistency gate
  generate-worker-compat-manifest.py --verify FILE \\
      [--consumer-contract-version 1.0.0] [--consumer-contract-digest sha256:...]

`--check` regenerates the unreleased manifest, validates it against the schema
and recomputes the contract digest. It writes nothing and does no network I/O.
`--verify` is the reference consumer decision: it prints a JSON verdict and
exits 0 only when the manifest is compatible; every failure is fail-closed.

Stdlib only (ADR-0012). No wall-clock value is embedded: output is
byte-identical for identical inputs. Signing and publishing the manifest as a
release asset: Not implemented yet (tracked in #352).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib" / "omes" / "py"))

from jobs import schema as schema_mod  # noqa: E402
from jobs import worker as worker_mod  # noqa: E402

SCHEMA_PATH = REPO_ROOT / "contracts" / "compatibility" / "v1" / "worker-compatibility-manifest.schema.json"
CONTRACT_DIR = REPO_ROOT / "contracts" / "control-center" / "v1"
CONTRACT_AREA = "control-center"
CONTRACT_MAJOR = "v1"
# Matches the literal the worker reports in worker-heartbeat.request
# (jobs/worker.py send_heartbeat); a unit test keeps the two in step.
CONTRACT_VERSION = "1.0.0"
WORKER_PROTOCOL_VERSION = "1.0.0"
# Per-request signing is the ADR-0027 target but is not in jobs/worker.py yet;
# a unit test fails when it lands so this constant is updated deliberately.
REQUEST_SIGNING = "not_implemented"

# The files a worker or its Control Center peer must agree on byte for byte.
CONTRACT_FILES = (
    "operation-request",
    "worker-enrollment.request",
    "worker-enrollment.response",
    "worker-heartbeat.request",
    "worker-heartbeat.response",
    "worker-poll.request",
    "worker-poll.response",
    "worker-result.request",
    "worker-result.response",
)

# Mirrors lib/omes/detect.sh detect_tier (a unit test checks the versions).
PLATFORM_OS = (
    {"id": "ubuntu", "versions": ["22.04", "24.04", "26.04"]},
    {"id": "linuxmint", "versions": ["22"]},
)
PLATFORM_ARCH = ("amd64", "arm64")

_SEMVER = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contract_files(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    base = repo_root / "contracts" / CONTRACT_AREA / CONTRACT_MAJOR
    out = []
    for name in CONTRACT_FILES:
        path = base / f"{name}.schema.json"
        if not path.is_file():
            raise FileNotFoundError(f"contract file missing: {path}")
        out.append({"path": f"contracts/{CONTRACT_AREA}/{CONTRACT_MAJOR}/{name}.schema.json", "sha256": _sha256_file(path)})
    return sorted(out, key=lambda f: f["path"])


def digest_of(files: list[dict[str, str]]) -> str:
    lines = "".join(f"{f['path']}:{f['sha256']}\n" for f in sorted(files, key=lambda f: f["path"]))
    return "sha256:" + hashlib.sha256(lines.encode("utf-8")).hexdigest()


def capability_registry_digest(repo_root: Path = REPO_ROOT) -> str:
    """Same formula as jobs/worker.py send_heartbeat."""
    data = (repo_root / "architecture" / "capabilities.json").read_bytes()
    return f"sha256:{hashlib.sha256(data).hexdigest()[:16]}"


def hermes_baseline(repo_root: Path = REPO_ROOT) -> str:
    registry = json.loads((repo_root / "architecture" / "capabilities.json").read_text(encoding="utf-8"))
    for cap in registry["capabilities"]:
        if cap.get("capability_id") == "hermes.agent.reasoning":
            return str(cap["supported_baseline"])
    raise ValueError("hermes.agent.reasoning missing from architecture/capabilities.json")


def build_manifest(
    repo_root: Path = REPO_ROOT,
    channel: str = "unreleased",
    tag: str | None = None,
    release_time: str | None = None,
    commit: str | None = None,
) -> dict[str, Any]:
    version = (repo_root / "VERSION").read_text(encoding="utf-8").strip()
    if channel == "stable":
        if not (tag and release_time and commit):
            raise ValueError("stable channel requires --tag, --release-time and --commit")
        if tag != f"v{version}":
            raise ValueError(f"tag {tag} does not match VERSION {version}; generate from the tagged checkout")
    elif channel != "unreleased":
        raise ValueError(f"unknown channel {channel!r}")
    elif tag or release_time or commit:
        raise ValueError("unreleased channel must not carry tag, release time or commit")

    files = contract_files(repo_root)
    major, _minor, _patch = (int(x) for x in _SEMVER.match(CONTRACT_VERSION).groups())  # type: ignore[union-attr]
    manifest: dict[str, Any] = {
        "schema_version": "1.0.0",
        "channel": channel,
        "product_version": version,
        "worker_protocol": {
            "name": "omes-pull-worker",
            "version": WORKER_PROTOCOL_VERSION,
            "transport": "outbound_https_pull",
            "operations": ["enroll", "poll", "result", "heartbeat"],
            "request_signing": REQUEST_SIGNING,
        },
        "control_contract": {
            "area": CONTRACT_AREA,
            "major": CONTRACT_MAJOR,
            "contract_version": CONTRACT_VERSION,
            "digest": digest_of(files),
            "files": files,
        },
        "awcms_omes_control": {
            "min_contract_version": CONTRACT_VERSION,
            "max_contract_version_exclusive": f"{major + 1}.0.0",
            "verified_builds": [],
        },
        "installer": {"status": "not_published"},
        "platform_support": {"os": [dict(o) for o in PLATFORM_OS], "arch": list(PLATFORM_ARCH)},
        "capability_posture": {
            "capability_registry_digest": capability_registry_digest(repo_root),
            "worker_capabilities": list(worker_mod.SUPPORTED_CAPABILITIES),
            "hermes_baseline": hermes_baseline(repo_root),
        },
        "integrity": {"canonicalization": "json-sorted-keys-2space-lf", "signature_status": "unsigned"},
        "consumer_policy": {
            "accepted_channels": ["stable"],
            "fail_closed_when_unverifiable": True,
            "fetch_at_host_boot": False,
        },
    }
    if channel == "stable":
        manifest["release_tag"] = tag
        manifest["release_time"] = release_time
        manifest["commit_sha"] = commit
    return manifest


def render(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def validate_manifest(manifest: Any) -> list[str]:
    schema = schema_mod.load_json(SCHEMA_PATH)
    return schema_mod.validate(manifest, schema) + schema_mod.scan_for_raw_secrets(manifest)


def _ver(text: str) -> tuple[int, int, int]:
    m = _SEMVER.match(text)
    if not m:
        raise ValueError(f"not a semantic version: {text!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def evaluate_compatibility(manifest: Any, consumer: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reference consumer decision (fail closed).

    `consumer` may carry `contract_version`, `contract_digest` (the consumer's
    pinned vendored copy) and `require_installer`. Returns
    {"compatible": bool, "reason_codes": [...]}; any problem makes it
    incompatible, including an unparseable manifest.
    """
    consumer = consumer or {}
    reasons: list[str] = []
    errors = validate_manifest(manifest) if isinstance(manifest, dict) else ["not an object"]
    if errors:
        # Structure cannot be trusted; do not read further.
        code = "unknown_schema_version" if isinstance(manifest, dict) and manifest.get("schema_version") != "1.0.0" else "manifest_invalid"
        return {"compatible": False, "reason_codes": [code]}

    if manifest["channel"] != "stable":
        reasons.append("unsupported_channel")
    else:
        if not all(manifest.get(k) for k in ("release_tag", "release_time", "commit_sha")):
            reasons.append("stable_release_identity_missing")
        elif manifest["release_tag"] != f"v{manifest['product_version']}":
            reasons.append("release_tag_version_mismatch")

    contract = manifest["control_contract"]
    if digest_of(contract["files"]) != contract["digest"]:
        reasons.append("manifest_digest_inconsistent")

    rng = manifest["awcms_omes_control"]
    cv = consumer.get("contract_version")
    if cv is not None:
        try:
            if not (_ver(rng["min_contract_version"]) <= _ver(cv) < _ver(rng["max_contract_version_exclusive"])):
                reasons.append("contract_version_out_of_range")
        except ValueError:
            reasons.append("consumer_contract_version_invalid")
    pinned = consumer.get("contract_digest")
    if pinned is not None and pinned != contract["digest"]:
        reasons.append("contract_digest_mismatch")

    installer = manifest["installer"]
    if installer["status"] == "published" and not (installer.get("uri") and installer.get("sha256")):
        reasons.append("installer_incomplete")
    if consumer.get("require_installer") and installer["status"] != "published":
        reasons.append("installer_not_published")

    return {"compatible": not reasons, "reason_codes": reasons}


def check(repo_root: Path = REPO_ROOT) -> list[str]:
    problems = []
    manifest = build_manifest(repo_root)
    problems += validate_manifest(manifest)
    if render(manifest) != render(build_manifest(repo_root)):
        problems.append("generation is not deterministic")
    if digest_of(manifest["control_contract"]["files"]) != manifest["control_contract"]["digest"]:
        problems.append("contract digest does not match listed files")
    # An unreleased manifest must never be accepted by a consumer.
    verdict = evaluate_compatibility(manifest)
    if verdict["compatible"] or "unsupported_channel" not in verdict["reason_codes"]:
        problems.append("unreleased manifest was not rejected by the reference consumer")
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--channel", choices=("stable", "unreleased"), default="unreleased")
    p.add_argument("--tag")
    p.add_argument("--release-time")
    p.add_argument("--commit")
    p.add_argument("--out")
    p.add_argument("--check", action="store_true")
    p.add_argument("--verify", metavar="FILE")
    p.add_argument("--consumer-contract-version")
    p.add_argument("--consumer-contract-digest")
    p.add_argument("--require-installer", action="store_true")
    args = p.parse_args(argv)

    if args.check:
        problems = check()
        if problems:
            for item in problems:
                print(f"generate-worker-compat-manifest: {item}", file=sys.stderr)
            return 1
        print("generate-worker-compat-manifest: OK")
        return 0

    if args.verify:
        try:
            manifest = json.loads(Path(args.verify).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = None
        consumer = {
            k: v
            for k, v in (
                ("contract_version", args.consumer_contract_version),
                ("contract_digest", args.consumer_contract_digest),
                ("require_installer", args.require_installer or None),
            )
            if v is not None
        }
        verdict = evaluate_compatibility(manifest, consumer)
        print(json.dumps(verdict, sort_keys=True))
        return 0 if verdict["compatible"] else 1

    try:
        manifest = build_manifest(REPO_ROOT, args.channel, args.tag, args.release_time, args.commit)
    except (ValueError, FileNotFoundError) as exc:
        print(f"generate-worker-compat-manifest: {exc}", file=sys.stderr)
        return 1
    errors = validate_manifest(manifest)
    if errors:
        print("generate-worker-compat-manifest: generated manifest invalid: " + "; ".join(errors), file=sys.stderr)
        return 1
    text = render(manifest)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
