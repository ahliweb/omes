"""Tests the Telegram approval front end (issue #65) against a fake,
stdlib-only Telegram-shaped HTTP server - no network, no real bot token.
Covers: outbound send via the curl -K token pattern, approver
authorization (subset-of-allowlist), hash-mismatch/staleness rejection,
and the two structural security guarantees: the bot token never appears
in any captured output/audit, and the source never references Telegram's
long-polling read endpoint."""
import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from . import _pathfix  # noqa: F401

from content import cli, jobs, paths, telegram  # noqa: E402

PLANTED_TOKEN = "PLANTED_FAKE_TOKEN_123456789:AA-should-never-leak"


class _FakeTelegramHandler(BaseHTTPRequestHandler):
    received_paths: list[str] = []

    def _respond_ok(self):
        body = json.dumps({"ok": True, "result": {"message_id": 1}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        _FakeTelegramHandler.received_paths.append(self.path)
        self._respond_ok()

    def do_POST(self):  # noqa: N802
        _FakeTelegramHandler.received_paths.append(self.path)
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self._respond_ok()

    def log_message(self, format, *args):  # noqa: A002 - silence test server logs
        pass


def _run_fake_server():
    server = HTTPServer(("127.0.0.1", 0), _FakeTelegramHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class TelegramTestBase(unittest.TestCase):
    def setUp(self):
        _FakeTelegramHandler.received_paths = []
        self.server, self.thread = _run_fake_server()
        self.addCleanup(self.server.shutdown)

        self._tmp = tempfile.mkdtemp(prefix="omes-content-telegram-")
        self.hermes_home = Path(self._tmp) / "hermes-home"
        self.hermes_home.mkdir()
        env_file = self.hermes_home / ".env"
        env_file.write_text(
            f"TELEGRAM_BOT_TOKEN={PLANTED_TOKEN}\n"
            "TELEGRAM_ALLOWED_USERS=111,222\n"
            "TELEGRAM_ALLOWED_CHATS=-1001\n",
            encoding="utf-8",
        )
        os.chmod(env_file, 0o600)

        self._old_env = {
            k: os.environ.get(k)
            for k in (
                "HERMES_HOME",
                "OMES_CONTENT_TELEGRAM_API_BASE",
                "OMES_CONTENT_APPROVERS",
                "OMES_CONTENT_ROOT",
            )
        }
        os.environ["HERMES_HOME"] = str(self.hermes_home)
        os.environ["OMES_CONTENT_TELEGRAM_API_BASE"] = f"http://127.0.0.1:{self.server.server_port}"

        self.root = Path(self._tmp) / "content"
        os.environ["OMES_CONTENT_ROOT"] = str(self.root)
        paths.ensure_layout(self.root)

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_job(self, job_id="job1", targets=None):
        processing = paths.job_processing_dir(job_id, self.root)
        processing.mkdir(parents=True, exist_ok=True)
        (processing / "source.mp4").write_bytes(b"fake media bytes")
        record = jobs.new_job_record(
            job_id=job_id,
            original_path="inbox/x.mp4",
            processing_path=f"processing/{job_id}/source.mp4",
            sha256_hex="f" * 64,
            size_bytes=11,
            mime_guess="video/mp4",
        )
        jobs.plan_job(record, caption="hello world", targets=targets or ["generic_browser"])
        jobs.save_job(record, self.root)
        return record


class TestOutboundSend(TelegramTestBase):
    def test_send_message_hits_fake_server_and_returns_ok(self):
        result = telegram.send_message("111", "hello")
        self.assertTrue(result["ok"])
        self.assertEqual(len(_FakeTelegramHandler.received_paths), 1)

    def test_token_appears_in_the_request_path_but_never_in_our_output(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            telegram.send_message("111", "hello")
        # The wire request legitimately contains the token (that's how the
        # Telegram Bot API works - the bash allowlist tool does the same);
        # what must never happen is OUR process printing/logging it.
        self.assertIn(PLANTED_TOKEN, _FakeTelegramHandler.received_paths[0])
        self.assertNotIn(PLANTED_TOKEN, out.getvalue())

    def test_notify_job_sends_and_never_leaks_token_in_its_result(self):
        record = self._make_job()
        result = telegram.notify_job(record, self.root, chat_id="111")
        self.assertTrue(result["sent"])
        self.assertNotIn(PLANTED_TOKEN, json.dumps(result))

    def test_cmd_notify_never_leaks_token_to_stdout_or_audit(self):
        record = self._make_job()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["notify", record["job_id"], "--chat-id", "111", "--json"])
        self.assertEqual(code, 0)
        self.assertNotIn(PLANTED_TOKEN, out.getvalue())
        audit_text = paths.audit_log_path(self.root).read_text(encoding="utf-8")
        self.assertNotIn(PLANTED_TOKEN, audit_text)

    def test_build_preview_text_has_no_session_or_secret_content(self):
        record = self._make_job()
        text = telegram.build_preview_text(record)
        self.assertIn("hello world", text)
        self.assertIn("generic_browser", text)
        self.assertIn(record["source"]["sha256"], text)
        self.assertNotIn("sessions", text)
        self.assertNotIn(PLANTED_TOKEN, text)


class TestApproverAuthorization(TelegramTestBase):
    def test_non_approver_rejected(self):
        os.environ["OMES_CONTENT_APPROVERS"] = "999"  # not in TELEGRAM_ALLOWED_USERS either
        record = self._make_job()
        code = cli.main(["approve", record["job_id"], "--actor", "999", "--channel", "telegram", "--json"])
        self.assertEqual(code, 1)
        reloaded = jobs.load_job(record["job_id"], self.root)
        self.assertEqual(reloaded["state"], "approval-required")

    def test_approver_not_in_telegram_allowlist_is_rejected_even_if_in_approvers(self):
        # 333 is in OMES_CONTENT_APPROVERS but NOT in TELEGRAM_ALLOWED_USERS
        # (111,222) - must fail closed on the subset rule.
        os.environ["OMES_CONTENT_APPROVERS"] = "333"
        record = self._make_job()
        code = cli.main(["approve", record["job_id"], "--actor", "333", "--channel", "telegram", "--json"])
        self.assertEqual(code, 1)

    def test_authorized_approver_succeeds(self):
        os.environ["OMES_CONTENT_APPROVERS"] = "111,222"
        record = self._make_job()
        code = cli.main(["approve", record["job_id"], "--actor", "111", "--channel", "telegram", "--json"])
        self.assertEqual(code, 0)
        reloaded = jobs.load_job(record["job_id"], self.root)
        self.assertEqual(reloaded["state"], "approved")
        self.assertEqual(reloaded["approvals"][-1]["channel"], "telegram")

    def test_cli_channel_approval_is_never_gated_by_approvers_list(self):
        os.environ.pop("OMES_CONTENT_APPROVERS", None)
        record = self._make_job()
        code = cli.main(["approve", record["job_id"], "--actor", "anyone", "--json"])
        self.assertEqual(code, 0)


class TestHashMismatchAndStaleness(TelegramTestBase):
    def test_hash_mismatch_rejected(self):
        os.environ["OMES_CONTENT_APPROVERS"] = "111"
        record = self._make_job()
        code = cli.main(
            [
                "approve",
                record["job_id"],
                "--actor",
                "111",
                "--channel",
                "telegram",
                "--expected-hash",
                "0" * 64,
                "--json",
            ]
        )
        self.assertEqual(code, 1)
        reloaded = jobs.load_job(record["job_id"], self.root)
        self.assertEqual(reloaded["state"], "approval-required")

    def test_matching_expected_hash_succeeds(self):
        os.environ["OMES_CONTENT_APPROVERS"] = "111"
        record = self._make_job()
        code = cli.main(
            [
                "approve",
                record["job_id"],
                "--actor",
                "111",
                "--channel",
                "telegram",
                "--expected-hash",
                record["source"]["sha256"],
                "--json",
            ]
        )
        self.assertEqual(code, 0)

    def test_stale_approval_is_rejected_before_publish(self):
        record = self._make_job()
        os.environ["OMES_CONTENT_APPROVERS"] = "111"
        cli.main(["approve", record["job_id"], "--actor", "111", "--channel", "telegram", "--ttl-seconds", "0", "--json"])
        reloaded = jobs.load_job(record["job_id"], self.root)
        time.sleep(1.1)
        valid, reason = jobs.is_approval_valid(reloaded)
        self.assertFalse(valid)
        self.assertIn("expired", reason)


class TestNoPolling(unittest.TestCase):
    def test_source_never_references_the_long_polling_endpoint(self):
        content_dir = Path(__file__).resolve().parents[2] / "lib" / "omes" / "py" / "content"
        offenders = []
        for py_file in content_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            if re.search(r"getUpdates", text):
                offenders.append(str(py_file))
        self.assertEqual(offenders, [], f"found a reference to the prohibited long-polling endpoint in: {offenders}")


if __name__ == "__main__":
    unittest.main()
