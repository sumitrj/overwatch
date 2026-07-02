"""
Engine, scanner, vendor, and surface tests for Census.
Maps 1:1 to acceptance criteria AC1-AC17 in SPEC.md (traceability block at the bottom).
Config-plane levers are covered separately in test_config.py.
"""
import pytest
from fastapi.testclient import TestClient

from census import Census, Signals                                  # ImportError until implemented — expected
from census.scanners import scan
from census.vendors.google_analytics import MeasurementProtocolForwarder


@pytest.fixture
def cx() -> Census:
    return Census()


@pytest.fixture
def client() -> TestClient:
    import app as appmod
    from census.config import CensusConfig
    from census.vendors.loan_users import LoanUsersVendor
    from census.vendors.ip_enrichment import IPEnrichmentVendor
    appmod.cx = Census()
    appmod.config = CensusConfig()
    appmod.ga.sent.clear()
    appmod.tally = appmod.new_tally()
    appmod.associations.clear()
    # Fresh vendors each test; ip_enrichment off by default so no test hits the network
    # (the AC40 test opts in with an injected transport).
    appmod.VENDORS = {v.name: v for v in (LoanUsersVendor(), IPEnrichmentVendor())}
    appmod.config.associators["ip_enrichment"] = False
    return TestClient(appmod.app)


def observe(cx, session, anon=None, ga=None, consent=True):
    signals = Signals(session_id=session, anon_id=anon, ga_client_id=ga)
    return cx.observe(signals, consent_observe=consent)


# === existence boundary ===

def test_observe_with_consent_creates_new_anonymous_entity(cx):
    """When a consented signal arrives, an entity exists with the sources as its basis."""
    r = observe(cx, "s1", anon="A", ga="555.666")

    assert r is not None
    assert r.archetype == "new_anonymous"
    assert sorted(r.basis) == ["anon", "ga"]


def test_observe_without_consent_creates_nothing(cx):
    """When observe-consent is withheld, no entity comes into existence."""
    r = observe(cx, "s1", anon="A", consent=False)

    assert r is None
    assert cx.analytics()["total"] == 0


# === resolution ===

def test_repeat_visit_with_same_anon_id_resolves_to_same_entity(cx):
    """When the same anon id returns, it is the same entity, now repeat_anonymous."""
    first = observe(cx, "s1", anon="A")

    second = observe(cx, "s2", anon="A")

    assert second.entity_id == first.entity_id
    assert second.archetype == "repeat_anonymous"


def test_ga_id_bridges_a_cleared_cookie(cx):
    """When anon+ga were seen together, a ga-only signal recovers the same entity."""
    first = observe(cx, "s1", anon="A", ga="555.666")

    recovered = observe(cx, "s2", ga="555.666")

    assert recovered.entity_id == first.entity_id


def test_login_merges_anonymous_history_into_logged_out_then_in(cx):
    """When an anonymous visitor logs in, the alias merges history under one entity."""
    first = observe(cx, "s1", anon="A")

    entity_id = cx.alias(anon_id="A", user_token="U-42")

    assert entity_id == first.entity_id
    assert cx.classify(entity_id) == "logged_out_then_in"


# === pseudonymous analytics ===

def test_analytics_exposes_counts_and_nothing_else(cx):
    """Analytics aggregates archetype counts only — no ids, no PII."""
    observe(cx, "s1", anon="A")

    a = cx.analytics()

    assert set(a.keys()) == {"total", "by_archetype"}
    assert a["total"] == 1
    assert all(isinstance(v, int) for v in a["by_archetype"].values())


# === PII boundary ===

@pytest.mark.parametrize("bound,consented", [(False, False), (True, False), (False, True)])
def test_attribute_is_denied_unless_bound_and_consented(cx, bound, consented):
    """Attribution is denied whenever either binding or consent is missing."""
    e = observe(cx, "s1", anon="A").entity_id
    if bound:
        cx.bind(e, "CUST-1")
    if consented:
        cx.set_consent(e, "outreach", True)

    result = cx.attribute(e, purpose="outreach")

    assert result.granted is False


