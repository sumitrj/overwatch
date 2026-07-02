"""
Google Analytics as a pseudonymous SOURCE and event SINK — never a PII vendor.
Forwarding uses the Measurement Protocol keyed on the ga client id only.
Without credentials (or without a client id) the forwarder is a silent no-op,
so the demo runs identically with or without a real GA property.
"""
from __future__ import annotations

MP_URL = "https://www.google-analytics.com/mp/collect"


class MeasurementProtocolForwarder:
    def __init__(self, measurement_id: str | None, api_secret: str | None, transport=None) -> None:
        self.measurement_id = measurement_id
        self.api_secret = api_secret
        self.transport = transport or self._http_post
        self.sent: list[dict] = []

    def forward(self, ga_client_id: str | None, event_name: str, params: dict) -> None:
        if not (self.measurement_id and self.api_secret and ga_client_id):
            return
        payload = {"client_id": ga_client_id, "events": [{"name": event_name, "params": params}]}
        url = f"{MP_URL}?measurement_id={self.measurement_id}&api_secret={self.api_secret}"
        try:
            self.transport(url, {"Content-Type": "application/json"}, payload)
        except Exception:
            return  # analytics forwarding must never take down /collect
        self.sent.append(payload)

    @staticmethod
    def _http_post(url: str, headers: dict, payload: dict) -> None:
        import httpx
        httpx.post(url, headers=headers, json=payload, timeout=3.0)
