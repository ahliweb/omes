"""tests/py/provenance/test_release_bundle.py - unit tests for release SLSA
provenance, SBOM, and evidence bundle generation & verification (ADR-0010, issues #168, #173).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
import sys

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from provenance import release_bundle  # noqa: E402


class ReleaseBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.version = "0.3.0"
        self.tag = "v0.3.0"
        self.commit = "b0f5e2361665a04e90fe5ad0f6eb93fa91bb354c"
        self.date_str = "2026-09-21"

    def test_deterministic_generation(self) -> None:
        """Generating release bundle twice from fixed inputs produces byte-identical checksums."""
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            p1 = Path(d1)
            p2 = Path(d2)

            f1 = release_bundle.generate_bundle(
                REPO_ROOT, self.version, self.tag, self.commit, p1, date_str=self.date_str
            )
            f2 = release_bundle.generate_bundle(
                REPO_ROOT, self.version, self.tag, self.commit, p2, date_str=self.date_str
            )

            self.assertEqual(sorted(f1.keys()), sorted(f2.keys()))
            for name in f1.keys():
                h1 = release_bundle.sha256_file(f1[name])
                h2 = release_bundle.sha256_file(f2[name])
                self.assertEqual(h1, h2, f"Hash divergence for {name}")

            sums1 = (p1 / "SHA256SUMS").read_text(encoding="utf-8")
            sums2 = (p2 / "SHA256SUMS").read_text(encoding="utf-8")
            self.assertEqual(sums1, sums2)

    def test_checksum_verification_detects_tampering(self) -> None:
        """Modifying any artifact in the evidence bundle must fail verification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            release_bundle.generate_bundle(
                REPO_ROOT, self.version, self.tag, self.commit, out, date_str=self.date_str
            )

            # Initially passes
            errs = release_bundle.verify_bundle(out, expected_version=self.version, expected_commit=self.commit)
            self.assertEqual(errs, [], f"Expected clean verification, got {errs}")

            # Tamper with sbom.json
            sbom_file = out / "sbom.json"
            sbom_file.write_text(sbom_file.read_text(encoding="utf-8") + "\n# tamper\n", encoding="utf-8")

            errs = release_bundle.verify_bundle(out, expected_version=self.version, expected_commit=self.commit)
            self.assertTrue(
                any("checksum mismatch for sbom.json" in e for e in errs),
                f"Expected tampering detection for sbom.json, got: {errs}",
            )

    def test_source_commit_tag_mismatch_fails_generation(self) -> None:
        """Mismatched tag or invalid commit SHA fails generation with ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            with self.assertRaises(ValueError):
                release_bundle.generate_bundle(
                    REPO_ROOT, self.version, "v0.3.1", self.commit, out, date_str=self.date_str
                )
            with self.assertRaises(ValueError):
                release_bundle.generate_bundle(
                    REPO_ROOT, self.version, self.tag, "not-a-sha", out, date_str=self.date_str
                )

    def test_secret_canary_detection(self) -> None:
        """Secret canary injected into any artifact fails verification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            release_bundle.generate_bundle(
                REPO_ROOT, self.version, self.tag, self.commit, out, date_str=self.date_str
            )

            # Inject canary
            lim_file = out / "limitations.json"
            content = lim_file.read_text(encoding="utf-8")
            lim_file.write_text(content.replace("docker_mint_parity", "ghp_super_secret_token"), encoding="utf-8")
            # Update SHA256SUMS to isolate secret scan test from checksum test
            new_hash = release_bundle.sha256_file(lim_file)
            sums_lines = []
            for line in (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
                if "limitations.json" in line:
                    sums_lines.append(f"{new_hash}  limitations.json")
                else:
                    sums_lines.append(line)
            (out / "SHA256SUMS").write_text("\n".join(sums_lines) + "\n", encoding="utf-8")

            errs = release_bundle.verify_bundle(out)
            self.assertTrue(
                any("detected potential credential leak" in e for e in errs),
                f"Expected canary leak detection, got: {errs}",
            )

    def test_missing_compatibility_evidence_cannot_be_pass(self) -> None:
        """Tier 3 arm64 claiming PASS without evidence must fail verification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            release_bundle.generate_bundle(
                REPO_ROOT, self.version, self.tag, self.commit, out, date_str=self.date_str
            )

            compat_file = out / "compatibility-evidence.json"
            data = release_bundle.schema_mod.load_json(compat_file)
            for p in data["platforms"]:
                if p["platform"] == "arm64":
                    p["status"] = "PASS"
            import json

            compat_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            new_hash = release_bundle.sha256_file(compat_file)
            sums_lines = []
            for line in (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
                if "compatibility-evidence.json" in line:
                    sums_lines.append(f"{new_hash}  compatibility-evidence.json")
                else:
                    sums_lines.append(line)
            (out / "SHA256SUMS").write_text("\n".join(sums_lines) + "\n", encoding="utf-8")

            errs = release_bundle.verify_bundle(out)
            self.assertTrue(
                any("tier 3 arm64 cannot be rendered as PASS" in e for e in errs),
                f"Expected arm64 PASS rejection, got: {errs}",
            )


if __name__ == "__main__":
    unittest.main()
