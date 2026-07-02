"""
Census — config-driven walkthrough (no browser).

Shows the point of the console: compliance decisions are LEVERS, and flipping a lever
changes what the pipeline does. Drives the real HTTP app through TestClient.

Run:  python demo.py
"""
import warnings
warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient
import app as appmod
from census import Census
from census.config import CensusConfig
from census.vendors.google_analytics import MeasurementProtocolForwarder

appmod.cx = Census()
appmod.config = CensusConfig()
appmod.config.associators["ip_enrichment"] = False   # keep the walkthrough offline/deterministic
_ga_events = []
appmod.ga = MeasurementProtocolForwarder("G-DEMO", "secret",
                                         transport=lambda u, h, p: _ga_events.append(p))
c = TestClient(appmod.app)
GA = "GA1.1.555.666"

def show(beat, note, resp):
    body = resp.json() if hasattr(resp, "json") else resp
    print(f"\n\033[1m{beat}\033[0m  {note}\n  -> {body}")

def collect(session, cookies, consent=True):
    return c.post("/collect", json={"session_id": session, "consent": consent}, cookies=cookies)

print("=" * 76); print("CENSUS — one pipeline, posture set by configuration"); print("=" * 76)

# ---- ACT 1: DPDP-strict posture (operator applies a preset) ----
show("POSTURE", "operator applies preset dpdp_strict", c.post("/config/preset", json={"name": "dpdp_strict"}))
show("existence gate", "no consent under strict -> no entity",
     collect("s0", {"cx_anon": "A"}, consent=False))
r = collect("s1", {"cx_anon": "A", "_ga": GA})
E = r.json()["entity_id"]
show("with consent", "entity exists; note basis has NO ga (vendor source lever is off)", r)

# ---- ACT 2: operator flips to Balanced (turns the vendor source on) ----
show("POSTURE", "operator applies preset balanced (vendor fusion ON)", c.post("/config/preset", json={"name": "balanced"}))
show("repeat visit", "same cookie -> repeat_anonymous; basis now includes ga", collect("s2", {"cx_anon": "A", "_ga": GA}))
r = collect("s3", {"_ga": GA})   # cx_anon cleared
same = "SAME entity" if r.json()["entity_id"] == E else "DIFFERENT"
show("vendor fusion", f"cookie cleared, _ga survives -> {same} (recovered)", r)
show("login merge", "anonymous history stitched -> logged_out_then_in",
     c.post("/login", json={"user_token": "U-42"}, cookies={"cx_anon": "A"}))

# ---- ACT 3: pseudonymous analytics + the PII gate + compliance ----
show("analytics", "aggregates on entity_ids, zero PII", c.get("/analytics"))
print(f"  -> GA events forwarded (Measurement Protocol): {len(_ga_events)}")
show("attribute (pre)", "unbound/unconsented -> denied", c.post("/attribute", json={"entity_id": E, "purpose": "outreach"}))
c.post("/bind", json={"entity_id": E, "customer_id": "CUST-1"})
c.post("/consent", json={"entity_id": E, "purpose": "outreach", "granted": True})
show("attribute (post)", "bound + consented -> record crosses the PII boundary",
     c.post("/attribute", json={"entity_id": E, "purpose": "outreach"}))
before = c.get("/analytics").json()
c.post("/consent", json={"entity_id": E, "purpose": "outreach", "granted": False})
d = c.post("/attribute", json={"entity_id": E, "purpose": "outreach"}).json()
show("revoke", f"attribute {d['granted']}, analytics unchanged: {before == c.get('/analytics').json()}", d)
c.post("/erase", json={"entity_id": E})
d = c.post("/attribute", json={"entity_id": E, "purpose": "outreach"}).json()
kept = collect("s4", {"cx_anon": "A"}).json()["entity_id"] == E
show("erase", f"attribute {d['granted']}, entity still resolves: {kept} (graph survives)", d)

print("\n" + "=" * 76); print("Same pipeline throughout. Only the levers moved."); print("=" * 76)
