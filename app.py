"""
Census framework — HTTP surface.

Visitor surface:
  GET  /                loan calculator page + Scanner beacon
  POST /collect         beacon sink: enabled Scanners -> observe(); sets cx_anon cookie
  POST /login           auth -> alias()/merge

Operator surface (the demo interface):
  GET  /console         compliance console: levers, live entities, gate tester
  GET  /config          current configuration (the levers, as data)
  PUT  /config          patch levers at runtime
  POST /config/preset   apply a named posture (dpdp_strict | balanced | dev_open)
  GET  /analytics       pseudonymous aggregates
  GET  /entities        pseudonymous entity table (ids, archetypes, session counts)
  POST /bind /consent /attribute /erase   Identity-layer operations
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from census import Census, Signals
from census.config import CensusConfig, PRESETS
from census.scanners import ANON_COOKIE, scan
from census.vendors.google_analytics import MeasurementProtocolForwarder
from census.vendors.loan_users import LoanUsersVendor
from census.vendors.ip_enrichment import IPEnrichmentVendor

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Census")
cx = Census()
config = CensusConfig()
ga = MeasurementProtocolForwarder(
    measurement_id=os.getenv("GA_MEASUREMENT_ID"),
    api_secret=os.getenv("GA_API_SECRET"),
)

# The legal tally: every source reach and every consent decision, accounted for.
def new_tally() -> dict:
    return {"sources": {s: {"seen": 0, "admitted": 0, "withheld": 0}
                        for s in ("cookie", "ga", "fingerprint", "pii")},
            "consent": {"prompted": 0, "granted": 0, "declined": 0}}

tally = new_tally()

# Associators (data vendors). The registry is the plug-in seam for the association layer.
VENDORS = {v.name: v for v in (LoanUsersVendor(), IPEnrichmentVendor())}
associations: dict[str, dict[str, dict]] = {}   # entity_id -> {vendor_name: record}

# scan() output key -> source (lever) name
SOURCE_KEYS = {"cookie": "anon_id", "ga": "ga_client_id", "fingerprint": "fingerprint", "pii": "pii"}


def associate(entity_id: str, pii: dict) -> list | None:
    """Association layer: run every enabled vendor against the admitted identifiers. Each match
    vaults the vendor's attributes; a vendor that returns a customer_id also binds the identity
    (loan_users), while an enrich-only vendor just appends (ip_enrichment). Many may match."""
    hits = []
    for name, enabled in config.associators.items():
        if not enabled or name not in VENDORS:
            continue
        record = VENDORS[name].match(pii)
        if not record:
            continue
        attrs = {k: v for k, v in record.items() if k != "customer_id" and v}
        if attrs:
            cx.capture_pii(entity_id, attrs)
        if record.get("customer_id"):
            cx.bind(entity_id, record["customer_id"])
        associations.setdefault(entity_id, {})[name] = record
        hits.append({"vendor": name, **record})
    return hits or None


def mask(value: str) -> str:
    """Operator surfaces never show raw PII."""
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{local[:1]}•••@{domain}"
    if len(value) > 4:
        return value[:2] + "•••" + value[-2:]
    return "•••"


# --- visitor surface ---------------------------------------------------------

@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.post("/collect")
def collect(request: Request, body: dict = Body(default={})) -> JSONResponse:
    cookies = dict(request.cookies)
    consented = cookies.get("cx_consent") == "true" or body.get("consent") is True
    event = body.get("consent_event")
    if event in ("granted", "declined"):
        tally["consent"][event] += 1

    scanned = scan(cookies, body, enabled=config.scanners)
    minted = scanned.pop("_minted_anon", None)

    # Server-observed PII signals (IP, user agent) ride the same pii source lever.
    if config.scanners.get("pii"):
        meta = {}
        if request.client:
            meta["ip"] = request.client.host
        if request.headers.get("user-agent"):
            meta["user_agent"] = request.headers["user-agent"]
        scanned["pii"] = {**meta, **scanned.get("pii", {})}

    # Per-source enforcement: a "consent" source is withheld until the visitor agrees;
    # a "legitimate_interest" source is admitted immediately. The master switch
    # (require_observe_consent) off means nothing waits.
    admitted: dict[str, str] = {}
    withheld: list[str] = []
    for source, key in SOURCE_KEYS.items():
        value = scanned.get(key)
        if value is None:
            continue
        tally["sources"][source]["seen"] += 1
        waits = config.require_observe_consent and config.enforcement.get(source, "consent") == "consent"
        if waits and not consented:
            tally["sources"][source]["withheld"] += 1
            withheld.append(source)
        else:
            tally["sources"][source]["admitted"] += 1
            admitted[source] = value

    ask_consent = (not consented) and config.require_observe_consent and any(
        on and config.enforcement.get(s, "consent") == "consent"
        for s, on in config.scanners.items())
    if ask_consent and event is None:
        tally["consent"]["prompted"] += 1

    signals = Signals(
        session_id=body.get("session_id") or f"s_{uuid.uuid4().hex[:8]}",
        anon_id=admitted.get("cookie"),
        ga_client_id=admitted.get("ga"),
        fingerprint=admitted.get("fingerprint"),
        pii=admitted.get("pii"),
        context={"page": body.get("page", "loan-calculator"), "event": body.get("event", "page_view")},
    )
    resolution = cx.observe(signals, consent_observe=bool(admitted))

    payload = {"observed": resolution is not None,
               "gate": "open" if admitted else "closed (observe-consent required)",
               "admitted": sorted(admitted),
               "awaiting_consent": withheld,
               "ask_consent": ask_consent}
    if resolution is not None:
        for purpose, granted in config.default_consent.items():
            if granted:
                cx.set_consent(resolution.entity_id, purpose, True)
        ga.forward(signals.ga_client_id, "calculator_use", {"entity": resolution.entity_id})
        payload |= {"entity_id": resolution.entity_id, "archetype": resolution.archetype,
                    "basis": resolution.basis}
        if "pii" in admitted:
            payload["pii_captured"] = sorted(admitted["pii"])
        associated = associate(resolution.entity_id, admitted.get("pii", {}))
        if associated:
            payload["associated"] = associated

    out = JSONResponse(payload)
    # The cookie is only ever SET once the cookie source is admitted — never on a maybe.
    if minted and "cookie" in admitted and resolution is not None:
        out.set_cookie(ANON_COOKIE, minted, httponly=True, samesite="lax", max_age=60 * 60 * 24 * config.retention_days)
    return out


@app.post("/login")
def login(request: Request, body: dict = Body(...)) -> JSONResponse:
    anon = request.cookies.get(ANON_COOKIE) or body.get("anon_id")
    entity_id = cx.alias(anon_id=anon, user_token=body["user_token"])
    if config.scanners.get("pii"):
        cx.capture_pii(entity_id, {"account": body["user_token"]})
    associated = associate(entity_id, {"email": body["user_token"]})
    return JSONResponse({"entity_id": entity_id, "archetype": cx.classify(entity_id),
                         "backpopulated_sessions": len(cx._s.entities[entity_id].sessions),
                         "associated": associated})


# --- operator surface --------------------------------------------------------

@app.get("/console")
def console() -> FileResponse:
    return FileResponse(STATIC / "console.html")


@app.get("/config")
def get_config() -> JSONResponse:
    return JSONResponse(config.to_dict() | {"presets": list(PRESETS)})


@app.put("/config")
def put_config(patch: dict = Body(...)) -> JSONResponse:
    config.apply(patch)
    return JSONResponse(config.to_dict())


@app.post("/config/preset")
def apply_preset(body: dict = Body(...)) -> JSONResponse:
    name = body["name"]
    config.apply(PRESETS[name])
    return JSONResponse(config.to_dict() | {"applied": name})


@app.post("/reset")
def reset() -> JSONResponse:
    """Clean all entries — people, vaults, tally, associations. The configured posture stays."""
    global cx, tally
    cx = Census()
    tally = new_tally()
    associations.clear()
    return JSONResponse({"cleaned": True})


@app.get("/vendors")
def vendors() -> JSONResponse:
    """The Associators step: each data vendor, whether it is enabled, and its match count."""
    rows = [{"name": v.name, "label": v.label,
             "enabled": config.associators.get(v.name, False),
             "matched": sum(1 for a in associations.values() if v.name in a)}
            for v in VENDORS.values()]
    return JSONResponse({"vendors": rows})


@app.get("/tally")
def get_tally() -> JSONResponse:
    return JSONResponse(tally)


@app.get("/analytics")
def analytics() -> JSONResponse:
    return JSONResponse(cx.analytics())


@app.get("/entities")
def entities() -> JSONResponse:
    rows = [{"entity_id": e.entity_id,
             "archetype": cx.classify(e.entity_id),
             "sessions": len(e.sessions),
             "identifiers": sorted(f"{t}" for t, _ in e.identifiers),
             "bound": e.entity_id in cx._s.bindings,
             "customer_id": cx._s.bindings.get(e.entity_id),
             "vendors": sorted(associations.get(e.entity_id, {})),
             "pii": {k: mask(v) for k, v in cx._s.pii_vault.get(e.entity_id, {}).items()}}
            for e in cx._s.entities.values()]
    return JSONResponse({"entities": rows})


@app.get("/entities/{entity_id}")
def entity_detail(entity_id: str) -> JSONResponse:
    """The raw record, exactly as stored — the operator's unmasked view."""
    entity = cx._s.entities.get(entity_id)
    if entity is None:
        return JSONResponse({"error": "unknown entity"}, status_code=404)
    return JSONResponse({
        "entity_id": entity.entity_id,
        "archetype": cx.classify(entity.entity_id),
        "sessions": sorted(entity.sessions),
        "identifiers": sorted([t, v] for t, v in entity.identifiers),
        "pii": dict(cx._s.pii_vault.get(entity_id, {})),
        "consents": dict(cx._s.consents.get(entity_id, {})),
        "customer_id": cx._s.bindings.get(entity_id),
    })


@app.post("/bind")
def bind(body: dict = Body(...)) -> JSONResponse:
    cx.bind(body["entity_id"], body["customer_id"])
    return JSONResponse({"bound": True})


@app.post("/consent")
def consent(body: dict = Body(...)) -> JSONResponse:
    cx.set_consent(body["entity_id"], body["purpose"], bool(body["granted"]))
    return JSONResponse({"purpose": body["purpose"], "granted": bool(body["granted"])})


@app.post("/attribute")
def attribute(body: dict = Body(...)) -> JSONResponse:
    result = cx.attribute(body["entity_id"], purpose=body["purpose"])
    return JSONResponse({"granted": result.granted, "record": result.record})


@app.post("/erase")
def erase(body: dict = Body(...)) -> JSONResponse:
    cx.erase_identity(body["entity_id"])
    return JSONResponse({"erased": True})
