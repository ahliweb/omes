"""lib/omes/py/jobs/worker.py - Secure pull-worker transport for the Control Center (ADR-0027, issue #192).

Provides outbound-only HTTPS polling, challenge-based enrollment, fixed-argv job execution,
and telemetry heartbeats for distributed OMES hosts.

Guarantees:
  - Zero inbound listening ports on OMES hosts.
  - Challenge-based enrollment (issue #362): a real Ed25519 key is generated locally with OpenSSL 3,
    stays in its own 0600 file, and the worker proves possession by signing the enrollment challenge.
  - Every poll, result and heartbeat request carries X-OMES-Worker-Id/Timestamp/Nonce/Signature
    headers (see worker_signing.py). AWCMS-side verification is Not implemented yet (tracked in #362).
  - No arbitrary shell, eval, or unvetted command execution (dispatches exclusively via jobs.runner).
  - Strict tenant and server scope validation (rejects mismatched requests).
  - Zero raw secrets in results, logs, or telemetry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import correlation, paths, runner, schema as schema_mod, store
from . import worker_signing as signing

SUPPORTED_CAPABILITIES = [
    "status",
    "preflight",
    "backup",
    "restore",
    "rollback",
]

DEFAULT_POLL_INTERVAL = 10
DEFAULT_HEARTBEAT_INTERVAL = 60

CONTRACTS_DIR = Path(__file__).resolve().parents[4] / "contracts" / "control-center" / "v1"


class WorkerError(Exception):
    pass


class EnrollmentError(WorkerError):
    pass


class ScopeMismatchError(WorkerError):
    pass


class LegacyCredentialsError(WorkerError):
    """Credentials written before issue #362 hold a fabricated key; they are refused, never migrated."""


LEGACY_CREDENTIALS_MESSAGE = (
    "stored worker credentials predate request signing (issue #362) and contain a fabricated key; "
    "they are refused. Re-enroll with a fresh challenge: 'omes worker enroll --endpoint <url> "
    "--tenant <id> --server <id> --challenge <token>'. The old credentials are not migrated."
)


@dataclass
class WorkerCredentials:
    tenant_id: str
    server_id: str
    worker_id: str
    control_center_endpoint: str
    public_key: str  # base64 of the raw 32-byte Ed25519 public key
    private_key_path: str  # path of the 0600 key file; the key itself is never held in this object
    public_key_algorithm: str = signing.ALGORITHM
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL
    heartbeat_interval_seconds: int = DEFAULT_HEARTBEAT_INTERVAL
    enrolled_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Persist only the key file's name (resolved against the worker dir on load), never key bytes.
        d["private_key_file"] = Path(d.pop("private_key_path")).name
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any], worker_dir: Path | None = None) -> WorkerCredentials:
        if "private_key" in data or "private_key_file" not in data:
            raise LegacyCredentialsError(LEGACY_CREDENTIALS_MESSAGE)
        key_name = Path(str(data["private_key_file"])).name
        key_path = str((worker_dir or _worker_dir()) / key_name)
        return cls(
            tenant_id=data["tenant_id"],
            server_id=data["server_id"],
            worker_id=data["worker_id"],
            control_center_endpoint=data["control_center_endpoint"],
            public_key=data["public_key"],
            private_key_path=key_path,
            public_key_algorithm=data.get("public_key_algorithm", signing.ALGORITHM),
            poll_interval_seconds=int(data.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)),
            heartbeat_interval_seconds=int(data.get("heartbeat_interval_seconds", DEFAULT_HEARTBEAT_INTERVAL)),
            enrolled_at=data.get("enrolled_at", ""),
        )


def _worker_dir(state_dir: Path | None = None) -> Path:
    base = state_dir or paths.state_root()
    return base / "worker"


def _credentials_path(state_dir: Path | None = None) -> Path:
    return _worker_dir(state_dir) / "credentials.json"


def load_credentials(state_dir: Path | None = None) -> WorkerCredentials | None:
    p = _credentials_path(state_dir)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return WorkerCredentials.from_dict(data, _worker_dir(state_dir))
    except LegacyCredentialsError:
        raise
    except Exception:
        return None