def test_attribute_granted_after_bind_and_consent_carries_the_record(cx):
    """Bound + consented, the record crosses the boundary with customer and purpose."""
    e = observe(cx, "s1", anon="A").entity_id
    cx.bind(e, "CUST-1")
    cx.set_consent(e, "outreach", True)

    result = cx.attribute(e, purpose="outreach")

    assert result.granted is True
    assert result.record["customer_id"] == "CUST-1"
    assert result.record["purpose"] == "outreach"


def test_revoking_consent_closes_the_gate_and_leaves_analytics_unchanged(cx):
    """Revocation denies future attribution without touching pseudonymous aggregates."""
    e = observe(cx, "s1", anon="A").entity_id
    cx.bind(e, "CUST-1")
    cx.set_consent(e, "outreach", True)
    before = cx.analytics()

    cx.set_consent(e, "outreach", False)

    assert cx.attribute(e, purpose="outreach").granted is False
    assert cx.analytics() == before


def test_erase_identity_removes_pii_but_the_entity_graph_survives(cx):
    """Erasure kills binding and consent; the pseudonymous entity keeps resolving."""
    e = observe(cx, "s1", anon="A").entity_id
    cx.bind(e, "CUST-1")
    cx.set_consent(e, "outreach", True)

    cx.erase_identity(e)

    assert cx.attribute(e, purpose="outreach").granted is False
    assert observe(cx, "s2", anon="A").entity_id == e


# === scanners ===

def test_cookie_scanner_mints_returns_or_ignores_the_anon_id():
    """The cookie lever governs the anon id: mint when absent, reuse when present, nothing when off."""
    minted = scan({}, {}, enabled={"cookie": True, "ga": False})
    reused = scan({"cx_anon": "A"}, {}, enabled={"cookie": True, "ga": False})
    off = scan({"cx_anon": "A"}, {}, enabled={"cookie": False, "ga": False})

    assert minted["anon_id"] == minted["_minted_anon"]
    assert reused["anon_id"] == "A" and "_minted_anon" not in reused
    assert "anon_id" not in off


def test_ga_scanner_parses_the_client_id_only_when_enabled():
    """The ga lever governs the vendor id: parsed from _ga when on, absent when off."""
    on = scan({"_ga": "GA1.1.555.666"}, {}, enabled={"cookie": False, "ga": True})
    off = scan({"_ga": "GA1.1.555.666"}, {}, enabled={"cookie": False, "ga": False})

    assert on["ga_client_id"] == "555.666"
    assert "ga_client_id" not in off


# === GA forwarder ===

def test_forward_with_credentials_sends_a_measurement_protocol_payload():
    """With credentials and a client id, the event is recorded and handed to the transport."""
    calls = []
    ga = MeasurementProtocolForwarder("G-DEMO", "secret", transport=lambda u, h, p: calls.append((u, p)))

    ga.forward("555.666", "calculator_use", {"entity": "e_1"})

    assert len(ga.sent) == 1
    url, payload = calls[0]
    assert payload["client_id"] == "555.666"
    assert payload["events"][0]["name"] == "calculator_use"


@pytest.mark.parametrize("measurement_id,api_secret,client_id",
                         [(None, None, "555.666"), ("G-DEMO", "secret", None)])
def test_forward_without_credentials_or_client_id_is_a_silent_noop(measurement_id, api_secret, client_id):
    """Missing credentials or a missing client id must never raise or forward."""
    ga = MeasurementProtocolForwarder(measurement_id, api_secret, transport=lambda u, h, p: 1 / 0)

    ga.forward(client_id, "calculator_use", {})

    assert ga.sent == []


# === demo surfaces ===

def test_visitor_page_and_console_are_served(client):
    """Both surfaces come back as HTML from the static directory."""
    home = client.get("/")
    console = client.get("/console")

    assert home.status_code == 200 and "text/html" in home.headers["content-type"]
    assert console.status_code == 200 and "text/html" in console.headers["content-type"]


