"""lib/omes/py/jobs/worker_signing.py - Ed25519 worker keys and request signing (ADR-0027, issue #362).

The pull worker proves possession of a locally generated Ed25519 key at enrollment and signs every
poll, result and heartbeat request. Only the OpenSSL 3 command line is used: no Python crypto
dependency is added and nothing runs through a shell (fixed argv lists, a timeout, no `shell=True`).

Guarantees:
  - The private key is created by `openssl genpkey` into a file that already exists with mode 0600,
    so no private data is ever written to a more permissive file.
  - The private key never appears in argv beyond its file path, in a payload, a header, or a log line.
  - Fail closed: a missing or too old OpenSSL raises SigningError; there is no fallback key.

Canonical string signed for every request (UTF-8, `\\n` separated, no trailing newline):

    OMES-WORKER-SIG-V1
    <METHOD>
    <PATH>
    <hex sha256 of the exact body bytes>
    <nonce>
    <timestamp>

Verification and replay handling (nonce store, clock skew) belong to the Control Center and are
Not implemented yet (tracked in #362). `verify_signature` below is a reference for tests only.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SIGNATURE_VERSION = "OMES-WORKER-SIG-V1"
ALGORITHM = "ed25519"
KEY_FILE_NAME = "worker_ed25519.key"
HEADER_WORKER_ID = "X-OMES-Worker-Id"
HEADER_TIMESTAMP = "X-OMES-Timestamp"
HEADER_NONCE = "X-OMES-Nonce"
HEADER_SIGNATURE = "X-OMES-Signature"
# Documented bound for a verifier; the worker itself only stamps the current time.
MAX_CLOCK_SKEW_SECONDS = 300

OPENSSL_TIMEOUT_SECONDS = 15
# DER SubjectPublicKeyInfo prefix of an Ed25519 key: the raw 32-byte key follows it.
_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_.:-]{16,128}$")


class SigningError(Exception):
    """Key generation or signing failed. Callers must not send an unsigned request."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_nonce() -> str:
    return f"nonce_{secrets.token_hex(16)}"


def _openssl() -> str:
    path = shutil.which("openssl")
    if not path:
        raise SigningError("openssl is not installed; install OpenSSL 3 (the 'openssl' package) to enroll")
    return path


def _run(argv: list[str]) -> bytes:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv list, no shell
            argv, capture_output=True, timeout=OPENSSL_TIMEOUT_SECONDS, shell=False, check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise SigningError("openssl timed out") from exc
    except OSError as exc:
        raise SigningError(f"openssl could not be run: {exc.strerror or type(exc).__name__}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().replace("\n", " ")[:200]
        raise SigningError(
            f"openssl {argv[1] if len(argv) > 1 else ''} failed (OpenSSL 3 with Ed25519 support is required): {detail}"
        )
    return proc.stdout


def generate_private_key(dest: Path) -> None:
    """Create a new Ed25519 private key at `dest` (must not exist), mode 0600 from the first byte."""
    openssl = _openssl()
    try:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as exc:
        raise SigningError(f"cannot create key file: {exc.strerror or type(exc).__name__}") from exc
    os.close(fd)
    os.chmod(dest, 0o600)
    try:
        _run([openssl, "genpkey", "-algorithm", "ed25519", "-out", str(dest)])
        if dest.stat().st_size == 0:
            raise SigningError("openssl produced an empty key file")
    except BaseException:
        try:
            dest.unlink()
        except OSError:
            pass
        raise


def check_key_file(key_path: Path | str) -> Path:
    """Return the key path if it is a private regular file; refuse otherwise (fail closed)."""
    p = Path(key_path)
    try:
        st = p.lstat()
    except OSError as exc:
        raise SigningError(f"worker private key not found at {p}; re-enroll with 'omes worker enroll'") from exc
    if not stat.S_ISREG(st.st_mode):
        raise SigningError(f"worker private key {p} is not a regular file")
    if st.st_mode & 0o077:
        raise SigningError(f"worker private key {p} is accessible to group/other (mode {st.st_mode & 0o777:04o}); expected 0600")
    return p


def public_key_raw(key_path: Path | str) -> bytes:
    """Derive the raw 32-byte Ed25519 public key from a private key file."""
    der = _run([_openssl(), "pkey", "-in", str(check_key_file(key_path)), "-pubout", "-outform", "DER"])
    if len(der) != len(_SPKI_PREFIX) + 32 or not der.startswith(_SPKI_PREFIX):
        raise SigningError("unexpected public key encoding from openssl (not Ed25519)")
    return der[len(_SPKI_PREFIX):]


def public_key_b64(key_path: Path | str) -> str:
    return base64.b64encode(public_key_raw(key_path)).decode("ascii")


def sign_bytes(key_path: Path | str, message: bytes) -> bytes:
    """Return the raw 64-byte Ed25519 signature of `message`."""
    openssl = _openssl()
    key = check_key_file(key_path)
    with tempfile.TemporaryDirectory(prefix="omes-sig-") as td:  # mode 0700
        msg_file = Path(td) / "msg"
        msg_file.write_bytes(message)
        sig = _run([openssl, "pkeyutl", "-sign", "-rawin", "-inkey", str(key), "-in", str(msg_file)])
    if len(sig) != 64:
        raise SigningError("unexpected signature length from openssl")
    return sig


def sign_challenge(key_path: Path | str, enrollment_challenge: str) -> str:
    """Proof of possession: base64 raw signature over the UTF-8 enrollment challenge."""
    return base64.b64encode(sign_bytes(key_path, enrollment_challenge.encode("utf-8"))).decode("ascii")


def canonical_string(method: str, path: str, body: bytes, nonce: str, timestamp: str) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return "\n".join([SIGNATURE_VERSION, method.upper(), path, digest, nonce, timestamp]).encode("utf-8")


def sign_request(
    key_path: Path | str,
    worker_id: str,
    method: str,
    path: str,
    body: bytes,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> dict[str, str]:
    """Return the four X-OMES-* headers for a request with these exact body bytes."""
    nonce = nonce or new_nonce()
    timestamp = timestamp or now_iso()
    if not _NONCE_RE.match(nonce):
        raise SigningError("nonce has an invalid shape")
    sig = sign_bytes(key_path, canonical_string(method, path, body, nonce, timestamp))
    return {
        HEADER_WORKER_ID: worker_id,
        HEADER_TIMESTAMP: timestamp,
        HEADER_NONCE: nonce,
        HEADER_SIGNATURE: base64.b64encode(sig).decode("ascii"),
    }


def verify_signature(public_key_raw_bytes: bytes, message: bytes, signature: bytes) -> bool:
    """Reference verification through `openssl pkeyutl -verify` (used by tests, not by the worker)."""
    if len(public_key_raw_bytes) != 32 or len(signature) != 64:
        return False
    openssl = _openssl()
    with tempfile.TemporaryDirectory(prefix="omes-ver-") as td:
        d = Path(td)
        (d / "pub.der").write_bytes(_SPKI_PREFIX + public_key_raw_bytes)
        (d / "msg").write_bytes(message)
        (d / "sig").write_bytes(signature)
        proc = subprocess.run(  # noqa: S603 - fixed argv list, no shell
            [openssl, "pkeyutl", "-verify", "-pubin", "-inkey", str(d / "pub.der"), "-keyform", "DER",
             "-rawin", "-in", str(d / "msg"), "-sigfile", str(d / "sig")],
            capture_output=True, timeout=OPENSSL_TIMEOUT_SECONDS, shell=False, check=False, stdin=subprocess.DEVNULL,
        )
    return proc.returncode == 0
