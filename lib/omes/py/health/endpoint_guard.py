"""lib/omes/py/health/endpoint_guard.py - endpoint URL parsing, redaction and
destination (SSRF) policy for the read-only inference endpoint evidence
probe (issue #292, ADR-0029, docs/ai-inference-serving-architecture.md).

Standard library only (ADR-0012). Pure policy: the only I/O is the
injectable name resolver in `resolve_and_vet`. This module never opens a
connection, never reads credentials and never reads any file.

Threat model (docs/threat-model.md AI-09..AI-13):

- The endpoint string is operator-provided but is still treated as
  untrusted input: it may carry credentials (userinfo), CRLF, a hostile
  scheme (file://, gopher://), a numeric-encoded host (2130706433,
  0x7f.1) or a name whose DNS answer changes between lookups (DNS
  rebinding).
- Policy: only http/https; no userinfo, query or fragment; a strict
  path alphabet; hostnames in canonical DNS or canonical IP-literal form.
- The name is resolved ONCE. EVERY resolved address is classified, and
  the probe may only connect to an address from that vetted set, so a
  second (rebound) lookup can never change the destination.
- Default scope is `private`: loopback and RFC 1918 / IPv6 ULA only.
  Link-local (including cloud metadata 169.254.169.254), multicast,
  unspecified, shared/CGNAT, reserved and public addresses are always
  denied; there is deliberately no override to allow a public address
  (cloud endpoints are Hermes provider routing plus the AI egress
  policy, not this probe).
- IPv4-mapped IPv6 (::ffff:a.b.c.d) is unwrapped and classified as the
  embedded IPv4 address; other IPv4-embedding IPv6 forms (NAT64, 6to4,
  Teredo, IPv4-compatible) are denied as reserved.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import threading
from typing import Callable, Optional
from urllib.parse import urlsplit

SCHEMES = ("http", "https")
DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_URL_LENGTH = 2048

SCOPE_LOOPBACK = "loopback"
SCOPE_PRIVATE = "private"
SCOPES = (SCOPE_LOOPBACK, SCOPE_PRIVATE)

# The ONLY request this probe is allowed to make (phase 1): a metadata
# GET that carries no prompt and no credential. See
# docs/ai-inference-serving-architecture.md section 3 and docs/cli.md.
# Server-specific extras (/health, /version, /metrics, /api/*) are
# intentionally excluded: they are not part of the OpenAI-compatible
# contract, differ per server, and each extra path is extra surface.
ALLOWED_PROBES = (("GET", "/v1/models"),)
_PROBE_PATH_RE = re.compile(r"^(/[A-Za-z0-9._~-]+)*/v1/models$")

_PATH_RE = re.compile(r"^(/[A-Za-z0-9._~-]+)*/?$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/@+-]{1,256}$")
_LABEL_RE = re.compile(r"^[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?$")
_NUMERIC_LABEL_RE = re.compile(r"^(0[xX][0-9a-fA-F]+|[0-9]+)$")

# Cloud / platform instance-metadata endpoints. Denied even if a future
# change widened the allowed ranges.
_METADATA_ADDRESSES = frozenset(
    ipaddress.ip_address(a)
    for a in (
        "169.254.169.254",  # AWS, GCP, Azure, OpenStack
        "169.254.170.2",  # AWS ECS task metadata
        "100.100.100.200",  # Alibaba Cloud
        "192.0.0.192",  # Oracle Cloud
        "fd00:ec2::254",  # AWS IPv6
    )
)

_V4_PRIVATE = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_V4_SHARED = ipaddress.ip_network("100.64.0.0/10")
_V6_ULA = ipaddress.ip_network("fc00::/7")
_V6_EMBEDDING = tuple(
    ipaddress.ip_network(n)
    for n in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32", "::/96")
)


class EndpointError(Exception):
    """A rejected endpoint. `.reason` is a stable machine-readable code
    that is always safe to print (it never contains any part of the
    rejected input)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class Endpoint:
    """A syntactically valid, policy-clean endpoint (before DNS)."""

    __slots__ = ("scheme", "host", "port", "base_path", "ip_literal")

    def __init__(self, scheme: str, host: str, port: int, base_path: str, ip_literal: Optional[str]):
        self.scheme = scheme
        self.host = host
        self.port = port
        self.base_path = base_path
        self.ip_literal = ip_literal

    @property
    def host_header(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        if self.port == DEFAULT_PORTS[self.scheme]:
            return host
        return f"{host}:{self.port}"

    @property
    def display(self) -> str:
        """Redacted display form. No userinfo, query or fragment can be
        present in a validated Endpoint, but a path prefix can carry an
        operator's gateway token (e.g. ``/sk-abc/v1``), so any prefix other
        than the well-known ``/v1`` is printed as ``/[REDACTED]`` (keeping a
        trailing ``/v1`` so the probed API version stays visible). The real
        prefix is still used for the request itself (``probe_path``)."""
        base = self.base_path
        if base in ("", "/v1"):
            shown = base
        else:
            shown = "/[REDACTED]" + ("/v1" if base.endswith("/v1") else "")
        return f"{self.scheme}://{self.host_header}{shown}"

    @property
    def probe_path(self) -> str:
        base = self.base_path
        if base == "/v1" or base.endswith("/v1"):
            path = f"{base}/models"
        else:
            path = f"{base}/v1/models"
        if not _PROBE_PATH_RE.match(path):  # defence in depth; unreachable via parse_endpoint
            raise EndpointError("probe_path_not_allowlisted")
        return path


def parse_endpoint(raw: object) -> Endpoint:
    """Parses and validates an operator-provided endpoint base URL.
    Raises EndpointError(reason) on anything outside the narrow accepted
    grammar: scheme://host[:port][/path-prefix]."""
    if not isinstance(raw, str) or not raw:
        raise EndpointError("invalid_url")
    if len(raw) > MAX_URL_LENGTH:
        raise EndpointError("invalid_url")
    # Printable ASCII only: rejects CR/LF/NUL/tab/space (header and log
    # injection), non-ASCII (IDN homographs) and DEL in one check.
    if not all(0x21 <= ord(c) <= 0x7E for c in raw) or "\\" in raw:
        raise EndpointError("invalid_url")
    try:
        parts = urlsplit(raw)
    except ValueError:
        raise EndpointError("invalid_url") from None

    scheme = parts.scheme.lower()
    if scheme not in SCHEMES:
        raise EndpointError("scheme_not_allowed")
    if not parts.netloc:
        raise EndpointError("invalid_url")
    if "@" in parts.netloc:
        raise EndpointError("userinfo_not_allowed")
    if parts.query or parts.fragment or "?" in raw or "#" in raw:
        raise EndpointError("query_not_allowed")

    try:
        port = parts.port
        hostname = parts.hostname
    except ValueError:
        raise EndpointError("invalid_port") from None
    if port is None:
        port = DEFAULT_PORTS[scheme]
    if not 1 <= port <= 65535:
        raise EndpointError("invalid_port")
    if not hostname or "%" in hostname:
        raise EndpointError("invalid_host")
    host = hostname.lower()

    ip_literal: Optional[str] = None
    try:
        ip_literal = str(ipaddress.ip_address(host))
        host = ip_literal
    except ValueError:
        labels = host.split(".")
        if len(host) > 253 or not all(_LABEL_RE.match(label) for label in labels):
            raise EndpointError("invalid_host") from None
        # 2130706433, 0x7f.1, 0177.0.0.1, 127.1 ... are resolved as IPv4
        # addresses by inet_aton-style resolvers while looking like names.
        if all(_NUMERIC_LABEL_RE.match(label) for label in labels):
            raise EndpointError("non_canonical_ip_host") from None

    path = parts.path
    if not _PATH_RE.match(path) or any(seg in (".", "..") for seg in path.split("/")):
        raise EndpointError("invalid_path")
    base_path = path.rstrip("/")

    return Endpoint(scheme, host, port, base_path, ip_literal)


def redact_endpoint(raw: object) -> str:
    """Display form of an operator-provided endpoint that is safe to put in
    output, logs and evidence for ANY input, valid or not. A rejected
    endpoint is never echoed (it may carry credentials): it prints as
    `[REDACTED]`."""
    try:
        return parse_endpoint(raw).display
    except EndpointError:
        return "[REDACTED]"


def validate_model_id(raw: object) -> Optional[str]:
    """Returns the model id if it matches the conservative id alphabet,
    else None (never echoed by callers when None)."""
    if isinstance(raw, str) and _MODEL_RE.match(raw):
        return raw
    return None


# ---------------------------------------------------------------------------
# Address classification
# ---------------------------------------------------------------------------

CLASS_LOOPBACK = "loopback"
CLASS_PRIVATE = "private"


def _unwrap(ip: ipaddress._BaseAddress) -> ipaddress._BaseAddress:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def classify_ip(value: str) -> tuple:
    """Returns (canonical_address, class). `class` is "loopback" or
    "private" for the only addresses that can ever be allowed, otherwise a
    stable denial code: unparseable_address, metadata_address,
    link_local_address, multicast_address, unspecified_address,
    reserved_address or public_address. IPv4-mapped IPv6 is unwrapped; the
    returned canonical address is then the embedded IPv4 address."""
    if not isinstance(value, str) or "%" in value:
        return str(value), "unparseable_address"
    try:
        ip = _unwrap(ipaddress.ip_address(value))
    except ValueError:
        return value, "unparseable_address"
    canonical = str(ip)

    if ip in _METADATA_ADDRESSES:
        return canonical, "metadata_address"
    if ip.is_unspecified or (ip.version == 4 and int(ip) >> 24 == 0):
        return canonical, "unspecified_address"
    if ip.is_multicast:
        return canonical, "multicast_address"
    if ip.is_link_local:
        return canonical, "link_local_address"
    if ip.is_loopback:
        return canonical, CLASS_LOOPBACK
    if ip.version == 4:
        if any(ip in net for net in _V4_PRIVATE):
            return canonical, CLASS_PRIVATE
        if ip in _V4_SHARED:
            return canonical, "reserved_address"
    else:
        if any(ip in net for net in _V6_EMBEDDING):
            return canonical, "reserved_address"
        if ip in _V6_ULA:
            return canonical, CLASS_PRIVATE
    if ip.is_global:
        return canonical, "public_address"
    return canonical, "reserved_address"


def address_allowed(klass: str, scope: str) -> bool:
    if klass == CLASS_LOOPBACK:
        return True
    if klass == CLASS_PRIVATE:
        return scope == SCOPE_PRIVATE
    return False


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

Resolver = Callable[[str, int], "list[str]"]


def system_resolver(host: str, port: int) -> list:
    """Default resolver: the OS resolver, TCP stream results only."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


class ResolutionError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _resolve_bounded(resolver: Resolver, host: str, port: int, timeout: float) -> list:
    """Runs the (blocking) resolver in a daemon thread so a hung DNS server
    cannot exceed the probe deadline."""
    box: dict = {}

    def work() -> None:
        try:
            box["addresses"] = list(resolver(host, port))
        except Exception as exc:  # noqa: BLE001 - mapped to a code below, never echoed
            box["error"] = exc

    thread = threading.Thread(target=work, name="omes-endpoint-resolve", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise ResolutionError("dns_timeout")
    if "error" in box:
        raise ResolutionError("dns_resolution_failed")
    return box["addresses"]


def resolve_and_vet(endpoint: Endpoint, scope: str, resolver: Optional[Resolver], timeout: float) -> list:
    """Resolves the endpoint host exactly once and returns the vetted,
    canonical address list to connect to (order preserved, de-duplicated).

    Raises ResolutionError(reason) for resolution failure/timeout/empty
    answers and EndpointError(<class code>) when ANY resolved address is
    outside the allowed scope. A single bad address rejects the whole
    answer: an attacker-influenced name must not be able to smuggle a
    forbidden address into a set that also contains an allowed one."""
    if endpoint.ip_literal is not None:
        raw_addresses = [endpoint.ip_literal]
    else:
        raw_addresses = _resolve_bounded(resolver or system_resolver, endpoint.host, endpoint.port, timeout)
    if not raw_addresses:
        raise ResolutionError("dns_resolution_failed")

    vetted: list = []
    for raw in raw_addresses:
        canonical, klass = classify_ip(raw)
        if not address_allowed(klass, scope):
            # klass is a stable denial code, or "private" when the scope is
            # "loopback" and a private-range address was resolved.
            raise EndpointError("private_address_outside_scope" if klass == CLASS_PRIVATE else klass)
        if canonical not in vetted:
            vetted.append(canonical)
    return vetted