def test_visitor_page_asks_for_permission_instead_of_assuming_it(client):
    """The page carries a consent banner wired to the cx_consent cookie."""
    html = client.get("/").text

    assert "consent-banner" in html
    assert "cx_consent" in html


def test_collect_through_an_open_gate_returns_the_entity_and_sets_the_cookie(client):
    """A consented beacon yields an entity id and mints the first-party cookie."""
    r = client.post("/collect", json={"session_id": "s1", "consent": True})

    assert r.json()["observed"] is True
    assert "entity_id" in r.json()
    assert r.cookies.get("cx_anon")


# === fingerprint source + per-source enforcement (increment 2) ===

def test_fingerprint_scanner_reads_the_body_only_when_enabled():
    """The fingerprint lever governs the device hash coming in from the page."""
    on = scan({}, {"fingerprint": "fp_9ab2"}, enabled={"cookie": False, "ga": False, "fingerprint": True})
    off = scan({}, {"fingerprint": "fp_9ab2"}, enabled={"cookie": False, "ga": False, "fingerprint": False})

    assert on["fingerprint"] == "fp_9ab2"
    assert "fingerprint" not in off


def test_legitimate_interest_fingerprint_is_admitted_before_any_consent(client):
    """A fingerprint-only beacon registers an entity pre-consent; the cookie still waits."""
    r = client.post("/collect", json={"session_id": "s1", "fingerprint": "fp_9ab2"})

    assert r.json()["observed"] is True
    assert r.json()["basis"] == ["fp"]
    assert r.json()["ask_consent"] is True
    assert not r.cookies.get("cx_anon")


def test_granting_consent_joins_the_cookie_to_the_fingerprint_entity(client):
    """After consent the cookie is admitted, set, and merged onto the fingerprint entity."""
    first = client.post("/collect", json={"session_id": "s1", "fingerprint": "fp_9ab2"})

    second = client.post("/collect", json={"session_id": "s2", "fingerprint": "fp_9ab2", "consent": True})

    assert second.json()["entity_id"] == first.json()["entity_id"]
    assert "anon" in second.json()["basis"]
    assert second.cookies.get("cx_anon")


def test_enforcement_toggle_makes_the_fingerprint_wait_for_consent(client):
    """Flip fingerprint enforcement to consent and the same pre-consent beacon observes nothing."""
    client.put("/config", json={"enforcement": {"fingerprint": "consent"}})

    r = client.post("/collect", json={"session_id": "s1", "fingerprint": "fp_9ab2"})

    assert r.json()["observed"] is False


def test_login_backpopulates_prior_anonymous_sessions(client):
    """Anonymous history is stitched into the account, and the count says how much."""
    client.post("/collect", json={"session_id": "s1", "consent": True})
    client.post("/collect", json={"session_id": "s2", "consent": True})

    r = client.post("/login", json={"user_token": "priya@example.com"})

    assert r.json()["backpopulated_sessions"] == 2
    assert r.json()["archetype"] == "logged_out_then_in"


def test_tally_counts_admissions_withholdings_and_consent_events(client):
    """The legal tally accounts for every source reach and every consent decision."""
    client.post("/collect", json={"session_id": "s1", "fingerprint": "fp_9ab2"})
    client.post("/collect", json={"session_id": "s2", "consent_event": "declined"})

    t = client.get("/tally").json()

    assert t["sources"]["fingerprint"] == {"seen": 1, "admitted": 1, "withheld": 0}
    assert t["sources"]["cookie"]["withheld"] >= 1 and t["sources"]["cookie"]["admitted"] == 0
    assert t["consent"]["prompted"] >= 1
    assert t["consent"]["declined"] == 1


def test_visitor_page_is_a_loan_site_that_fingerprints_and_asks(client):
    """The page sells loans, computes EMI, fingerprints the device, and offers sign-in."""
    html = client.get("/").text

    assert "EMI" in html
    assert "fingerprint" in html.lower()
    assert "consent-banner" in html
    assert "Sign in" in html


# === PII scanner + identity vault (increment 3) ===

