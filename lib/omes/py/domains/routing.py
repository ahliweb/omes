"""lib/omes/py/domains/routing.py - TLD/provider routing (issue #98).

Resolves which provider handles a given (tld, operation) pair by looking
up `RegistrarCapability` records (`contracts/domains/v1/
registrar-capability.schema.json`) - never by TLD-suffix string matching
alone (AGENTS.md #98: "suffix-only routing is insufficient"). A capability
also declares the *account scope* and *operations* it covers, so the same
provider can have narrower capability for some operations (e.g. Cloudflare
supports `dns_records` for a TLD it cannot register) than others.

When no capability matches, routing returns an explicit
`manual_fallback: true` decision (`routing-decision` schema) rather than
guessing or defaulting to any single provider.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Capability:
    capability_id: str
    provider: str
    extension_pattern: str
    account_scope: str
    supported_operations: tuple[str, ...]
    manual_fallback: bool = False

    def matches(self, tld: str, operation: str) -> bool:
        if self.manual_fallback:
            return False
        if operation not in self.supported_operations:
            return False
        return re.match(self.extension_pattern, tld) is not None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Capability":
        return cls(
            capability_id=data["capability_id"],
            provider=data["provider"],
            extension_pattern=data["extension_pattern"],
            account_scope=data["account_scope"],
            supported_operations=tuple(data["supported_operations"]),
            manual_fallback=bool(data.get("manual_fallback", False)),
        )


@dataclass
class Router:
    """Holds an ordered list of capabilities and resolves the first match.
    Order matters: a narrower/earlier capability wins over a broader
    later one, mirroring how an operator would configure "prefer this
    scoped account for these TLDs, fall back to that one otherwise"."""

    capabilities: list[Capability] = field(default_factory=list)

    def register(self, capability: Capability) -> None:
        self.capabilities.append(capability)

    def resolve(self, tenant_id: str, correlation_id: str, tld: str, operation: str) -> dict[str, Any]:
        tld = tld.lower().lstrip(".")
        for cap in self.capabilities:
            if cap.matches(tld, operation):
                return {
                    "tenant_id": tenant_id,
                    "correlation_id": correlation_id,
                    "tld": tld,
                    "operation": operation,
                    "provider": cap.provider,
                    "capability_id": cap.capability_id,
                    "manual_fallback": False,
                }
        return {
            "tenant_id": tenant_id,
            "correlation_id": correlation_id,
            "tld": tld,
            "operation": operation,
            "provider": "manual",
            "manual_fallback": True,
            "reason": f"no registrar capability supports operation {operation!r} for .{tld}",
        }


def default_router() -> Router:
    """A router pre-loaded with the two capability profiles this
    repository documents (docs/domain-providers.md): a Cloudflare-like
    international profile and an SRS-X-like `.id` profile. This is
    illustrative wiring for tests/fake-provider use, not a live
    configuration source - a real deployment loads its own
    `RegistrarCapability` records (issue #99/#100), it does not hardcode
    this function's list.
    """
    router = Router()
    router.register(
        Capability(
            capability_id="cap-cloudflare-intl-01",
            provider="cloudflare",
            extension_pattern=r"^(com|net|org|dev|io|app)$",
            account_scope="acct-cf-main",
            supported_operations=(
                "search",
                "availability",
                "pricing",
                "registration",
                "read_sync",
                "dns_records",
                "dnssec",
            ),
        )
    )
    router.register(
        Capability(
            capability_id="cap-srsx-id-01",
            provider="srsx",
            extension_pattern=r"^(id|co\.id|or\.id)$",
            account_scope="acct-srsx-main",
            supported_operations=(
                "search",
                "availability",
                "pricing",
                "registration",
                "read_sync",
                "renewal",
            ),
        )
    )
    return router
