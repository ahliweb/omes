"""tests/py/health/test_inference_endpoint.py - unit tests for
lib/omes/py/health/{endpoint_guard,pinned_http,inference_endpoint}.py
(issue #292): read-only inference endpoint evidence.

No real network: every request goes to a stdlib stub server bound to
127.0.0.1 (ephemeral port) and every name lookup uses a fake resolver.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
HEALTH_DIR = os.path.join(ROOT, "lib", "omes", "py", "health")
sys.path.insert(0, HEALTH_DIR)

import endpoint_guard as guard  # noqa: E402
import inference_endpoint as ie  # noqa: E402
import pinned_http  # noqa: E402
import model as health_model  # noqa: E402

SCRIPT = os.path.join(HEALTH_DIR, "inference_endpoint.py")

MODELS_OK = json.dumps({"object": "list", "data": [{"id": "demo-model", "object": "model"}, {"id": "other"}]}).encode()


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class Stub:
    """A tiny configurable OpenAI-compatible stub that records requests."""

    def __init__(self, tls=None, **cfg):
        self.cfg = {"status": 200, "body": MODELS_OK}
        self.cfg.update(cfg)
        self.requests = []
        self.sni = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def handle_error(self, *_a):  # noqa: A003
                pass

            def do_GET(self):  # noqa: N802
                stub.requests.append(
                    {
                        "method": "GET",
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                    }
                )
                c = stub.cfg
                if c.get("delay"):
                    time.sleep(c["delay"])
                status = c["status"]
                if c.get("omit_date"):
                    self.send_response_only(status)
                else:
                    self.send_response(status)
                for key, value in (c.get("headers") or {}).items():
                    self.send_header(key, value)
                if c.get("endless"):
                    self.end_headers()
                    chunk = b"x" * 65536
                    try:
                        for _ in range(256):
                            self.wfile.write(chunk)
                    except OSError:
                        pass
                    return
                body = c["body"]
                if c.get("drip"):
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    try:
                        for i in range(len(body)):
                            self.wfile.write(body[i : i + 1])
                            self.wfile.flush()
                            time.sleep(c["drip"])
                    except OSError:
                        pass
                    return
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_PUT = do_DELETE = do_GET  # noqa: N815 - any verb is recorded

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.handle_error = lambda *_a: None  # clients that hit their deadline disconnect mid-write
        if tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(tls["cert"], tls["key"])

            def on_sni(_sock, name, _ctx):
                stub.sni.append(name)

            ctx.sni_callback = on_sni
            self.server.socket = ctx.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class FakeResolver:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, host, port):
        self.calls.append((host, port))
        idx = min(len(self.calls) - 1, len(self.answers) - 1)
        answer = self.answers[idx]
        if isinstance(answer, Exception):
            raise answer
        return list(answer)


def layer_states(result):
    return {name: (layer["status"], layer.get("reason")) for name, layer in result["layers"].items()}


def probe(url, model="demo-model", **kw):
    kw.setdefault("timeout", 3.0)
    return ie.probe(url, model, **kw)


class NoNetwork:
    """Context manager proving that no connection attempt is made."""

    def __enter__(self):
        self.patch = mock.patch.object(pinned_http, "get", side_effect=AssertionError("network used"))
        self.mock = self.patch.start()
        return self

    def __exit__(self, *exc):
        self.patch.stop()


# ---------------------------------------------------------------------------
# Endpoint parsing / URL attacks
# ---------------------------------------------------------------------------


class TestParseEndpoint(unittest.TestCase):
    def assertRejected(self, raw, reason=None):
        with self.assertRaises(guard.EndpointError) as cm:
            guard.parse_endpoint(raw)
        if reason:
            self.assertEqual(cm.exception.reason, reason, raw)

    def test_accepts_canonical_endpoints(self):
        cases = {
            "http://127.0.0.1:8000": ("http", "127.0.0.1", 8000, ""),
            "HTTP://LOCALHOST": ("http", "localhost", 80, ""),
            "https://localhost": ("https", "localhost", 443, ""),
            "http://[::1]:11434/v1": ("http", "::1", 11434, "/v1"),
            "http://10.0.0.5/prefix/": ("http", "10.0.0.5", 80, "/prefix"),
            "http://model_server.internal:8080": ("http", "model_server.internal", 8080, ""),
        }
        for raw, (scheme, host, port, base) in cases.items():
            ep = guard.parse_endpoint(raw)
            self.assertEqual((ep.scheme, ep.host, ep.port, ep.base_path), (scheme, host, port, base), raw)

    def test_hostile_schemes_are_rejected(self):
        for raw in (
            "file:///etc/passwd",
            "gopher://127.0.0.1:70/_x",
            "ftp://127.0.0.1/",
            "javascript:alert(1)",
            "data:text/plain,hi",
            "127.0.0.1:8000",  # no scheme: urlsplit reads the host as a scheme
            "localhost:8000",
            "ws://127.0.0.1:8000",
        ):
            self.assertRejected(raw, "scheme_not_allowed")

    def test_userinfo_is_rejected_and_never_echoed(self):
        for raw in ("http://a@b", "http://user:s3cret@127.0.0.1:8000", "http://127.0.0.1@evil.example/", "http://:tok@127.0.0.1"):
            self.assertRejected(raw, "userinfo_not_allowed")
            self.assertEqual(guard.redact_endpoint(raw), "[REDACTED]")

    def test_crlf_and_control_characters_are_rejected(self):
        for raw in (
            "http://127.0.0.1:8000/\r\nX-Injected: 1",
            "http://127.0.0.1:8000/\nGET / HTTP/1.1",
            "http://127.0.0.1:8000/\x00",
            "http://127.0.0.1:8000/ x",
            " http://127.0.0.1:8000",
            "http://127.0.0.1:8000\t",
            "http://127.0.0.1:8000/\x7f",
            "http://127.0.0.1\\@evil.example/",
        ):
            self.assertRejected(raw, "invalid_url")

    def test_non_ascii_and_oversized_urls_are_rejected(self):
        self.assertRejected("http://ｌocalhost/", "invalid_url")
        self.assertRejected("http://" + "a" * 3000, "invalid_url")

    def test_query_fragment_and_odd_paths_are_rejected(self):
        self.assertRejected("http://127.0.0.1:8000/?a=b", "query_not_allowed")
        self.assertRejected("http://127.0.0.1:8000/#frag", "query_not_allowed")
        self.assertRejected("http://127.0.0.1:8000?x", "query_not_allowed")
        for raw in ("http://localhost/../x", "http://localhost//x", "http://localhost/%2e%2e/", "http://localhost/a;b", "http://localhost/./a"):
            self.assertRejected(raw, "invalid_path")

    def test_bad_ports_and_hosts(self):
        self.assertRejected("http://127.0.0.1:99999", "invalid_port")
        self.assertRejected("http://127.0.0.1:0", "invalid_port")
        self.assertRejected("http://127.0.0.1:abc", "invalid_port")
        self.assertRejected("http://[::1", "invalid_url")
        self.assertRejected("http://", "invalid_url")
        self.assertRejected("http://[fe80::1%25eth0]/", "invalid_host")
        self.assertRejected("http://bad_-.host-/", "invalid_host")

    def test_numeric_encoded_hosts_are_rejected(self):
        for raw in ("http://2130706433/", "http://0x7f.1/", "http://0177.0.0.1/", "http://127.1/", "http://127.000.000.001/", "http://0x7f000001/"):
            self.assertRejected(raw, "non_canonical_ip_host")

    def test_non_string_input(self):
        for raw in (None, "", 7, b"http://127.0.0.1", ["http://127.0.0.1"]):
            self.assertRejected(raw)

    def test_probe_path_is_allowlisted_and_not_duplicated(self):
        self.assertEqual(guard.parse_endpoint("http://127.0.0.1:1").probe_path, "/v1/models")
        self.assertEqual(guard.parse_endpoint("http://127.0.0.1:1/v1").probe_path, "/v1/models")
        self.assertEqual(guard.parse_endpoint("http://127.0.0.1:1/proxy").probe_path, "/proxy/v1/models")
        self.assertEqual(guard.ALLOWED_PROBES, (("GET", "/v1/models"),))

    def test_host_header_forms(self):
        self.assertEqual(guard.parse_endpoint("http://localhost").host_header, "localhost")
        self.assertEqual(guard.parse_endpoint("https://localhost:8443").host_header, "localhost:8443")
        self.assertEqual(guard.parse_endpoint("http://[::1]:81").host_header, "[::1]:81")

    def test_redact_never_echoes_rejected_input(self):
        for raw in ("http://u:p@127.0.0.1/?k=sk-secret#f", "file:///etc/shadow", "http://127.0.0.1/\r\nHost: x"):
            self.assertEqual(guard.redact_endpoint(raw), "[REDACTED]")
        self.assertEqual(guard.redact_endpoint("HTTP://LocalHost:8000/v1/"), "http://localhost:8000/v1")

    def test_model_id_alphabet(self):
        self.assertEqual(guard.validate_model_id("org/model-7b:q4_K.M@v1"), "org/model-7b:q4_K.M@v1")
        for bad in ("", "a b", "a;b", "a\nb", "x" * 300, None, "$(id)", "a`b"):
            self.assertIsNone(guard.validate_model_id(bad))


# ---------------------------------------------------------------------------
# Address classification / resolution policy
# ---------------------------------------------------------------------------


class TestClassifyIp(unittest.TestCase):
    def test_classes(self):
        table = {
            "127.0.0.1": "loopback",
            "127.5.5.5": "loopback",
            "::1": "loopback",
            "::ffff:127.0.0.1": "loopback",
            "10.1.2.3": "private",
            "172.16.0.1": "private",
            "172.31.255.255": "private",
            "192.168.1.1": "private",
            "::ffff:10.0.0.1": "private",
            "fd12:3456::1": "private",
            "fc00::1": "private",
            "172.32.0.1": "public_address",
            "8.8.8.8": "public_address",
            "::ffff:8.8.8.8": "public_address",
            "2001:4860:4860::8888": "public_address",
            "169.254.169.254": "metadata_address",
            "::ffff:169.254.169.254": "metadata_address",
            "fd00:ec2::254": "metadata_address",
            "100.100.100.200": "metadata_address",
            "169.254.170.2": "metadata_address",
            "169.254.1.1": "link_local_address",
            "fe80::1": "link_local_address",
            "0.0.0.0": "unspecified_address",
            "0.1.2.3": "unspecified_address",
            "::": "unspecified_address",
            "224.0.0.1": "multicast_address",
            "ff02::1": "multicast_address",
            "100.64.0.1": "reserved_address",
            "192.0.2.1": "reserved_address",
            "255.255.255.255": "reserved_address",
            "64:ff9b::7f00:1": "reserved_address",
            "2002:7f00:1::1": "reserved_address",
            "::7f00:1": "reserved_address",
            "not-an-ip": "unparseable_address",
            "fe80::1%eth0": "unparseable_address",
            "": "unparseable_address",
        }
        for value, expected in table.items():
            self.assertEqual(guard.classify_ip(value)[1], expected, value)

    def test_ipv4_mapped_is_unwrapped_to_canonical_ipv4(self):
        self.assertEqual(guard.classify_ip("::ffff:127.0.0.1"), ("127.0.0.1", "loopback"))
        self.assertEqual(guard.classify_ip("::ffff:7f00:1"), ("127.0.0.1", "loopback"))

    def test_scope(self):
        self.assertTrue(guard.address_allowed("loopback", guard.SCOPE_LOOPBACK))
        self.assertFalse(guard.address_allowed("private", guard.SCOPE_LOOPBACK))
        self.assertTrue(guard.address_allowed("private", guard.SCOPE_PRIVATE))
        for klass in ("public_address", "metadata_address", "link_local_address", "reserved_address", "unspecified_address", "multicast_address"):
            self.assertFalse(guard.address_allowed(klass, guard.SCOPE_PRIVATE))


class TestPolicyBlocksBeforeAnyRequest(unittest.TestCase):
    """policy-layer fail => pinned_http.get is never reached (no request)."""

    def run_policy(self, answers, scope="private", url="http://model.test:8000"):
        with NoNetwork():
            return probe(url, resolver=FakeResolver(answers), scope=scope)

    def assertBlocked(self, result, reason):
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["ready"])
        self.assertEqual(result["layers"]["policy"]["status"], "fail")
        self.assertEqual(result["layers"]["policy"]["reason"], reason)
        self.assertEqual(result["layers"]["network"]["status"], "not_applicable")

    def test_public_literal_ip(self):
        with NoNetwork():
            result = probe("http://8.8.8.8:8000")
        self.assertBlocked(result, "public_address")

    def test_public_hostname(self):
        self.assertBlocked(self.run_policy(["93.184.216.34"]), "public_address")

    def test_metadata_link_local_multicast_unspecified(self):
        self.assertBlocked(self.run_policy(["169.254.169.254"]), "metadata_address")
        self.assertBlocked(self.run_policy(["169.254.10.10"]), "link_local_address")
        self.assertBlocked(self.run_policy(["fe80::1"]), "link_local_address")
        self.assertBlocked(self.run_policy(["224.0.0.251"]), "multicast_address")
        self.assertBlocked(self.run_policy(["0.0.0.0"]), "unspecified_address")

    def test_ipv6_and_ipv4_mapped_ipv6(self):
        self.assertBlocked(self.run_policy(["2606:4700::1111"]), "public_address")
        self.assertBlocked(self.run_policy(["::ffff:8.8.8.8"]), "public_address")
        self.assertBlocked(self.run_policy(["::ffff:169.254.169.254"]), "metadata_address")
        with NoNetwork():
            self.assertBlocked(probe("http://[::ffff:8.8.8.8]:8000"), "public_address")

    def test_every_resolved_address_is_checked(self):
        self.assertBlocked(self.run_policy(["127.0.0.1", "93.184.216.34"]), "public_address")
        self.assertBlocked(self.run_policy(["93.184.216.34", "127.0.0.1"]), "public_address")
        self.assertBlocked(self.run_policy(["10.0.0.5", "::ffff:169.254.169.254"]), "metadata_address")

    def test_loopback_scope_rejects_private_ranges(self):
        self.assertBlocked(self.run_policy(["10.0.0.5"], scope="loopback"), "private_address_outside_scope")

    def test_private_scope_allows_rfc1918_and_ula(self):
        for address in ("10.0.0.5", "192.168.1.9", "fd12::5"):
            with self.subTest(address=address):
                # policy passes; the (refused) connection is what fails afterwards
                result = probe("http://model.test:1", resolver=FakeResolver([address]), timeout=0.5)
                self.assertEqual(result["layers"]["policy"]["status"], "pass")

    def test_resolution_failure_and_timeout(self):
        result = self.run_policy(socket.gaierror("nope"))
        self.assertEqual(result["layers"]["policy"]["reason"], "dns_resolution_failed")
        self.assertEqual(result["status"], "fail")
        result = self.run_policy([])
        self.assertEqual(result["layers"]["policy"]["reason"], "dns_resolution_failed")

        def hang(host, port):
            time.sleep(2)
            return ["127.0.0.1"]

        started = time.monotonic()
        with NoNetwork():
            result = probe("http://model.test:8000", resolver=hang, timeout=0.3)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(result["layers"]["policy"]["status"], "unknown")
        self.assertEqual(result["layers"]["policy"]["reason"], "dns_timeout")
        self.assertFalse(result["ready"])

    def test_system_resolver_is_not_used_for_ip_literals(self):
        with mock.patch.object(guard, "system_resolver", side_effect=AssertionError("resolver used")):
            ep = guard.parse_endpoint("http://127.0.0.1:9")
            self.assertEqual(guard.resolve_and_vet(ep, "private", None, 1.0), ["127.0.0.1"])


# ---------------------------------------------------------------------------
# End-to-end against the stub
# ---------------------------------------------------------------------------


class TestHappyPath(unittest.TestCase):
    def test_ready_when_model_is_advertised(self):
        with Stub() as stub:
            result = probe(stub.url)
        self.assertEqual(result["status"], "pass", layer_states(result))
        self.assertTrue(result["ready"])
        states = layer_states(result)
        self.assertEqual(states["config"][0], "pass")
        self.assertEqual(states["policy"][0], "pass")
        self.assertEqual(states["network"][0], "pass")
        self.assertEqual(states["tls"], ("not_applicable", "plaintext_http_loopback"))
        self.assertEqual(states["api"][0], "pass")
        self.assertEqual(states["model"][0], "pass")
        self.assertEqual(states["freshness"][0], "pass")
        self.assertEqual(states["active_probe"], ("not_applicable", "active_probe_not_implemented"))
        self.assertEqual(result["limits"]["prompt_sent"], False)
        self.assertEqual(result["limits"]["credentials_sent"], False)

    def test_exactly_one_allowlisted_read_only_request_without_credentials(self):
        with Stub() as stub:
            probe(stub.url)
        self.assertEqual(len(stub.requests), 1)
        req = stub.requests[0]
        self.assertEqual((req["method"], req["path"]), ("GET", "/v1/models"))
        for forbidden in ("authorization", "cookie", "proxy-authorization", "x-api-key", "content-length", "transfer-encoding"):
            self.assertNotIn(forbidden, req["headers"])
        self.assertEqual(req["headers"]["accept-encoding"], "identity")
        self.assertEqual(req["headers"]["host"], f"127.0.0.1:{stub.port}")

    def test_environment_credentials_and_proxies_are_never_used(self):
        env = {
            "HTTP_PROXY": "http://127.0.0.1:9",
            "http_proxy": "http://127.0.0.1:9",
            "OPENAI_API_KEY": "CANARY-openai-never-sent",
            "HF_TOKEN": "CANARY-hf-never-sent",
        }
        with Stub() as stub, mock.patch.dict(os.environ, env):
            result = probe(stub.url)
        self.assertTrue(result["ready"])
        self.assertEqual(len(stub.requests), 1)
        blob = json.dumps(stub.requests)
        self.assertNotIn("never-sent", blob)

    def test_base_path_prefix(self):
        with Stub() as stub:
            probe(stub.url + "/v1")
            probe(stub.url + "/gateway/")
        self.assertEqual([r["path"] for r in stub.requests], ["/v1/models", "/gateway/v1/models"])

    def test_dns_name_uses_vetted_address_and_original_host_header(self):
        with Stub() as stub:
            resolver = FakeResolver(["127.0.0.1"])
            result = probe(f"http://model.test:{stub.port}", resolver=resolver)
        self.assertTrue(result["ready"], layer_states(result))
        self.assertEqual(resolver.calls, [("model.test", stub.port)])
        self.assertEqual(stub.requests[0]["headers"]["host"], f"model.test:{stub.port}")

    def test_ipv4_mapped_loopback_connects_via_unwrapped_ipv4(self):
        with Stub() as stub:
            result = probe(f"http://model.test:{stub.port}", resolver=FakeResolver(["::ffff:127.0.0.1"]))
        self.assertTrue(result["ready"], layer_states(result))

    def test_non_loopback_private_http_is_flagged_but_not_a_failure(self):
        layer = ie._plaintext_layer({"private"}, "2026-01-01T00:00:00Z")
        self.assertEqual(layer["status"], "not_applicable")
        self.assertEqual(layer["reason"], "plaintext_http_non_loopback")
        self.assertIn("https", layer["remediation"])


class TestNotConfiguredAndInvalid(unittest.TestCase):
    def test_nothing_configured_is_not_applicable_and_not_ready(self):
        with NoNetwork():
            for empty in (None, ""):
                result = ie.probe(empty, "demo-model")
                self.assertEqual(result["status"], "not_applicable")
                self.assertFalse(result["ready"])
                self.assertIsNone(result["endpoint"])
                self.assertEqual(result["layers"]["config"]["reason"], "endpoint_not_configured")
                for name in ("policy", "network", "tls", "api", "model", "freshness"):
                    self.assertEqual(result["layers"][name]["status"], "not_applicable")
                    self.assertIsNone(result["layers"][name]["remediation"])

    def test_invalid_endpoints_fail_in_config_without_a_request(self):
        with NoNetwork():
            for raw, reason in (
                ("file:///etc/passwd", "scheme_not_allowed"),
                ("http://a@b", "userinfo_not_allowed"),
                ("http://127.0.0.1:1/\r\nX: y", "invalid_url"),
                ("http://2130706433/", "non_canonical_ip_host"),
            ):
                result = ie.probe(raw, "demo-model")
                self.assertEqual(result["status"], "fail", raw)
                self.assertEqual(result["layers"]["config"]["reason"], reason, raw)
                self.assertEqual(result["endpoint"], "[REDACTED]")
                self.assertFalse(result["ready"])

    def test_invalid_model_id_and_scope(self):
        with NoNetwork():
            self.assertEqual(ie.probe("http://127.0.0.1:1", "a b;c")["layers"]["config"]["reason"], "invalid_model_id")
            self.assertEqual(ie.probe("http://127.0.0.1:1", "m", scope="public")["layers"]["config"]["reason"], "invalid_scope")

    def test_missing_ca_file_fails_closed_for_https(self):
        with NoNetwork():
            result = ie.probe("https://localhost:8443", "m", ca_file="/nonexistent/ca.pem")
        self.assertEqual(result["layers"]["config"]["reason"], "ca_file_invalid")
        self.assertFalse(result["ready"])


class TestResponseHandling(unittest.TestCase):
    def check(self, expected_status, api_reason, model="demo-model", **cfg):
        with Stub(**cfg) as stub:
            result = probe(stub.url, model)
        self.assertFalse(result["ready"], layer_states(result))
        self.assertEqual(result["status"], expected_status, layer_states(result))
        self.assertEqual(result["layers"]["api"].get("reason"), api_reason, layer_states(result))
        return result

    def test_redirects_are_never_followed(self):
        with Stub() as target:
            with Stub(status=302, headers={"Location": f"http://127.0.0.1:{target.port}/v1/models"}) as redirector:
                result = probe(redirector.url)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["layers"]["api"]["reason"], "redirect_not_followed")
        self.assertEqual(len(redirector.requests), 1)
        self.assertEqual(target.requests, [])
        for code in (301, 303, 307, 308):
            self.check("fail", "redirect_not_followed", status=code, headers={"Location": "http://169.254.169.254/latest"})

    def test_redirect_location_is_not_echoed(self):
        result = self.check("fail", "redirect_not_followed", status=302, headers={"Location": "http://169.254.169.254/latest/meta-data"})
        self.assertNotIn("169.254", json.dumps(result))

    def test_auth_required_is_unknown(self):
        for code in (401, 403):
            result = self.check("unknown", "auth_required", status=code, body=b'{"error":"unauthorized"}')
            self.assertEqual(result["layers"]["model"]["status"], "not_applicable")

    def test_unsupported_api_and_server_errors(self):
        self.check("fail", "unsupported_api", status=404, body=b"not found")
        self.check("fail", "unsupported_api", status=405)
        self.check("fail", "server_error", status=500)
        self.check("fail", "server_error", status=503)
        self.check("fail", "http_status", status=418)
        self.check("fail", "unexpected_status", status=204, body=b"")

    def test_malformed_and_unexpected_json(self):
        self.check("fail", "malformed_json", body=b"{not json")
        self.check("fail", "malformed_json", body=b"\xff\xfe")
        self.check("fail", "malformed_json", body=b"[" * 200000)  # recursion bomb, under the size cap
        self.check("fail", "invalid_response", body=b'{"data": "x"}')
        self.check("fail", "invalid_response", body=b'["a"]')
        self.check("fail", "invalid_response", body=b'{"data": [{"name": "no-id"}]}')
        self.check("fail", "invalid_response", body=b'{"data": [{"id": 7}]}')
        self.check("fail", "invalid_response", body=b'{"models": [{"id": "demo-model"}]}')
        self.check("fail", "invalid_response", headers={"Content-Encoding": "gzip"})

    def test_oversized_declared_and_streamed_bodies(self):
        with mock.patch.object(ie, "MAX_RESPONSE_BYTES", 2048):
            self.check("fail", "response_too_large", body=b"x" * 5000)
            self.check("fail", "response_too_large", endless=True)
            huge = json.dumps({"data": [{"id": "demo-model"}], "pad": "x" * 5000}).encode()
            self.check("fail", "response_too_large", body=huge)

    def test_oversized_response_is_bounded_in_time_and_memory(self):
        started = time.monotonic()
        self.check("fail", "response_too_large", endless=True)
        self.assertLess(time.monotonic() - started, 5)

    def test_timeout_is_unknown_not_ready(self):
        started = time.monotonic()
        with Stub(delay=3) as stub:
            result = probe(stub.url, timeout=0.5)
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["ready"])
        self.assertEqual(result["layers"]["api"]["status"], "unknown")
        self.assertEqual(result["layers"]["api"]["reason"], "timeout")

    def test_slow_drip_cannot_extend_the_deadline(self):
        started = time.monotonic()
        with Stub(drip=0.2, body=MODELS_OK) as stub:
            result = probe(stub.url, timeout=0.8)
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["layers"]["api"]["reason"], "timeout")

    def test_connection_refused_is_fail(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        result = probe(f"http://127.0.0.1:{port}")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["layers"]["network"]["reason"], "connection_refused")
        self.assertFalse(result["ready"])

    def test_connection_that_closes_without_a_response(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)

        def accept_and_close():
            conn, _ = listener.accept()
            conn.close()

        t = threading.Thread(target=accept_and_close, daemon=True)
        t.start()
        try:
            result = probe(f"http://127.0.0.1:{listener.getsockname()[1]}")
        finally:
            listener.close()
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["layers"]["api"]["reason"], "connection_reset")

    def test_non_http_garbage_is_invalid_response(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)

        def serve():
            conn, _ = listener.accept()
            conn.recv(4096)
            conn.sendall(b"\x16\x03\x01garbage-not-http\r\n\r\n")
            conn.close()

        threading.Thread(target=serve, daemon=True).start()
        try:
            result = probe(f"http://127.0.0.1:{listener.getsockname()[1]}")
        finally:
            listener.close()
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["layers"]["api"]["reason"], "invalid_response")

    def test_model_missing_not_configured_and_present(self):
        with Stub() as stub:
            missing = probe(stub.url, "no-such-model")
            unset = probe(stub.url, "")
            present = probe(stub.url, "other")
            prefix = probe(stub.url, "demo")  # ids are matched exactly, never by prefix
        self.assertEqual((missing["status"], missing["layers"]["model"]["reason"]), ("fail", "model_missing"))
        self.assertEqual((unset["status"], unset["layers"]["model"]["reason"]), ("unknown", "model_not_configured"))
        self.assertEqual(unset["layers"]["api"]["status"], "pass")
        self.assertFalse(missing["ready"] or unset["ready"])
        self.assertTrue(present["ready"])
        self.assertEqual(prefix["status"], "fail")

    def test_http_200_alone_never_means_ready(self):
        with Stub(body=b'{"data": []}') as stub:
            result = probe(stub.url)
        self.assertEqual(result["layers"]["api"]["status"], "pass")
        self.assertFalse(result["ready"])
        self.assertEqual(result["layers"]["model"]["reason"], "model_missing")


class TestDnsRebinding(unittest.TestCase):
    def test_resolver_is_consulted_once_and_the_vetted_address_is_used(self):
        # First answer is private (vetted), any later answer would be a public address.
        with Stub() as stub:
            resolver = FakeResolver(["127.0.0.1"], ["93.184.216.34"])
            result = probe(f"http://rebind.test:{stub.port}", resolver=resolver)
        self.assertEqual(len(resolver.calls), 1)
        self.assertTrue(result["ready"], layer_states(result))
        self.assertEqual(len(stub.requests), 1)
        self.assertEqual(stub.requests[0]["headers"]["host"], f"rebind.test:{stub.port}")

    def test_public_first_answer_is_rejected_even_if_later_answer_is_private(self):
        with Stub() as stub:
            resolver = FakeResolver(["93.184.216.34"], ["127.0.0.1"])
            result = probe(f"http://rebind.test:{stub.port}", resolver=resolver)
        self.assertEqual(len(resolver.calls), 1)
        self.assertEqual(result["layers"]["policy"]["reason"], "public_address")
        self.assertEqual(stub.requests, [])

    def test_get_refuses_an_unexpected_peer(self):
        # Defence in depth: if the OS ever connected somewhere other than the
        # vetted address, the response is not used.
        real_socket = socket.socket

        class Wrapper:
            def __init__(self, family, kind):
                self.inner = real_socket(family, kind)

            def __getattr__(self, name):
                return getattr(self.inner, name)

            def getpeername(self):
                return ("10.9.9.9", 1)

        with Stub() as stub, mock.patch.object(pinned_http.socket, "socket", Wrapper):
            result = probe(stub.url)
        self.assertEqual(result["layers"]["network"]["reason"], "peer_mismatch")
        self.assertFalse(result["ready"])


class TestFreshness(unittest.TestCase):
    def http_date(self, offset_seconds):
        from email.utils import formatdate

        return formatdate(time.time() + offset_seconds, usegmt=True)

    def test_server_clock_skew_makes_evidence_unknown(self):
        for offset in (3600, -3600):
            with Stub(headers={"Date": self.http_date(offset)}, omit_date=True) as stub:
                result = probe(stub.url)
            self.assertEqual(result["layers"]["freshness"]["reason"], "clock_skew_exceeded")
            self.assertEqual(result["status"], "unknown")
            self.assertFalse(result["ready"])
            self.assertEqual(result["layers"]["api"]["status"], "pass")

    def test_small_skew_is_tolerated(self):
        with Stub(headers={"Date": self.http_date(30)}, omit_date=True) as stub:
            result = probe(stub.url)
        self.assertTrue(result["ready"])

    def test_invalid_date_is_unknown_and_missing_date_is_not_applicable(self):
        with Stub(headers={"Date": "yesterday-ish"}, omit_date=True) as stub:
            invalid = probe(stub.url)
        self.assertEqual(invalid["layers"]["freshness"]["reason"], "server_date_invalid")
        self.assertFalse(invalid["ready"])
        with Stub(omit_date=True) as stub:
            missing = probe(stub.url)
        self.assertEqual(missing["layers"]["freshness"]["reason"], "server_date_unavailable")
        self.assertTrue(missing["ready"])

    def test_checked_at_uses_the_injected_clock_and_freshness_block(self):
        fixed = datetime.datetime(2026, 10, 14, 12, 0, 0, tzinfo=datetime.timezone.utc)
        with NoNetwork():
            result = ie.probe("http://8.8.8.8:1", "m", now=lambda: fixed)
        self.assertEqual(result["checked_at"], "2026-10-14T12:00:00Z")
        self.assertEqual(result["freshness"]["expires_at"], "2026-10-14T12:05:00Z")
        self.assertEqual(result["layers"]["policy"]["checked_at"], "2026-10-14T12:00:00Z")

    def ready_result(self):
        with Stub() as stub:
            return probe(stub.url)

    def test_revalidate_downgrades_stale_future_and_unparseable_evidence(self):
        result = self.ready_result()
        checked = ie._parse_iso(result["checked_at"])
        original = json.dumps(result, sort_keys=True)

        fresh = ie.revalidate(result, now=lambda: checked + datetime.timedelta(seconds=10))
        self.assertTrue(fresh["ready"])
        self.assertEqual(fresh["freshness"]["state"], "fresh")

        stale = ie.revalidate(result, now=lambda: checked + datetime.timedelta(seconds=ie.MAX_AGE_SECONDS + 1))
        self.assertFalse(stale["ready"])
        self.assertEqual(stale["status"], "unknown")
        self.assertEqual(stale["freshness"]["state"], "stale")

        future = ie.revalidate(result, now=lambda: checked - datetime.timedelta(seconds=ie.MAX_CLOCK_SKEW_SECONDS + 60))
        self.assertFalse(future["ready"])
        self.assertEqual(future["freshness"]["state"], "future_timestamp")

        broken = dict(result, checked_at="not-a-time")
        out = ie.revalidate(broken)
        self.assertFalse(out["ready"])
        self.assertEqual(out["freshness"]["state"], "unparseable_timestamp")

        # Input is never mutated; a non-ready result keeps its (worse) status.
        self.assertEqual(json.dumps(result, sort_keys=True), original)
        failed = ie.probe("file:///x")
        again = ie.revalidate(failed, now=lambda: ie._parse_iso(failed["checked_at"]) + datetime.timedelta(days=2))
        self.assertEqual(again["status"], "fail")
        self.assertFalse(again["ready"])


class TestRedactionAndOutputHygiene(unittest.TestCase):
    SECRETS = ("hunter2", "admin", "sk-secret", "S3CRETFRAG", "tok3n", "Bearer", "INJECTED")

    def test_credentials_in_the_endpoint_never_reach_output(self):
        for raw in (
            "http://admin:hunter2@127.0.0.1:9/v1?key=sk-secret#S3CRETFRAG",
            "http://admin:hunter2@127.0.0.1:9/",
            "https://127.0.0.1:9/v1?api_key=sk-secret",
            "http://127.0.0.1:9/#S3CRETFRAG",
            "http://127.0.0.1:9/\r\nAuthorization: Bearer tok3n\r\nX: INJECTED",
        ):
            with NoNetwork():
                result = ie.probe(raw, "demo-model")
            blob = json.dumps(result)
            for secret in self.SECRETS:
                self.assertNotIn(secret, blob, raw)
            self.assertEqual(result["endpoint"], "[REDACTED]")

    def test_invalid_model_id_is_not_echoed(self):
        with NoNetwork():
            result = ie.probe("http://127.0.0.1:1", "sk-secret;$(id)")
        self.assertNotIn("sk-secret", json.dumps(result))

    def test_invalid_scope_value_is_not_echoed(self):
        with NoNetwork():
            result = ie.probe("http://127.0.0.1:1", "m", scope="sk-secret")
        self.assertNotIn("sk-secret", json.dumps(result))
        self.assertEqual(result["limits"]["scope"], "invalid")

    def test_every_layer_follows_the_health_model(self):
        with Stub() as stub:
            result = probe(stub.url)
        for name, layer in result["layers"].items():
            self.assertIn(layer["status"], ("pass", "fail", "not_applicable", "unknown"), name)
            for key in ("signals", "proves", "remediation", "detail", "authority", "source", "compatibility_fallback"):
                self.assertIn(key, layer, name)
            self.assertTrue(layer["proves"], name)
            self.assertIn(layer["authority"], ("omes-host", "external-provider"), name)
            self.assertIs(layer["compatibility_fallback"], False)
        self.assertEqual(list(result["layers"]), list(ie.LAYER_ORDER))
        self.assertEqual(result["layers"]["api"]["authority"], "external-provider")
        self.assertEqual(result["layers"]["policy"]["authority"], "omes-host")
        self.assertIn("Not implemented yet (tracked in #292)", result["layers"]["active_probe"]["proves"])

    def test_every_failure_reason_has_redacted_remediation(self):
        for reason in (
            "public_address", "metadata_address", "link_local_address", "connection_refused", "timeout",
            "tls_untrusted_certificate", "auth_required", "unsupported_api", "redirect_not_followed",
            "response_too_large", "malformed_json", "invalid_response", "model_missing", "model_not_configured",
            "clock_skew_exceeded", "scheme_not_allowed", "userinfo_not_allowed",
        ):
            self.assertTrue(ie._REMEDIATION.get(reason), reason)
            self.assertNotRegex(ie._REMEDIATION[reason], r"[A-Za-z0-9+/]{32,}")


# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------


@unittest.skipUnless(shutil.which("openssl"), "openssl binary required to mint a throwaway test certificate")
class TestTls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="omes-t292-")
        cls.cert = os.path.join(cls.tmp, "cert.pem")
        cls.key = os.path.join(cls.tmp, "key.pem")
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", cls.key, "-out", cls.cert,
                "-days", "2", "-subj", "/CN=localhost",
                "-addext", "subjectAltName=DNS:localhost,DNS:model.test,IP:127.0.0.1",
            ],
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def tls_stub(self, **cfg):
        return Stub(tls={"cert": self.cert, "key": self.key}, **cfg)

    def test_untrusted_certificate_fails_by_default(self):
        with self.tls_stub() as stub:
            result = probe(f"https://127.0.0.1:{stub.port}")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["layers"]["tls"]["reason"], "tls_untrusted_certificate")
        self.assertEqual(result["layers"]["network"]["status"], "pass")
        self.assertEqual(result["layers"]["api"]["status"], "not_applicable")
        self.assertEqual(stub.requests, [])  # no HTTP request over an unverified channel
        self.assertFalse(result["ready"])

    def test_trusted_ca_file_passes_with_verification_enabled(self):
        with self.tls_stub() as stub:
            result = probe(f"https://127.0.0.1:{stub.port}", ca_file=self.cert)
        self.assertTrue(result["ready"], layer_states(result))
        self.assertEqual(result["layers"]["tls"]["status"], "pass")
        self.assertRegex(result["layers"]["tls"]["detail"], r"TLSv1\.[23]")

    def test_original_hostname_is_used_for_sni_host_header_and_verification(self):
        with self.tls_stub() as stub:
            resolver = FakeResolver(["127.0.0.1"])
            result = probe(f"https://model.test:{stub.port}", resolver=resolver, ca_file=self.cert)
        self.assertTrue(result["ready"], layer_states(result))
        self.assertEqual(stub.sni, ["model.test"])
        self.assertEqual(stub.requests[0]["headers"]["host"], f"model.test:{stub.port}")

    def test_hostname_mismatch_fails(self):
        with self.tls_stub() as stub:
            result = probe(f"https://other.test:{stub.port}", resolver=FakeResolver(["127.0.0.1"]), ca_file=self.cert)
        self.assertEqual(result["layers"]["tls"]["reason"], "tls_hostname_mismatch")
        self.assertFalse(result["ready"])
        self.assertEqual(stub.requests, [])

    def test_https_to_a_plain_http_server_is_a_handshake_failure(self):
        with Stub() as stub:
            result = probe(f"https://127.0.0.1:{stub.port}", ca_file=self.cert)
        self.assertEqual(result["layers"]["tls"]["status"], "fail")
        self.assertEqual(result["layers"]["tls"]["reason"], "tls_handshake_failed")
        self.assertFalse(result["ready"])

    def test_http_to_a_tls_server_is_not_ready(self):
        with self.tls_stub() as stub:
            result = probe(f"http://127.0.0.1:{stub.port}", timeout=1.5)
        self.assertFalse(result["ready"])
        self.assertIn(result["status"], ("fail", "unknown"))

    def test_expired_and_other_verify_codes_map_to_stable_reasons(self):
        for code, reason in ((10, "tls_certificate_expired"), (62, "tls_hostname_mismatch"), (18, "tls_untrusted_certificate"), (20, "tls_untrusted_certificate")):
            exc = ssl.SSLCertVerificationError(1, "verify failed")
            exc.verify_code = code
            self.assertEqual(pinned_http._tls_reason(exc), reason)
        self.assertEqual(pinned_http._tls_reason(ssl.SSLError(1, "x")), "tls_handshake_failed")

    def test_context_never_disables_verification(self):
        ctx = pinned_http.make_ssl_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)
        self.assertGreaterEqual(ctx.minimum_version, ssl.TLSVersion.TLSv1_2)
        with open(os.path.join(HEALTH_DIR, "pinned_http.py"), encoding="utf-8") as fh:
            src = fh.read()
        for needle in ("CERT_NONE", "_create_unverified_context", "check_hostname = False", "verify=False"):
            self.assertNotIn(needle, src)


# ---------------------------------------------------------------------------
# CLI wiring (python entry point) and env handling
# ---------------------------------------------------------------------------


class TestCli(unittest.TestCase):
    def run_script(self, args, env_extra=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("OMES_INFERENCE") and k not in ("OMES_HEALTH_TIMEOUT",)}
        env["OMES_HEALTH_TIMEOUT"] = "3"
        env.update(env_extra or {})
        return subprocess.run([sys.executable, "-I", SCRIPT] + args, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=30)

    def test_ready_exit_zero_with_env_reference(self):
        with Stub() as stub:
            proc = self.run_script([], {"OMES_INFERENCE_ENDPOINT": stub.url, "OMES_INFERENCE_MODEL": "demo-model"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = json.loads(proc.stdout)
        self.assertTrue(data["ready"])
        self.assertEqual(data["status"], "pass")
        self.assertEqual(data["endpoint"], stub.url)
        self.assertEqual(proc.stdout.count("\n"), 1)

    def test_flags_override_env(self):
        with Stub() as stub:
            proc = self.run_script(["--endpoint", stub.url, "--model", "other", "--scope", "loopback"], {"OMES_INFERENCE_ENDPOINT": "http://8.8.8.8", "OMES_INFERENCE_MODEL": "x"})
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_not_configured_is_exit_7_with_status_not_applicable(self):
        proc = self.run_script([])
        self.assertEqual(proc.returncode, 7)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "not_applicable")
        self.assertFalse(data["ready"])

    def test_failure_exit_7_and_secret_free_output(self):
        proc = self.run_script(["--endpoint", "http://admin:hunter2@127.0.0.1:9/"])
        self.assertEqual(proc.returncode, 7)
        self.assertNotIn("hunter2", proc.stdout + proc.stderr)

    def test_usage_error_exits_2(self):
        proc = self.run_script(["--scope", "public"])
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")

    def test_timeout_env_is_clamped(self):
        self.assertEqual(ie._timeout_from_env(None), 10.0)
        self.assertEqual(ie._timeout_from_env("abc"), 10.0)
        self.assertEqual(ie._timeout_from_env("nan"), 10.0)
        self.assertEqual(ie._timeout_from_env("inf"), 10.0)
        self.assertEqual(ie._timeout_from_env("-3"), 10.0)
        self.assertEqual(ie._timeout_from_env("0.001"), ie.MIN_TIMEOUT_SECONDS)
        self.assertEqual(ie._timeout_from_env("9999"), ie.MAX_TIMEOUT_SECONDS)
        self.assertEqual(ie._timeout_from_env("2.5"), 2.5)

    def test_run_accepts_injected_env_without_touching_os_environ(self):
        with Stub() as stub:
            result = ie.run([], env={"OMES_INFERENCE_ENDPOINT": stub.url, "OMES_INFERENCE_MODEL": "demo-model", "OMES_HEALTH_TIMEOUT": "3"})
        self.assertTrue(result["ready"])

    def test_module_never_reads_hermes_state_or_credentials(self):
        for name in ("endpoint_guard.py", "pinned_http.py", "inference_endpoint.py"):
            with open(os.path.join(HEALTH_DIR, name), encoding="utf-8") as fh:
                src = fh.read()
            for needle in ("messages.db", "sqlite3", "HERMES_HOME", ".hermes", "Authorization", "subprocess", "urllib.request"):
                self.assertNotIn(needle, src.replace("no Authorization/Cookie header", ""), f"{name}: {needle}")


# ---------------------------------------------------------------------------
# Shared health model and `omes health ollama` regressions
# ---------------------------------------------------------------------------


class TestHealthModelCompatibility(unittest.TestCase):
    def test_layer_result_is_unchanged_for_existing_callers(self):
        res = health_model.layer_result(health_model.STATUS_PASS, proves="p")
        self.assertEqual(
            res,
            {
                "status": "pass",
                "signals": {},
                "proves": "p",
                "remediation": None,
                "detail": "",
                "authority": "omes-host",
                "source": "omes",
                "compatibility_fallback": False,
            },
        )
        self.assertNotIn("reason", res)
        self.assertNotIn("checked_at", res)

    def test_optional_fields_are_emitted_only_when_given(self):
        res = health_model.layer_result(health_model.STATUS_UNKNOWN, reason="timeout", checked_at="2026-01-01T00:00:00Z")
        self.assertEqual((res["status"], res["reason"], res["checked_at"]), ("unknown", "timeout", "2026-01-01T00:00:00Z"))

    def test_unknown_blocks_readiness_fail_closed(self):
        unknown = health_model.layer_result(health_model.STATUS_UNKNOWN)
        ok = health_model.layer_result(health_model.STATUS_PASS)
        layers = {"host": ok, "runtime": ok, "gateway": ok, "provider": ok, "channel": ok}
        self.assertEqual(health_model.aggregate(layers), (True, True))
        for name in ("host", "runtime", "gateway", "provider"):
            self.assertEqual(health_model.aggregate(dict(layers, **{name: unknown}))[0], False, name)
        self.assertEqual(health_model.aggregate(dict(layers, channel=unknown))[1], False)


class TestOllamaHealthUnchanged(unittest.TestCase):
    """`omes health ollama` (issue #71) must be byte-for-byte unaffected."""

    @classmethod
    def setUpClass(cls):
        import ollama  # noqa: PLC0415 - loaded lazily, exactly as hermes.py does

        cls.ollama = ollama

    def setUp(self):
        self._env = dict(os.environ)
        for key in list(os.environ):
            if key.startswith("OMES_OLLAMA") or key == "OLLAMA_HOST":
                del os.environ[key]

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def run_ollama(self, fake):
        with mock.patch.object(self.ollama, "request_json", fake), mock.patch.object(self.ollama.shutil, "which", return_value="/usr/bin/ollama"):
            return self.ollama.run(["--model", "llama3"])

    def test_ready_result_shape_is_golden(self):
        responses = {
            "/api/version": {"version": "0.1.0"},
            "/api/tags": {"models": [{"name": "llama3:latest"}]},
            "/api/generate": {"response": "pong"},
            "/api/ps": {"models": [{"name": "llama3", "processor": "100% GPU"}]},
        }

        def fake(url, method="GET", payload=None, timeout=10.0):
            return responses[url.split("11434", 1)[1]]

        expected = {
            "provider": "ollama-local",
            "profile": "text",
            "service": {"status": "pass"},
            "model": {"id": "llama3", "status": "pass", "processor": "100% GPU"},
            "capabilities": {"chat": "pass", "structured_output": "not_applicable", "tool_calling": "not_applicable", "embeddings": "not_applicable", "vision": "not_applicable"},
            "checks": [
                {"name": "service:binary", "status": "pass", "detail": "ollama binary found on PATH", "remediation": None},
                {"name": "service:bind_policy", "status": "pass", "detail": "endpoint host '127.0.0.1' is loopback", "remediation": None},
                {"name": "service:version", "status": "pass", "detail": "reachable, version=0.1.0", "remediation": None},
                {"name": "model:present", "status": "pass", "detail": "model 'llama3' found in local catalog (1 model(s) present)", "remediation": None},
                {"name": "model:load_and_smoke", "status": "pass", "detail": "model loaded and returned a non-empty response", "remediation": None},
                {"name": "model:placement", "status": "pass", "detail": "observed placement='100% GPU' (no expectation set)", "remediation": None},
                {"name": "capability:chat", "status": "pass", "detail": "non-empty response", "remediation": None},
            ],
            "ready": True,
            "exit_code": 0,
        }
        self.assertEqual(self.run_ollama(fake), expected)

    def test_unreachable_result_shape_is_golden(self):
        def fake(url, method="GET", payload=None, timeout=10.0):
            raise self.ollama.HttpJsonError("unreachable", "refused")

        result = self.run_ollama(fake)
        self.assertEqual(result["exit_code"], 4)
        self.assertEqual(result["service"], {"status": "fail", "reason": "unreachable"})
        self.assertEqual(result["model"], {"id": "llama3", "status": "not_applicable"})
        self.assertFalse(result["ready"])
        self.assertEqual(sorted(result), ["capabilities", "checks", "exit_code", "model", "profile", "provider", "ready", "service"])

    def test_ollama_and_httpjson_do_not_depend_on_the_new_modules(self):
        for name in ("ollama.py", "httpjson.py", "hermes.py"):
            with open(os.path.join(HEALTH_DIR, name), encoding="utf-8") as fh:
                src = fh.read()
            for new in ("inference_endpoint", "endpoint_guard", "pinned_http"):
                self.assertNotIn(new, src, f"{name} must not reference {new}")
        self.assertIs(self.ollama.request_json.__module__, "httpjson")

    def test_importing_the_new_modules_does_not_change_ollama_endpoint_resolution(self):
        self.assertEqual(self.ollama.resolve_endpoint(), "http://127.0.0.1:11434")
        os.environ["OLLAMA_HOST"] = "10.1.2.3:9"
        self.assertEqual(self.ollama.resolve_endpoint(), "http://10.1.2.3:9")


if __name__ == "__main__":
    unittest.main()