def test_pii_scanner_reads_form_signals_only_when_enabled():
    """The pii lever governs form-signal capture from the page."""
    body = {"pii": {"email": "priya@example.com", "phone": "98200 12345"}}
    on = scan({}, body, enabled={"pii": True})
    off = scan({}, body, enabled={"pii": False})

    assert on["pii"]["email"] == "priya@example.com"
    assert "pii" not in off


def test_pii_is_withheld_until_the_visitor_consents(client):
    """Pre-consent, PII signals are tallied but never stored."""
    r = client.post("/collect", json={"session_id": "s1", "fingerprint": "fp_1",
                                      "pii": {"email": "priya@example.com"}})

    assert "pii" in r.json()["awaiting_consent"]
    assert not r.json().get("pii_captured")
    rows = client.get("/entities").json()["entities"]
    assert all(not row["pii"] for row in rows)


def test_admitted_pii_lands_in_the_vault_with_server_signals(client):
    """With consent, form fields plus the server-observed IP are captured."""
    r = client.post("/collect", json={"session_id": "s1", "consent": True,
                                      "pii": {"email": "priya@example.com"}})

    captured = r.json()["pii_captured"]

    assert "email" in captured
    assert "ip" in captured


def test_email_signal_bridges_a_cookieless_second_device(client):
    """The same email from a fresh client resolves to the same entity — no cookie needed."""
    import app as appmod
    first = client.post("/collect", json={"session_id": "s1", "consent": True,
                                          "pii": {"email": "priya@example.com"}})

    second_device = TestClient(appmod.app)
    r = second_device.post("/collect", json={"session_id": "s2", "consent": True,
                                             "pii": {"email": "priya@example.com"}})

    assert r.json()["entity_id"] == first.json()["entity_id"]
    assert "email" in r.json()["basis"]


def test_attribute_record_carries_the_vaulted_pii(client):
    """Once bound + consented, the attribution record includes the captured PII."""
    e = client.post("/collect", json={"session_id": "s1", "consent": True,
                                      "pii": {"email": "priya@example.com"}}).json()["entity_id"]
    client.post("/bind", json={"entity_id": e, "customer_id": "CUST-9"})
    client.post("/consent", json={"entity_id": e, "purpose": "outreach", "granted": True})

    record = client.post("/attribute", json={"entity_id": e, "purpose": "outreach"}).json()["record"]

    assert record["pii"]["email"] == "priya@example.com"


def test_erase_wipes_the_vault_and_keeps_attribution_denied(client):
    """Erasure empties the identity vault; the pseudonymous entity remains."""
    e = client.post("/collect", json={"session_id": "s1", "consent": True,
                                      "pii": {"email": "priya@example.com"}}).json()["entity_id"]

    client.post("/erase", json={"entity_id": e})

    row = next(r for r in client.get("/entities").json()["entities"] if r["entity_id"] == e)
    assert row["pii"] == {}
    assert client.post("/attribute", json={"entity_id": e, "purpose": "outreach"}).json()["granted"] is False


def test_operator_ledger_masks_pii(client):
    """The console ledger shows PII presence, never raw values."""
    client.post("/collect", json={"session_id": "s1", "consent": True,
                                  "pii": {"email": "priya@example.com"}})

    body = client.get("/entities").text

    assert "priya@example.com" not in body
    assert "p•••@example.com" in body


# === demo reset (increment 4) ===

def test_reset_cleans_all_entries_but_keeps_the_posture(client):
    """Reset wipes people and the tally; the configured levers survive."""
    client.put("/config", json={"enforcement": {"fingerprint": "consent"}})
    client.post("/collect", json={"session_id": "s1", "consent": True,
                                  "pii": {"email": "priya@example.com"}})

    r = client.post("/reset")

    assert r.json()["cleaned"] is True
    assert client.get("/entities").json()["entities"] == []
    assert client.get("/analytics").json()["total"] == 0
    assert client.get("/tally").json()["consent"] == {"prompted": 0, "granted": 0, "declined": 0}
    assert client.get("/config").json()["enforcement"]["fingerprint"] == "consent"


# === raw record view (increment 5) ===

