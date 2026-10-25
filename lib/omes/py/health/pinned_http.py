"""lib/omes/py/health/pinned_http.py - one bounded, read-only HTTP GET to an
already-vetted IP address (issue #292).

Standard library only (ADR-0012). This is deliberately NOT built on
lib/omes/py/health/httpjson.py (urllib): urllib follows redirects, resolves
names itself and cannot cap a response, none of which is acceptable for an
SSRF-sensitive probe. httpjson.py and `omes health ollama` are untouched.

Guarantees, each covered by tests/py/health/test_inference_endpoint.py:

- Connects ONLY to an address from the caller's vetted list (no name
  lookup happens here), and re-checks the connected peer address. The
  original host name is still sent as the HTTP `Host` header and as the
  TLS SNI/verification name, so virtual hosting and certificate checks
  behave as for the real name while DNS rebinding cannot redirect the
  connection.
- TLS (https): TLS >= 1.2, CERT_REQUIRED and hostname verification are
  never disabled. A private CA is supplied via `ca_file`, never by
  turning verification off.
- Redirects are never followed (http.client never does); a 3xx is
  reported to the caller as such.
- Request: a single GET with no body (or, for the opt-in active probe of
  issue #354, a single POST of a fixed JSON body), no Authorization/Cookie header and
  `Accept-Encoding: identity`. No credential is ever sent.
- Total wall-clock deadline for connect + handshake + headers + body,
  enforced by a watchdog that shuts the socket down (a slow-drip server
  cannot extend it by sending one byte per interval), plus a hard
  response-size cap enforced while reading.
- Failures raise ProbeError(layer, status, reason) with a stable code;
  exception text (which could echo the host) is never propagated.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import threading
import time
from typing import Optional

USER_AGENT = "omes-health-inference-endpoint/1"

LAYER_NETWORK = "network"
LAYER_TLS = "tls"
LAYER_API = "api"


class ProbeError(Exception):
    """layer: network|tls|api. status: fail|unknown. reason: stable code."""

    def __init__(self, layer: str, status: str, reason: str):
        self.layer = layer
        self.status = status
        self.reason = reason
        super().__init__(f"{layer}:{reason}")


class Response:
    __slots__ = ("status", "date_header", "body", "tls_version", "peer", "content_encoding")

    def __init__(self, status, date_header, body, tls_version, peer, content_encoding):
        self.status = status
        self.date_header = date_header
        self.body = body
        self.tls_version = tls_version
        self.peer = peer
        self.content_encoding = content_encoding


class _PinnedConnection(http.client.HTTPConnection):
    """HTTPConnection over an already-connected (and, for https, already
    verified) socket; connect() is a no-op so http.client can never open a
    second, unvetted connection."""

    def __init__(self, sock, host: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self.sock = sock
        self.auto_open = 0

    def connect(self) -> None:  # pragma: no cover - never reached (auto_open=0)
        raise OSError("pinned connection cannot reconnect")


def make_ssl_context(ca_file: Optional[str] = None) -> ssl.SSLContext:
    """Verifying client context. Raises ssl.SSLError/OSError when `ca_file`
    cannot be loaded (callers map that to a config failure)."""
    ctx = ssl.create_default_context(cafile=ca_file)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def _tls_reason(exc: ssl.SSLError) -> str:
    code = getattr(exc, "verify_code", None)
    if isinstance(exc, ssl.SSLCertVerificationError):
        if code == 62:  # X509_V_ERR_HOSTNAME_MISMATCH
            return "tls_hostname_mismatch"
        if code == 10:  # X509_V_ERR_CERT_HAS_EXPIRED
            return "tls_certificate_expired"
        return "tls_untrusted_certificate"
    return "tls_handshake_failed"


def get(**kwargs) -> Response:
    """Performs the single allowlisted GET (see request())."""
    return request(method="GET", **kwargs)


def request(
    *,
    method: str = "GET",
    body: Optional[bytes] = None,
    scheme: str,
    host: str,
    host_header: str,
    port: int,
    path: str,
    addresses: list,
    timeout: float,
    max_bytes: int,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> Response:
    """Performs one bounded request: the allowlisted GET, or (issue #354,
    opt-in active probe only) one POST of a fixed JSON `body`. `addresses`
    MUST already be vetted by endpoint_guard.resolve_and_vet; `path` MUST
    already be an allowlisted probe path. The POST carries a Content-Type
    but never any credential header; every other guarantee is shared."""
    if method not in ("GET", "POST"):
        raise ValueError("method not allowed")
    start = time.monotonic()
    state: dict = {"sock": None, "expired": False}

    def kill() -> None:
        state["expired"] = True
        sock = state["sock"]
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    watchdog = threading.Timer(timeout, kill)
    watchdog.daemon = True
    watchdog.start()

    phase = LAYER_NETWORK

    def remaining() -> float:
        left = timeout - (time.monotonic() - start)
        if left <= 0:
            raise ProbeError(phase, "unknown", "timeout")
        return left

    conn: Optional[_PinnedConnection] = None
    sock = None
    try:
        # ---- connect (vetted addresses only) -------------------------
        last_reason = "connect_failed"
        last_status = "fail"
        for address in addresses:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            candidate = socket.socket(family, socket.SOCK_STREAM)
            state["sock"] = candidate
            try:
                candidate.settimeout(remaining())
                candidate.connect((address, port))
            except ProbeError:
                candidate.close()
                raise
            except (socket.timeout, TimeoutError):
                candidate.close()
                last_reason, last_status = "timeout", "unknown"
                continue
            except ConnectionRefusedError:
                candidate.close()
                last_reason, last_status = "connection_refused", "fail"
                continue
            except OSError:
                candidate.close()
                if state["expired"]:
                    last_reason, last_status = "timeout", "unknown"
                else:
                    last_reason, last_status = "connect_failed", "fail"
                continue
            try:
                peer = ipaddress.ip_address(candidate.getpeername()[0])
                expected = ipaddress.ip_address(address)
            except (OSError, ValueError):
                candidate.close()
                last_reason, last_status = "connect_failed", "fail"
                continue
            if peer != expected:  # belt and braces: never talk to an unvetted peer
                candidate.close()
                last_reason, last_status = "peer_mismatch", "fail"
                continue
            sock = candidate
            break
        if sock is None:
            raise ProbeError(LAYER_NETWORK, last_status, last_reason)

        # ---- TLS ------------------------------------------------------
        tls_version = None
        if scheme == "https":
            phase = LAYER_TLS
            if ssl_context is None:
                ssl_context = make_ssl_context()
            try:
                wrapped = ssl_context.wrap_socket(sock, server_hostname=host, do_handshake_on_connect=False)
            except (ssl.SSLError, OSError):
                sock.close()
                raise ProbeError(LAYER_TLS, "fail", "tls_handshake_failed") from None
            sock = wrapped
            state["sock"] = sock
            sock.settimeout(remaining())
            try:
                sock.do_handshake()
            except ssl.SSLError as exc:
                raise ProbeError(LAYER_TLS, "fail", _tls_reason(exc)) from None
            tls_version = sock.version()

        # ---- HTTP -----------------------------------------------------
        phase = LAYER_API
        sock.settimeout(remaining())
        conn = _PinnedConnection(sock, host, port, timeout)
        headers = {
            "Host": host_header,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
            "Connection": "close",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        sock.settimeout(remaining())
        resp = conn.getresponse()
        status = resp.status
        date_header = resp.getheader("Date")
        content_encoding = resp.getheader("Content-Encoding")

        payload = b""
        if status == 200:
            declared = resp.getheader("Content-Length")
            if declared is not None:
                try:
                    if int(declared) > max_bytes:
                        raise ProbeError(LAYER_API, "fail", "response_too_large")
                except ValueError:
                    raise ProbeError(LAYER_API, "fail", "invalid_response") from None
            chunks = []
            total = 0
            while True:
                remaining()  # raises on an expired deadline; never touch the socket here (http.client may have closed it at EOF)
                chunk = resp.read(min(65536, max_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ProbeError(LAYER_API, "fail", "response_too_large")
                chunks.append(chunk)
            payload = b"".join(chunks)
        return Response(status, date_header, payload, tls_version, str(expected), content_encoding)

    except ProbeError:
        raise
    except (socket.timeout, TimeoutError):
        raise ProbeError(phase, "unknown", "timeout") from None
    except ssl.SSLError:
        raise ProbeError(phase, "fail", "tls_error" if phase == LAYER_TLS else "invalid_response") from None
    except (http.client.HTTPException, ValueError) as exc:
        if state["expired"]:
            raise ProbeError(phase, "unknown", "timeout") from None
        # RemoteDisconnected is both an HTTPException and a ConnectionError.
        reason = "connection_reset" if isinstance(exc, ConnectionError) else "invalid_response"
        raise ProbeError(LAYER_API, "fail", reason) from None
    except OSError:
        if state["expired"]:
            raise ProbeError(phase, "unknown", "timeout") from None
        reason = {LAYER_NETWORK: "connect_failed", LAYER_TLS: "tls_handshake_failed"}.get(phase, "connection_reset")
        raise ProbeError(phase, "fail", reason) from None
    finally:
        watchdog.cancel()
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
