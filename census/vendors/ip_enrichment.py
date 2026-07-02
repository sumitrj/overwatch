"""
Associator: IP enrichment via ip-api.com — a live, free, keyless third-party data vendor.

Demonstrates the OTHER shape of association: pure ENRICHMENT. Unlike loan_users (which
resolves an identity and binds a customer), this appends attributes — geo, ISP, ASN — from a
signal we already hold (the visitor IP). No regulated data, no PII vendor: it is the honest,
lawful floor of "associate a visitor against an external source", and it proves the match()
seam carries both identity-resolution and enrichment vendors.

The HTTP transport is injectable so tests never touch the network. A private/loopback IP (the
local-demo case) falls back to the server's own public IP so the demo still shows real data.
"""
from __future__ import annotations

_PRIVATE = ("127.", "10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.",
            "172.2", "172.3", "::1", "localhost")


class IPEnrichmentVendor:
    name = "ip_enrichment"
    label = "IP enrichment (ip-api.com)"

    def __init__(self, transport=None) -> None:
        self.transport = transport or self._http_get
        self._cache: dict[str, dict | None] = {}

    def match(self, identifiers: dict) -> dict | None:
        ip = identifiers.get("ip")
        target = "" if self._is_local(ip) else ip           # "" → ip-api resolves our own public IP
        key = target or "_self"
        if key in self._cache:
            return self._cache[key]

        result = None
        try:
            raw = self.transport(target)
            if raw and raw.get("status") == "success":
                result = {
                    "geo_city": raw.get("city"),
                    "geo_region": raw.get("regionName"),
                    "geo_country": raw.get("country"),
                    "isp": raw.get("isp"),
                    "asn": raw.get("as"),
                }
        except Exception:
            result = None                                    # enrichment must never break /collect
        self._cache[key] = result
        return result

    @staticmethod
    def _is_local(ip) -> bool:
        return not ip or not any(c in str(ip) for c in ".:") or str(ip).startswith(_PRIVATE)

    @staticmethod
    def _http_get(ip: str) -> dict:
        import httpx
        url = f"http://ip-api.com/json/{ip}" if ip else "http://ip-api.com/json/"
        return httpx.get(url, timeout=2.5).json()
