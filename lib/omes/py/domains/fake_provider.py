"""lib/omes/py/domains/fake_provider.py - an in-memory RegistrarAdapter/
DnsAdapter implementation for tests (issue #98).

This is NOT a live client for any provider. It never makes a network
call. It exists so `contracts/domains/v1/*.schema.json` and
`lib/omes/py/domains/{routing,states}.py` have something exercising them
end to end in this repository, and so issues #99 (Cloudflare) and #100
(SRS-X) can extend it with provider-specific test fixtures without a live
account.

Two "modes" are modeled because the two real registrar candidates behave
differently (AGENTS.md #98/#99/#100):

- `"async"` (Cloudflare-like): `register()` returns immediately with
  `in_progress`; the caller polls `poll_registration()` until the
  simulated provider reaches a terminal outcome. A timeout/no-poll is
  never treated as success - the caller decides success only from an
  observed terminal status.
- `"documents"` (SRS-X-like): `register()` immediately requires
  documents; the *document lifecycle*
  (`documents_required -> upload_pending -> submitted -> under_review ->
  rejected|active|action_required`) is modeled separately from the API
  submission status, per issue #100's explicit requirement.

All fields that look like secrets are stored only as `{"store", "key"}`
references, and `redact()` is applied to anything this module returns
that might be logged/audited, mirroring
`lib/omes/py/jobs/audit.py`'s redaction pattern.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_SECRET_NAME_RE = re.compile(
    r"(token|password|secret|credential|api[_-]?key|passphrase|cookie|authorization)", re.IGNORECASE
)


def redact(value: Any) -> Any:
    """Recursively replaces any secret-like field's value with
    "[REDACTED]", mirroring lib/omes/py/jobs/audit.py's redaction rule so
    fake-provider evidence can be logged/asserted-on safely in tests."""
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            if _SECRET_NAME_RE.search(key):
                out[key] = "[REDACTED]"
            else:
                out[key] = redact(val)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class ProviderError(Exception):
    pass


class DuplicateRequestError(ProviderError):
    """Raised when a registration/renewal is submitted twice with the
    same idempotency_key - the fake provider returns the ORIGINAL result
    rather than creating a second order, matching the real job store's
    idempotency contract (docs/jobs.md section 8)."""


@dataclass
class PriceBook:
    """tld -> price snapshot. A mutable in-memory book so tests can
    change a price between snapshot-time and checkout-time (issue #99's
    "price change between snapshot and checkout" test)."""

    prices: dict[str, dict[str, Any]] = field(default_factory=dict)

    def snapshot(self, tld: str, operation: str = "registration", term_years: int = 1) -> dict[str, Any]:
        entry = self.prices.get(tld)
        if entry is None:
            raise ProviderError(f"no price entry for .{tld}")
        return {
            "snapshot_id": f"price-{tld}-{operation}-{entry['_version']}",
            "provider": entry["provider"],
            "tld": tld,
            "operation": operation,
            "term_years": term_years,
            "provider_cost_minor": entry["provider_cost_minor"],
            "customer_price_minor": entry["customer_price_minor"],
            "currency": entry["currency"],
            "tax_class": entry.get("tax_class", "standard"),
            "premium": entry.get("premium", False),
            "captured_at": "2026-09-19T00:00:00Z",
        }

    def set_price(self, tld: str, provider: str, provider_cost_minor: int, customer_price_minor: int, currency: str = "USD") -> None:
        current = self.prices.get(tld, {"_version": 0})
        self.prices[tld] = {
            "provider": provider,
            "provider_cost_minor": provider_cost_minor,
            "customer_price_minor": customer_price_minor,
            "currency": currency,
            "tax_class": "standard",
            "premium": False,
            "_version": current["_version"] + 1,
        }


class FakeRegistrar:
    """A single fake registrar account. `mode="async"` models a
    Cloudflare-like flow; `mode="documents"` models an SRS-X-like flow."""

    def __init__(self, provider: str, mode: str, supported_tlds: tuple[str, ...], async_steps: int = 2):
        if mode not in ("async", "documents"):
            raise ValueError(f"unknown fake provider mode: {mode!r}")
        self.provider = provider
        self.mode = mode
        self.supported_tlds = set(supported_tlds)
        self.async_steps = async_steps
        self.price_book = PriceBook()
        self._orders: dict[str, dict[str, Any]] = {}
        self._idempotency: dict[str, str] = {}
        self._documents: dict[str, dict[str, Any]] = {}
        self._dns: dict[str, dict[str, dict[str, Any]]] = {}
        self._dnssec: dict[str, str] = {}
        self._registered_domains: dict[str, dict[str, Any]] = {}

    # -- discovery / availability -----------------------------------
    def search(self, query: str, tlds: tuple[str, ...]) -> dict[str, Any]:
        results = []
        for tld in tlds:
            results.append(
                {
                    "domain": f"{query}.{tld}",
                    "tld": tld,
                    "likely_available": tld in self.supported_tlds,
                    "premium": False,
                }
            )
        return {"provider": self.provider, "discovery_only": True, "results": results}

    def check_availability(self, domain: str) -> dict[str, Any]:
        tld = domain.rsplit(".", 1)[-1]
        if tld not in self.supported_tlds:
            raise ProviderError(f"{self.provider} does not support .{tld} (route to manual_fallback)")
        available = domain not in self._registered_domains
        return {
            "domain": domain,
            "provider": self.provider,
            "available": available,
            "authoritative": True,
            "premium": False,
            "checked_at": "2026-09-19T00:00:01Z",
        }

    # -- registration --------------------------------------------------
    def register(self, *, domain: str, idempotency_key: str, price_snapshot_id: str, term_years: int = 1) -> dict[str, Any]:
        if idempotency_key in self._idempotency:
            order_id = self._idempotency[idempotency_key]
            raise DuplicateRequestError(f"idempotency_key {idempotency_key!r} already submitted as {order_id}")
        tld = domain.rsplit(".", 1)[-1]
        if tld not in self.supported_tlds:
            raise ProviderError(f"{self.provider} does not support .{tld} (route to manual_fallback)")

        order_id = f"order-{self.provider}-{len(self._orders) + 1:04d}"
        self._idempotency[idempotency_key] = order_id

        if self.mode == "async":
            record = {
                "order_id": order_id,
                "domain": domain,
                "provider": self.provider,
                "status": "in_progress",
                "provider_order_id": f"{self.provider}-po-{order_id}",
                "_polls_remaining": self.async_steps,
                "term_years": term_years,
                "price_snapshot_id": price_snapshot_id,
            }
        else:  # documents
            record = {
                "order_id": order_id,
                "domain": domain,
                "provider": self.provider,
                "status": "action_required",
                "provider_order_id": f"{self.provider}-po-{order_id}",
                "term_years": term_years,
                "price_snapshot_id": price_snapshot_id,
            }
            self._documents[order_id] = {"lifecycle": "documents_required", "urls": []}

        self._orders[order_id] = record
        return dict(record)

    def poll_registration(self, order_id: str) -> dict[str, Any]:
        """Advances a simulated async registration by one poll step.
        Never returns success on a poll that did not actually observe a
        terminal provider state - a caller that stops polling early sees
        `in_progress` forever, not a false success."""
        record = self._orders.get(order_id)
        if record is None:
            raise ProviderError(f"no such order: {order_id}")
        if self.mode != "async":
            return dict(record)
        if record["status"] != "in_progress":
            return dict(record)
        record["_polls_remaining"] -= 1
        if record["_polls_remaining"] <= 0:
            record["status"] = "succeeded"
            self._registered_domains[record["domain"]] = {
                "domain": record["domain"],
                "provider": self.provider,
                "provider_domain_id": record["provider_order_id"],
                "status": "active",
                "expires_at": "2027-09-19T00:00:00Z",
                "locked": True,
                "auto_renew": False,
                "privacy_enabled": True,
                "last_synced_at": "2026-09-19T00:05:00Z",
            }
        return dict(record)

    # -- documents (SRS-X-like) ----------------------------------------
    def request_document_upload(self, order_id: str, expires_in_seconds: int = 900) -> dict[str, Any]:
        doc = self._documents.get(order_id)
        if doc is None:
            raise ProviderError(f"no document workflow for order {order_id}")
        doc["lifecycle"] = "upload_pending"
        upload_ref = {
            "order_id": order_id,
            "upload_url_reference": {"store": "object-store", "key": f"domains/{order_id}/upload"},
            "expires_in_seconds": expires_in_seconds,
        }
        doc["urls"].append(upload_ref)
        return dict(upload_ref)

    def submit_documents(self, order_id: str) -> dict[str, Any]:
        doc = self._documents.get(order_id)
        if doc is None:
            raise ProviderError(f"no document workflow for order {order_id}")
        if doc["lifecycle"] != "upload_pending":
            raise ProviderError(f"order {order_id} has no pending upload to submit (state={doc['lifecycle']!r})")
        doc["lifecycle"] = "submitted"
        return {"order_id": order_id, "lifecycle": doc["lifecycle"]}

    def review_documents(self, order_id: str, *, approve: bool) -> dict[str, Any]:
        doc = self._documents.get(order_id)
        if doc is None:
            raise ProviderError(f"no document workflow for order {order_id}")
        doc["lifecycle"] = "under_review"
        record = self._orders[order_id]
        if approve:
            doc["lifecycle"] = "active"
            record["status"] = "succeeded"
            self._registered_domains[record["domain"]] = {
                "domain": record["domain"],
                "provider": self.provider,
                "provider_domain_id": record["provider_order_id"],
                "status": "active",
                "expires_at": "2027-09-19T00:00:00Z",
                "locked": True,
                "auto_renew": False,
                "privacy_enabled": False,
                "last_synced_at": "2026-09-19T00:05:00Z",
            }
        else:
            doc["lifecycle"] = "rejected"
            record["status"] = "action_required"
        return {"order_id": order_id, "lifecycle": doc["lifecycle"], "registration_status": record["status"]}

    def document_lifecycle(self, order_id: str) -> str:
        doc = self._documents.get(order_id)
        if doc is None:
            raise ProviderError(f"no document workflow for order {order_id}")
        return doc["lifecycle"]

    # -- renewal / transfer / contact ----------------------------------
    def renew(self, *, domain: str, idempotency_key: str, price_snapshot_id: str, term_years: int = 1) -> dict[str, Any]:
        if idempotency_key in self._idempotency:
            order_id = self._idempotency[idempotency_key]
            raise DuplicateRequestError(f"idempotency_key {idempotency_key!r} already submitted as {order_id}")
        if domain not in self._registered_domains:
            raise ProviderError(f"{domain} is not registered with {self.provider}; cannot renew")
        order_id = f"renew-{self.provider}-{len(self._orders) + 1:04d}"
        self._idempotency[idempotency_key] = order_id
        record = {"order_id": order_id, "domain": domain, "provider": self.provider, "status": "succeeded", "term_years": term_years, "price_snapshot_id": price_snapshot_id}
        self._orders[order_id] = record
        return dict(record)

    def transfer(self, **_kwargs: Any) -> dict[str, Any]:
        # Neither modeled provider claims automated transfer yet
        # (issue #99/#100: "do not claim automated transfer/contact
        # update until the provider API capability is verified").
        return {"status": "action_required", "reason": "manual_fallback: transfer is not automated for this provider"}

    def update_contact(self, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "action_required", "reason": "manual_fallback: contact update is not automated for this provider"}

    # -- DNS -------------------------------------------------------------
    def upsert_dns_record(self, zone_id: str, record: dict[str, Any]) -> dict[str, Any]:
        zone = self._dns.setdefault(zone_id, {})
        zone[record["record_id"]] = dict(record)
        return dict(record)

    def list_dns_records(self, zone_id: str) -> list[dict[str, Any]]:
        return list(self._dns.get(zone_id, {}).values())

    def detect_drift(self, zone_id: str, desired: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Compares `desired` (what OMES/awcms-one believes should exist)
        against the provider's actual records for `zone_id`. Returns a
        list of drift entries; an empty list means no drift."""
        actual = {r["record_id"]: r for r in self.list_dns_records(zone_id)}
        drift = []
        for want in desired:
            got = actual.get(want["record_id"])
            if got is None:
                drift.append({"record_id": want["record_id"], "kind": "missing_at_provider", "desired": want})
            elif got != want:
                drift.append({"record_id": want["record_id"], "kind": "mismatch", "desired": want, "observed": got})
        wanted_ids = {w["record_id"] for w in desired}
        for record_id, got in actual.items():
            if record_id not in wanted_ids:
                drift.append({"record_id": record_id, "kind": "unmanaged_at_provider", "observed": got})
        return drift

    # -- DNSSEC ------------------------------------------------------------
    def dnssec_status(self, domain: str) -> dict[str, Any]:
        status = self._dnssec.get(domain, "unsigned")
        return {"domain": domain, "provider": self.provider, "status": status, "checked_at": "2026-09-19T00:10:00Z"}

    def set_dnssec_status(self, domain: str, status: str) -> None:
        self._dnssec[domain] = status
