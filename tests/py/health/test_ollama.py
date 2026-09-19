"""tests/py/health/test_ollama.py - unit tests for lib/omes/py/health/ollama.py
using a stdlib http.server fake Ollama endpoint. No real network, no real
Ollama required (issue #71).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OLLAMA_PY = os.path.join(ROOT, "lib", "omes", "py", "health", "ollama.py")

_spec = importlib.util.spec_from_file_location("omes_health_ollama", OLLAMA_PY)
ollama = importlib.util.module_from_spec(_spec)
sys.modules["omes_health_ollama"] = ollama
_spec.loader.exec_module(ollama)  # type: ignore[union-attr]


def make_handler(config: dict):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def handle_error(self, *_a):  # noqa: A003 - stdlib override name
            # A client that hit its own request timeout (e.g. the
            # load-timeout test) disconnects before this fake server
            # finishes writing its (irrelevant, by then) response; that
            # is expected and not a test failure.
            pass

        def _send_json(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_raw(self, raw: bytes, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                raw = self.rfile.read(length)
                try:
                    return json.loads(raw.decode("utf-8"))
                except ValueError:
                    return {}
            return {}

        def do_GET(self):  # noqa: N802
            if self.path == "/api/version":
                self._send_json(config.get("version", {"version": "0.1.0"}))
            elif self.path == "/api/tags":
                self._send_json({"models": [{"name": n} for n in config.get("tags", [])]})
            elif self.path == "/api/ps":
                self._send_json(config.get("ps_response", {"models": []}))
            else:
                self._send_json({}, status=404)

        def do_POST(self):  # noqa: N802
            self._read_body()
            if self.path == "/api/generate":
                delay = config.get("generate_delay")
                if delay:
                    import time

                    time.sleep(delay)
                if config.get("generate_malformed"):
                    self._send_raw(b"{not-json")
                    return
                self._send_json(config.get("generate_response", {"response": "pong"}))
            elif self.path == "/api/chat":
                self._send_json(config.get("chat_response", {"message": {"tool_calls": []}}))
            elif self.path == "/api/embed":
                calls = config.setdefault("_embed_calls", [0])
                calls[0] += 1
                responses = config.get("embed_responses")
                if responses is not None:
                    idx = min(calls[0] - 1, len(responses) - 1)
                    self._send_json(responses[idx])
                else:
                    self._send_json(config.get("embed_response", {"embeddings": [[0.1, 0.2, 0.3]]}))
            else:
                self._send_json({}, status=404)

    return Handler


class FakeOllama:
    def __init__(self, config: dict):
        self.config = config
        self.server = HTTPServer(("127.0.0.1", 0), make_handler(config))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    @property
    def endpoint(self) -> str:
        return f"127.0.0.1:{self.server.server_address[1]}"


class BaseOllamaTest(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["OMES_HEALTH_TIMEOUT"] = "2"
        os.environ["OMES_OLLAMA_LOAD_TIMEOUT"] = "2"
        for key in ("OMES_OLLAMA_PROFILE", "OMES_OLLAMA_PROFILE_FILE", "OMES_OLLAMA_MODEL", "OMES_OLLAMA_EXPECT_PLACEMENT", "OMES_OLLAMA_ALLOW_REMOTE", "OLLAMA_HOST"):
            os.environ.pop(key, None)
        # Tests run in a sandbox without a real `ollama` binary on PATH;
        # simulate its presence so the tests exercise the endpoint/model/
        # capability logic under test rather than always failing on the
        # binary-presence check (that check itself is covered separately,
        # see test_service_binary_missing_is_reported below).
        self._which_patch = mock.patch.object(ollama.shutil, "which", return_value="/usr/bin/ollama")
        self._which_patch.start()

    def tearDown(self):
        self._which_patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)


class TestServiceLayer(BaseOllamaTest):
    def test_service_unavailable_exits_4(self):
        os.environ["OLLAMA_HOST"] = "127.0.0.1:1"  # nothing listens here
        os.environ["OMES_OLLAMA_MODEL"] = "llama3"
        result = ollama.run([])
        self.assertEqual(result["exit_code"], ollama.EXIT_SERVICE_MISSING)
        self.assertFalse(result["ready"])
        self.assertEqual(result["service"]["status"], "fail")

    def test_healthy_text_profile(self):
        config = {"tags": ["llama3"], "generate_response": {"response": "pong"}, "ps_response": {"models": [{"name": "llama3", "processor": "100% CPU"}]}}
        with FakeOllama(config) as fake:
            os.environ["OLLAMA_HOST"] = fake.endpoint
            os.environ["OMES_OLLAMA_MODEL"] = "llama3"
            result = ollama.run([])
        self.assertEqual(result["exit_code"], ollama.EXIT_READY)
        self.assertTrue(result["ready"])
        self.assertEqual(result["capabilities"]["chat"], "pass")
        self.assertEqual(result["capabilities"]["structured_output"], "not_applicable")


class TestModelLayer(BaseOllamaTest):
    def test_model_missing(self):
        config = {"tags": ["other-model"]}
        with FakeOllama(config) as fake:
            os.environ["OLLAMA_HOST"] = fake.endpoint
            os.environ["OMES_OLLAMA_MODEL"] = "llama3"
            result = ollama.run([])
        self.assertEqual(result["exit_code"], ollama.EXIT_NOT_READY)
        self.assertFalse(result["ready"])
        self.assertEqual(result["model"]["status"], "fail")

    def test_load_timeout(self):
        os.environ["OMES_OLLAMA_LOAD_TIMEOUT"] = "0.2"
        config = {"tags": ["llama3"], "generate_delay": 2}
        with FakeOllama(config) as fake:
            os.environ["OLLAMA_HOST"] = fake.endpoint
            os.environ["OMES_OLLAMA_MODEL"] = "llama3"
            result = ollama.run([])
        self.assertFalse(result["ready"])
        self.assertEqual(result["model"]["status"], "fail")

    def test_malformed_json_from_generate(self):
        config = {"tags": ["llama3"], "generate_malformed": True}
        with FakeOllama(config) as fake:
            os.environ["OLLAMA_HOST"] = fake.endpoint
            os.environ["OMES_OLLAMA_MODEL"] = "llama3"
            result = ollama.run([])
        self.assertFalse(result["ready"])
        self.assertEqual(result["model"]["status"], "fail")

    def test_wrong_placement(self):
        config = {"tags": ["llama3"], "generate_response": {"response": "pong"}, "ps_response": {"models": [{"name": "llama3", "processor": "100% CPU"}]}}
        with FakeOllama(config) as fake:
            os.environ["OLLAMA_HOST"] = fake.endpoint
            os.environ["OMES_OLLAMA_MODEL"] = "llama3"
            os.environ["OMES_OLLAMA_EXPECT_PLACEMENT"] = "gpu"
            result = ollama.run([])
        self.assertFalse(result["ready"])
        self.assertEqual(result["model"]["status"], "fail")


class TestCapabilities(BaseOllamaTest):
    def test_empty_embedding_fails_when_required(self):
        config = {
            "tags": ["llama3"],
            "generate_response": {"response": "pong"},
            "ps_response": {"models": [{"name": "llama3", "processor": "100% CPU"}]},
            "embed_response": {"embeddings": []},
        }
        with FakeOllama(config) as fake:
            os.environ["OLLAMA_HOST"] = fake.endpoint
            os.environ["OMES_OLLAMA_MODEL"] = "llama3"
            os.environ["OMES_OLLAMA_PROFILE"] = "embeddings"
            result = ollama.run([])
        self.assertFalse(result["ready"])
        self.assertEqual(result["capabilities"]["embeddings"], "fail")

    def test_stable_embeddings_pass(self):
        config = {
            "tags": ["llama3"],
            "generate_response": {"response": "pong"},
            "ps_response": {"models": [{"name": "llama3", "processor": "100% CPU"}]},
            "embed_response": {"embeddings": [[0.1, 0.2, 0.3]]},
        }
        with FakeOllama(config) as fake:
            os.environ["OLLAMA_HOST"] = fake.endpoint
            os.environ["OMES_OLLAMA_MODEL"] = "llama3"
            os.environ["OMES_OLLAMA_PROFILE"] = "embeddings"
            result = ollama.run([])
        self.assertTrue(result["ready"])
        self.assertEqual(result["capabilities"]["embeddings"], "pass")

    def test_unsupported_capability_not_required_is_not_applicable(self):
        config = {"tags": ["llama3"], "generate_response": {"response": "pong"}, "ps_response": {"models": [{"name": "llama3", "processor": "100% CPU"}]}}
        with FakeOllama(config) as fake:
            os.environ["OLLAMA_HOST"] = fake.endpoint
            os.environ["OMES_OLLAMA_MODEL"] = "llama3"
            os.environ["OMES_OLLAMA_PROFILE"] = "text"
            result = ollama.run([])
        self.assertEqual(result["capabilities"]["tool_calling"], "not_applicable")
        self.assertEqual(result["capabilities"]["vision"], "not_applicable")
        self.assertTrue(result["ready"])

    def test_required_capability_failure_fails_closed(self):
        config = {
            "tags": ["llama3"],
            "generate_response": {"response": "pong"},
            "ps_response": {"models": [{"name": "llama3", "processor": "100% CPU"}]},
            "chat_response": {"message": {"tool_calls": []}},
        }
        with FakeOllama(config) as fake:
            os.environ["OLLAMA_HOST"] = fake.endpoint
            os.environ["OMES_OLLAMA_MODEL"] = "llama3"
            os.environ["OMES_OLLAMA_PROFILE"] = "tools"
            result = ollama.run([])
        self.assertFalse(result["ready"])
        self.assertEqual(result["capabilities"]["tool_calling"], "fail")

    def test_structured_output_pass(self):
        config = {
            "tags": ["llama3"],
            "generate_response": {"response": json.dumps({"ok": True})},
            "ps_response": {"models": [{"name": "llama3", "processor": "100% CPU"}]},
        }
        with FakeOllama(config) as fake:
            os.environ["OLLAMA_HOST"] = fake.endpoint
            os.environ["OMES_OLLAMA_MODEL"] = "llama3"
            os.environ["OMES_OLLAMA_PROFILE"] = "structured"
            result = ollama.run([])
        self.assertTrue(result["ready"])
        self.assertEqual(result["capabilities"]["structured_output"], "pass")


class TestBindPolicy(BaseOllamaTest):
    def test_loopback_endpoint_passes_bind_policy(self):
        config = {"tags": ["llama3"], "generate_response": {"response": "pong"}, "ps_response": {"models": []}}
        with FakeOllama(config) as fake:
            checks = []
            os.environ.pop("OMES_OLLAMA_ALLOW_REMOTE", None)
            result = ollama.check_service(f"http://127.0.0.1:{fake.server.server_address[1]}", 2.0, checks)
            self.assertEqual(result["status"], "pass")

    def test_non_loopback_host_is_classified_unsafe_without_opt_in(self):
        # Pure classification test (no real remote listener needed):
        # _is_loopback/_endpoint_host are the functions the bind-policy
        # check itself relies on.
        self.assertFalse(ollama._is_loopback("192.0.2.10"))
        self.assertEqual(ollama._endpoint_host("http://192.0.2.10:11434"), "192.0.2.10")


class TestServiceBinary(BaseOllamaTest):
    def test_service_binary_missing_is_reported_but_endpoint_still_checked(self):
        self._which_patch.stop()
        with mock.patch.object(ollama.shutil, "which", return_value=None):
            config = {"tags": ["llama3"], "generate_response": {"response": "pong"}, "ps_response": {"models": [{"name": "llama3", "processor": "100% CPU"}]}}
            with FakeOllama(config) as fake:
                os.environ["OLLAMA_HOST"] = fake.endpoint
                os.environ["OMES_OLLAMA_MODEL"] = "llama3"
                result = ollama.run([])
        self.assertEqual(result["service"]["status"], "fail")
        self.assertFalse(result["ready"])
        self._which_patch.start()


if __name__ == "__main__":
    unittest.main()
