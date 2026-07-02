"""
Scanners — each one reads a source we are configured (i.e. allowed) to reach.
The `enabled` dict IS the legal surface: a disabled scanner's signal never
enters the pipeline, so nothing downstream can act on it.
"""
from __future__ import annotations

import uuid

ANON_COOKIE = "cx_anon"
GA_COOKIE = "_ga"


def scan(cookies: dict, body: dict, *, enabled: dict[str, bool]) -> dict:
    out: dict = {}

    if enabled.get("cookie"):
        anon_id = cookies.get(ANON_COOKIE) or body.get("anon_id")
        if not anon_id:
            anon_id = f"a_{uuid.uuid4().hex[:12]}"
            out["_minted_anon"] = anon_id
        out["anon_id"] = anon_id

    if enabled.get("ga"):
        raw = cookies.get(GA_COOKIE) or body.get("ga_client_id")
        if raw:
            out["ga_client_id"] = _ga_client_id(raw)

    if enabled.get("fingerprint"):
        fp = body.get("fingerprint")
        if fp:
            out["fingerprint"] = fp

    if enabled.get("pii"):
        pii = body.get("pii")
        if isinstance(pii, dict):
            fields = {k: str(v) for k, v in pii.items() if v}
            if fields:
                out["pii"] = fields

    return out


def _ga_client_id(raw: str) -> str:
    """'GA1.1.555.666' -> '555.666' (the client id is the last two segments)."""
    parts = raw.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 4 else raw