def test_entity_detail_returns_the_exact_stored_record(client):
    """The per-entity endpoint exposes the record exactly as captured — raw, unmasked JSON."""
    e = client.post("/collect", json={"session_id": "s1", "consent": True, "fingerprint": "fp_1",
                                      "pii": {"email": "priya@example.com"}}).json()["entity_id"]
    client.post("/bind", json={"entity_id": e, "customer_id": "CUST-7"})

    record = client.get(f"/entities/{e}").json()

    assert record["pii"]["email"] == "priya@example.com"
    assert ["fp", "fp_1"] in record["identifiers"]
    assert record["sessions"] == ["s1"]
    assert record["customer_id"] == "CUST-7"
    assert client.get("/entities/e_nope").status_code == 404


# === Overwatch associators / data vendors (increment 6) ===

def test_associators_lever_defaults_on_and_toggles(client):
    """The loan_users associator ships on and can be turned off from the console."""
    assert client.get("/config").json()["associators"]["loan_users"] is True

    cfg = client.put("/config", json={"associators": {"loan_users": False}}).json()

    assert cfg["associators"]["loan_users"] is False


def test_matching_email_auto_associates_the_visitor_to_a_loan_customer(client):
    """An admitted email that matches the vendor promotes the visitor to a known customer."""
    r = client.post("/collect", json={"session_id": "s1", "consent": True,
                                      "pii": {"email": "priya@example.com"}})

    loan_hit = next(a for a in r.json()["associated"] if a["vendor"] == "loan_users")

    assert loan_hit["customer_id"] == "LN-4471"
    e = r.json()["entity_id"]
    assert client.get(f"/entities/{e}").json()["customer_id"] == "LN-4471"


def test_associator_off_leaves_the_visitor_unassociated(client):
    """With the associator off, a matching email does not auto-bind."""
    client.put("/config", json={"associators": {"loan_users": False}})

    r = client.post("/collect", json={"session_id": "s1", "consent": True,
                                      "pii": {"email": "priya@example.com"}})

    assert r.json().get("associated") is None
    assert client.get(f"/entities/{r.json()['entity_id']}").json()["customer_id"] is None


def test_vendors_endpoint_reports_enabled_state_and_match_count(client):
    """The Associators step reads its state and impact from /vendors."""
    client.post("/collect", json={"session_id": "s1", "consent": True,
                                  "pii": {"email": "priya@example.com"}})

    vendors = client.get("/vendors").json()["vendors"]
    loan = next(v for v in vendors if v["name"] == "loan_users")

    assert loan["enabled"] is True
    assert loan["matched"] == 1


def test_console_is_the_overwatch_platform(client):
    """The operator console is branded and framed as Overwatch."""
    html = client.get("/console").text

    assert "Overwatch" in html


# === prefilled / autofilled capture (increment 7) ===

def test_visitor_page_harvests_prefilled_and_autofilled_pii(client):
    """The page reads values that arrive without a keystroke, and leaves the password alone."""
    html = client.get("/").text

    assert "scanPrefilled" in html
    assert "harvestPage" in html          # full within-origin sweep (fields + links + text + URL)
    assert "ow-autofill" in html
    assert '"#email"' in html and '"#phone"' in html
    assert '"#pw"' not in html and "'#pw'" not in html   # never the password


# === full within-origin sweep (increment 8) ===

def test_visitor_page_pulls_the_full_within_origin_surface(client):
    """The full sweep reaches storage, cookies, and environment — but never the password."""
    html = client.get("/").text

    assert "harvestEverything" in html
    assert "localStorage" in html and "sessionStorage" in html
    assert "navigator" in html and "document.cookie" in html
    assert 'el.type === "password"' in html      # explicit password exclusion


# === live third-party associator: IP enrichment (increment 9) ===