def save_credentials(creds: WorkerCredentials, state_dir: Path | None = None) -> Path:
    wdir = _worker_dir(state_dir)
    wdir.mkdir(parents=True, exist_ok=True)
    os.chmod(wdir, 0o700)
    p = _credentials_path(state_dir)

    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # 0600 before any data is written
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(creds.to_dict(), indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    return p


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _detect_platform() -> dict[str, str]:
    os_name = "ubuntu"
    version = "24.04"
    arch = "amd64"
    if Path("/etc/os-release").is_file():
        text = Path("/etc/os-release").read_text(encoding="utf-8").lower()
        if "linux mint" in text or "id=linuxmint" in text:
            os_name = "linuxmint"
            version = "22"
        elif "22.04" in text:
            version = "22.04"
        elif "26.04" in text:
            version = "26.04"
    return {"os": os_name, "version": version, "arch": arch}


def _load_schema(name: str) -> dict[str, Any]:
    schema_path = CONTRACTS_DIR / f"{name}.schema.json"
    if not schema_path.is_file():
        raise WorkerError(f"contract schema not found: {schema_path}")
    return schema_mod.load_json(schema_path)


class HttpClient:
    """HTTP client wrapper with timeout, headers, request signing and mock injection for testing."""

    def __init__(self, timeout: int = 15, mock_handlers: dict[str, Any] | None = None) -> None:
        self.timeout = timeout
        self.mock_handlers = mock_handlers or {}
        # Every request as sent on the wire (path, exact body bytes, headers); only populated for mocks,
        # so tests can verify what a Control Center would have received.
        self.sent: list[dict[str, Any]] = []

    def post_json(
        self,
        url: str,
        data: dict[str, Any],
        headers: dict[str, str] | None = None,
        creds: WorkerCredentials | None = None,
        nonce: str | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        """POST `data` as JSON. With `creds`, the exact body bytes are signed and the X-OMES-* headers added.

        If signing fails the request is not sent (fail closed).
        """
        path = urllib.parse.urlparse(url).path
        payload = json.dumps(data).encode("utf-8")
        all_headers = dict(headers or {})
        if creds is not None:
            all_headers.update(
                signing.sign_request(creds.private_key_path, creds.worker_id, "POST", path, payload, nonce, timestamp)
            )

        if path in self.mock_handlers:
            self.sent.append({"method": "POST", "path": path, "body": payload, "headers": all_headers})
            handler = self.mock_handlers[path]
            if callable(handler):
                return handler(data)
            return handler

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "OMES-Pull-Worker/1.0",
                **all_headers,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8")
            raise WorkerError(f"HTTP {exc.code} from {url}: {err_body}") from exc
        except Exception as exc:
            raise WorkerError(f"Request to {url} failed: {exc}") from exc


def enroll_worker(
    endpoint: str,
    tenant_id: str,
    server_id: str,
    enrollment_challenge: str,
    state_dir: Path | None = None,
    client: HttpClient | None = None,
) -> WorkerCredentials:
    """Enrolls this host with the Control Center using a challenge token."""
    http_client = client or HttpClient()

    wdir = _worker_dir(state_dir)
    wdir.mkdir(parents=True, exist_ok=True)
    os.chmod(wdir, 0o700)
    key_final = wdir / signing.KEY_FILE_NAME
    key_pending = wdir / f"{signing.KEY_FILE_NAME}.pending"

    # Generate a real Ed25519 key locally (OpenSSL 3, fixed argv, mode 0600). Fail closed: there is no
    # fallback key. A key left by an interrupted enrollment is ours and is replaced.
    key_pending.unlink(missing_ok=True)
    try:
        signing.generate_private_key(key_pending)
        pub_key = signing.public_key_b64(key_pending)
        challenge_signature = signing.sign_challenge(key_pending, enrollment_challenge)
    except signing.SigningError as exc:
        key_pending.unlink(missing_ok=True)
        raise EnrollmentError(f"cannot generate or use a local Ed25519 key: {exc}") from exc

    try:
        return _finish_enrollment(
            http_client, endpoint, tenant_id, server_id, enrollment_challenge,
            pub_key, challenge_signature, key_pending, key_final, state_dir,
        )
    finally:
        key_pending.unlink(missing_ok=True)  # no-op after a successful rename


def _finish_enrollment(
    http_client: HttpClient,
    endpoint: str,
    tenant_id: str,
    server_id: str,
    enrollment_challenge: str,
    pub_key: str,
    challenge_signature: str,
    key_pending: Path,
    key_final: Path,
    state_dir: Path | None,
) -> WorkerCredentials:
    hostname = os.uname().nodename
    platform_info = _detect_platform()

    req_payload = {
        "tenant_id": tenant_id,
        "server_id": server_id,
        "enrollment_challenge": enrollment_challenge,
        "public_key": pub_key,
        "public_key_algorithm": signing.ALGORITHM,
        "challenge_signature": challenge_signature,
        "hostname": hostname,
        "platform": platform_info,
        "capabilities": list(SUPPORTED_CAPABILITIES),
    }

    # Validate request contract
    req_schema = _load_schema("worker-enrollment.request")
    schema_errors = schema_mod.validate(req_payload, req_schema)
    if schema_errors:
        raise EnrollmentError(f"enrollment request schema violation: {'; '.join(schema_errors)}")

    enroll_url = f"{endpoint.rstrip('/')}/api/v1/worker/enroll"
    resp_data = http_client.post_json(enroll_url, req_payload)

    # Validate response contract
    resp_schema = _load_schema("worker-enrollment.response")
    resp_errors = schema_mod.validate(resp_data, resp_schema)
    if resp_errors:
        raise EnrollmentError(f"enrollment response schema violation: {'; '.join(resp_errors)}")

    status = resp_data.get("status")
    if status != "enrolled":
        raise EnrollmentError(f"enrollment failed with status '{status}'")

    # The control center accepted the key: only now does it replace any earlier key file.
    os.replace(key_pending, key_final)
    creds = WorkerCredentials(
        tenant_id=tenant_id,
        server_id=server_id,
        worker_id=resp_data["worker_id"],
        control_center_endpoint=endpoint,
        public_key=pub_key,
        private_key_path=str(key_final),
        poll_interval_seconds=resp_data.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL),
        heartbeat_interval_seconds=resp_data.get("heartbeat_interval_seconds", DEFAULT_HEARTBEAT_INTERVAL),
        enrolled_at=resp_data.get("enrolled_at", _now_iso()),
    )
    save_credentials(creds, state_dir)
    return creds


def send_heartbeat(
    creds: WorkerCredentials,
    status: str = "healthy",
    state_dir: Path | None = None,
    client: HttpClient | None = None,
) -> dict[str, Any]:
    """Transmits periodic heartbeat telemetry to Control Center."""
    http_client = client or HttpClient()

    # Capability digest
    reg_path = Path(__file__).resolve().parents[4] / "architecture" / "capabilities.json"
    digest = "sha256:unknown"
    if reg_path.is_file():
        digest = f"sha256:{hashlib.sha256(reg_path.read_bytes()).hexdigest()[:16]}"

    version_path = Path(__file__).resolve().parents[4] / "VERSION"
    omes_ver = version_path.read_text(encoding="utf-8").strip() if version_path.is_file() else "0.1.0"

    req_payload = {
        "tenant_id": creds.tenant_id,
        "server_id": creds.server_id,
        "worker_id": creds.worker_id,
        "timestamp": _now_iso(),
        "status": status,
        "omes_version": omes_ver,
        "contract_version": "1.0.0",
        "capability_registry_digest": digest,
        "platform": _detect_platform(),
        "uptime_seconds": 120,
        "last_reconciliation_at": _now_iso(),
    }

    req_schema = _load_schema("worker-heartbeat.request")
    req_errors = schema_mod.validate(req_payload, req_schema)
    if req_errors:
        raise WorkerError(f"heartbeat request schema violation: {'; '.join(req_errors)}")

    hb_url = f"{creds.control_center_endpoint.rstrip('/')}/api/v1/worker/heartbeat"
    resp = http_client.post_json(hb_url, req_payload, creds=creds)

    resp_schema = _load_schema("worker-heartbeat.response")
    resp_errors = schema_mod.validate(resp, resp_schema)
    if resp_errors:
        raise WorkerError(f"heartbeat response schema violation: {'; '.join(resp_errors)}")

    return resp


def poll_and_dispatch_once(
    creds: WorkerCredentials,
    state_dir: Path | None = None,
    client: HttpClient | None = None,
) -> dict[str, Any]:
    """Polls Control Center for queued jobs and executes allowlisted operations."""
    http_client = client or HttpClient()

    nonce = signing.new_nonce()
    poll_timestamp = _now_iso()
    poll_payload = {
        "tenant_id": creds.tenant_id,
        "server_id": creds.server_id,
        "worker_id": creds.worker_id,
        "nonce": nonce,
        "timestamp": poll_timestamp,
        "capabilities": list(SUPPORTED_CAPABILITIES),
    }

    # Validate poll contract
    poll_req_schema = _load_schema("worker-poll.request")
    poll_req_errors = schema_mod.validate(poll_payload, poll_req_schema)
    if poll_req_errors:
        raise WorkerError(f"poll request schema violation: {'; '.join(poll_req_errors)}")

    poll_url = f"{creds.control_center_endpoint.rstrip('/')}/api/v1/worker/poll"
    poll_resp = http_client.post_json(
        poll_url, poll_payload, creds=creds, nonce=nonce, timestamp=poll_timestamp
    )

    # Validate poll response contract
    poll_resp_schema = _load_schema("worker-poll.response")
    poll_resp_errors = schema_mod.validate(poll_resp, poll_resp_schema)
    if poll_resp_errors:
        raise WorkerError(f"poll response schema violation: {'; '.join(poll_resp_errors)}")

    status = poll_resp.get("status")
    if status == "idle":
        return {"status": "idle", "job_id": None}

    if status != "job_available":
        return {"status": status, "job_id": None}

    job = poll_resp.get("job")
    if not job or not isinstance(job, dict):
        raise WorkerError("job_available response missing job payload")

    # Validate job against operation-request contract
    op_schema = _load_schema("operation-request")
    op_errors = schema_mod.validate(job, op_schema)
    if op_errors:
        # Submit rejected result
        err_msg = f"schema violation: {'; '.join(op_errors)}"
        return _submit_result(
            creds=creds,
            client=http_client,
            job=job,
            state="rejected",
            returncode=1,
            summary=err_msg,
            error={"code": "schema_violation", "message": err_msg},
        )

    # Scope validation
    job_target = job.get("target", {})
    if job_target.get("server_id") != creds.server_id:
        err_msg = f"server_id mismatch: expected {creds.server_id}, got {job_target.get('server_id')}"
        return _submit_result(
            creds=creds,
            client=http_client,
            job=job,
            state="rejected",
            returncode=1,
            summary=err_msg,
            error={"code": "scope_mismatch", "message": err_msg},
        )

    if job.get("tenant_id") != creds.tenant_id:
        err_msg = f"tenant_id mismatch: expected {creds.tenant_id}, got {job.get('tenant_id')}"
        return _submit_result(
            creds=creds,
            client=http_client,
            job=job,
            state="rejected",
            returncode=1,
            summary=err_msg,
            error={"code": "tenant_mismatch", "message": err_msg},
        )

    # Check operation allowlist and implementation
    op_name = job.get("operation", "")
    if op_name not in SUPPORTED_CAPABILITIES:
        err_msg = f"unsupported capability: {op_name}"
        return _submit_result(
            creds=creds,
            client=http_client,
            job=job,
            state="rejected",
            returncode=1,
            summary=err_msg,
            error={"code": "unsupported_capability", "message": err_msg},
        )

    argv = runner.build_argv(job)
    if argv is None:
        err_msg = f"operation '{op_name}' is not implemented in current runtime"
        return _submit_result(
            creds=creds,
            client=http_client,
            job=job,
            state="rejected",
            returncode=1,
            summary=err_msg,
            error={"code": "unimplemented_operation", "message": err_msg},
        )

    # Submit to local store
    req_doc: dict[str, Any] = {
        "tenant_id": creds.tenant_id,
        "operation": op_name,
        "target": {"server_id": creds.server_id},
        "actor": job.get("actor") or {"type": "service", "id": "control-center-worker"},
        "idempotency_key": job["idempotency_key"],
        "correlation_id": job["correlation_id"],
    }
    if job.get("parameters"):
        req_doc["parameters"] = job["parameters"]
    if job.get("backup_id"):
        req_doc["backup_id"] = job["backup_id"]
    if job.get("rollback_ref"):
        req_doc["rollback_ref"] = job["rollback_ref"]
    # Issue #356: forward caller-supplied causation/trace/span ids unchanged;
    # store.submit() generates any missing trace/span id locally.
    for _id_field in ("causation_id", "trace_id", "span_id"):
        if job.get(_id_field):
            req_doc[_id_field] = job[_id_field]

    job_record, _replayed = store.submit(
        req_doc,
        root=state_dir,
        actor_for_audit="worker",
        enrolled_tenant_id=creds.tenant_id,
    )
    job_id = job_record["job_id"]

    if not store.can_auto_approve(op_name):
        perm = job.get("permission") or {}
        if not perm.get("requires_approval", True):
            store.approve(job_record, actor="control-center", root=state_dir, note="approved by control center policy")

    started_at = _now_iso()
    try:
        run_record = runner.run(job_record, actor="worker", root=state_dir)
        completed_at = _now_iso()
        success = run_record.get("state") in ("succeeded", "rolled_back")
        out_summary = f"{op_name} executed with state {run_record.get('state')}"

        return _submit_result(
            creds=creds,
            client=http_client,
            job=job,
            job_id=job_id,
            state="succeeded" if success else "failed",
            returncode=0 if success else 1,
            summary=out_summary,
            started_at=started_at,
            completed_at=completed_at,
            readback_status=run_record.get("evidence", {}).get("readback"),
            ids=correlation.result_fields(job_record),
        )
    except Exception as exc:
        completed_at = _now_iso()
        return _submit_result(
            creds=creds,
            client=http_client,
            job=job,
            job_id=job_id,
            state="failed",
            returncode=1,
            summary=f"execution failed: {exc}",
            started_at=started_at,
            completed_at=completed_at,
            error={"code": "execution_failed", "message": str(exc)},
            ids=correlation.result_fields(job_record),
        )


def _submit_result(
    creds: WorkerCredentials,
    client: HttpClient,
    job: dict[str, Any],
    state: str,
    returncode: int,
    summary: str,
    job_id: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    readback_status: dict[str, Any] | None = None,
    error: dict[str, str] | None = None,
    ids: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Uploads sanitized execution result and read-back evidence to Control Center.

    Correlation with the Control Center binds on `idempotency_key`
    (and `correlation_id`) only - the wire payload never carries a `job_id`
    (issue #221: the v1 contract required one but never gave the worker any
    server-known value to send, so `worker-result.request` no longer has
    that field). `job_id`, when passed in here, is purely the OMES-local
    job store id (`lib/omes/py/jobs/store.py`) used for local audit/logging
    and the function's own return value; it is never part of what is sent.
    """
    now = _now_iso()

    result_payload = {
        "tenant_id": creds.tenant_id,
        "server_id": creds.server_id,
        "worker_id": creds.worker_id,
        "correlation_id": job.get("correlation_id", "corr_unknown"),
        "idempotency_key": job.get("idempotency_key", f"idem_{secrets.token_hex(12)}"),
        "operation": job.get("operation", "status"),
        "state": state,
        "started_at": started_at or now,
        "completed_at": completed_at or now,
        "evidence": {
            "returncode": returncode,
            "output_summary": summary[:4096],
            **({"readback_status": readback_status} if readback_status else {}),
        },
        **({"error": error} if error else {}),
        # Issue #356: optional causation/trace/span ids. `ids` comes from the
        # local job record when one exists; otherwise (a rejected job) only
        # what the caller supplied is echoed - nothing is generated.
        **(ids or correlation.echo_fields(job)),
    }

    # Verify no raw secrets leaked in result payload
    secret_errors = schema_mod.scan_for_raw_secrets(result_payload)
    if secret_errors:
        raise WorkerError(f"secret detected in result payload: {'; '.join(secret_errors)}")

    res_req_schema = _load_schema("worker-result.request")
    schema_errors = schema_mod.validate(result_payload, res_req_schema)
    if schema_errors:
        raise WorkerError(f"worker-result.request schema violation: {'; '.join(schema_errors)}")

    result_url = f"{creds.control_center_endpoint.rstrip('/')}/api/v1/worker/result"
    resp = client.post_json(result_url, result_payload, creds=creds)

    res_resp_schema = _load_schema("worker-result.response")
    resp_errors = schema_mod.validate(resp, res_resp_schema)
    if resp_errors:
        raise WorkerError(f"worker-result.response schema violation: {'; '.join(resp_errors)}")

    return {"status": "executed", "job_id": job_id, "state": state, "result": resp}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omes worker", description="OMES Control Center pull-worker client.")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # enroll
    enroll_parser = subparsers.add_parser("enroll", help="Enroll this host with Control Center.")
    enroll_parser.add_argument("--endpoint", required=True, help="Control Center URL endpoint.")
    enroll_parser.add_argument("--tenant", required=True, help="Tenant ID.")
    enroll_parser.add_argument("--server", required=True, help="Server ID.")
    enroll_parser.add_argument("--challenge", required=True, help="Single-use enrollment challenge token.")

    # poll
    poll_parser = subparsers.add_parser("poll", help="Poll Control Center for queued jobs.")
    poll_parser.add_argument("--once", action="store_true", help="Poll once and exit.")

    # heartbeat
    subparsers.add_parser("heartbeat", help="Send heartbeat telemetry to Control Center.")

    # status
    subparsers.add_parser("status", help="Show worker status and credentials summary.")

    args = parser.parse_args(argv)

    if args.subcommand == "enroll":
        try:
            creds = enroll_worker(
                endpoint=args.endpoint,
                tenant_id=args.tenant,
                server_id=args.server,
                enrollment_challenge=args.challenge,
            )
            print(f"[omes-worker] Enrolled successfully as worker '{creds.worker_id}' for server '{creds.server_id}'.")
            return 0
        except Exception as exc:
            print(f"[omes-worker] ERROR: Enrollment failed: {exc}", file=sys.stderr)
            return 1

    try:
        creds = load_credentials()
    except LegacyCredentialsError as exc:
        print(f"[omes-worker] ERROR: {exc}", file=sys.stderr)
        return 1
    if not creds:
        print("[omes-worker] ERROR: Host is not enrolled. Run 'omes worker enroll' first.", file=sys.stderr)
        return 1

    if args.subcommand == "status":
        print(f"[omes-worker] Enrolled: yes")
        print(f"  Worker ID: {creds.worker_id}")
        print(f"  Server ID: {creds.server_id}")
        print(f"  Tenant ID: {creds.tenant_id}")
        print(f"  Endpoint:  {creds.control_center_endpoint}")
        print(f"  Enrolled:  {creds.enrolled_at}")
        print(f"  Key:       {creds.public_key_algorithm} (public key {creds.public_key})")
        return 0

    if args.subcommand == "heartbeat":
        try:
            resp = send_heartbeat(creds)
            print(f"[omes-worker] Heartbeat acknowledged at {resp.get('received_at')}.")
            return 0
        except Exception as exc:
            print(f"[omes-worker] ERROR: Heartbeat failed: {exc}", file=sys.stderr)
            return 1

    if args.subcommand == "poll":
        try:
            res = poll_and_dispatch_once(creds)
            print(f"[omes-worker] Poll completed: {json.dumps(res)}")
            return 0
        except Exception as exc:
            print(f"[omes-worker] ERROR: Poll failed: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