def test_ip_enrichment_associator_appends_geo_without_binding(client):
    """A live-vendor integration (injected transport) enriches the visitor without binding a customer."""
    import app as appmod
    from census.vendors.ip_enrichment import IPEnrichmentVendor
    appmod.VENDORS["ip_enrichment"] = IPEnrichmentVendor(transport=lambda ip: {
        "status": "success", "city": "Mumbai", "regionName": "Maharashtra",
        "country": "India", "isp": "Reliance Jio", "as": "AS55836 Reliance Jio"})
    client.put("/config", json={"associators": {"ip_enrichment": True, "loan_users": False}})

    r = client.post("/collect", json={"session_id": "s1", "consent": True,
                                      "pii": {"email": "nobody@nowhere.tld"}})

    ip_hit = next(a for a in r.json()["associated"] if a["vendor"] == "ip_enrichment")
    assert ip_hit.get("customer_id") is None
    detail = client.get(f"/entities/{r.json()['entity_id']}").json()
    assert detail["pii"]["geo_city"] == "Mumbai"
    assert detail["customer_id"] is None                 # enrich-only: no identity bound
    matched = next(v for v in client.get("/vendors").json()["vendors"] if v["name"] == "ip_enrichment")
    assert matched["matched"] == 1


# === Traceability ===
# AC1:  test_observe_with_consent_creates_new_anonymous_entity
# AC2:  test_observe_without_consent_creates_nothing
# AC3:  test_repeat_visit_with_same_anon_id_resolves_to_same_entity
# AC4:  test_ga_id_bridges_a_cleared_cookie
# AC5:  test_login_merges_anonymous_history_into_logged_out_then_in
# AC6:  test_analytics_exposes_counts_and_nothing_else
# AC7:  test_attribute_is_denied_unless_bound_and_consented (x3 parametrized)
# AC8:  test_attribute_granted_after_bind_and_consent_carries_the_record
# AC9:  test_revoking_consent_closes_the_gate_and_leaves_analytics_unchanged
# AC10: test_erase_identity_removes_pii_but_the_entity_graph_survives
# AC11: test_cookie_scanner_mints_returns_or_ignores_the_anon_id
# AC12: test_ga_scanner_parses_the_client_id_only_when_enabled
# AC13: test_forward_with_credentials_sends_a_measurement_protocol_payload
# AC14: test_forward_without_credentials_or_client_id_is_a_silent_noop (x2 parametrized)
# AC15: test_visitor_page_and_console_are_served
# AC16: test_visitor_page_asks_for_permission_instead_of_assuming_it
# AC17: test_collect_through_an_open_gate_returns_the_entity_and_sets_the_cookie
# AC18: test_fingerprint_scanner_reads_the_body_only_when_enabled
# AC19: test_legitimate_interest_fingerprint_is_admitted_before_any_consent
# AC20: test_granting_consent_joins_the_cookie_to_the_fingerprint_entity
# AC21: test_enforcement_toggle_makes_the_fingerprint_wait_for_consent
# AC22: test_login_backpopulates_prior_anonymous_sessions
# AC23: test_tally_counts_admissions_withholdings_and_consent_events
# AC24: test_visitor_page_is_a_loan_site_that_fingerprints_and_asks
# AC25: test_pii_scanner_reads_form_signals_only_when_enabled
# AC26: test_pii_is_withheld_until_the_visitor_consents
# AC27: test_admitted_pii_lands_in_the_vault_with_server_signals, test_email_signal_bridges_a_cookieless_second_device
# AC28: test_attribute_record_carries_the_vaulted_pii
# AC29: test_erase_wipes_the_vault_and_keeps_attribution_denied
# AC30: test_operator_ledger_masks_pii
# AC31: test_reset_cleans_all_entries_but_keeps_the_posture
# AC32: test_entity_detail_returns_the_exact_stored_record
# AC33: test_associators_lever_defaults_on_and_toggles
# AC34: test_matching_email_auto_associates_the_visitor_to_a_loan_customer
# AC35: test_associator_off_leaves_the_visitor_unassociated
# AC36: test_vendors_endpoint_reports_enabled_state_and_match_count
# AC37: test_console_is_the_overwatch_platform
# AC38: test_visitor_page_harvests_prefilled_and_autofilled_pii
# AC39: test_visitor_page_pulls_the_full_within_origin_surface
# AC40: test_ip_enrichment_associator_appends_geo_without_binding
