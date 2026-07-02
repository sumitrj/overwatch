# Census engine + consent UX (completes the demo)

## Intent
The Census demo becomes runnable end-to-end: an operator opens `/console`, flips levers or
applies a posture preset, and watches the pipeline obey; a visitor on `/` is **asked for
permission** when the posture requires observe-consent, and only then becomes a tracked entity.
Everything app.py / demo.py / test_config.py already import must exist and behave as they assume.

## Interface (fixed by existing consumers — do not change them)
```python
# census/__init__.py
@dataclass class Signals: session_id: str; anon_id: str | None = None; ga_client_id: str | None = None; context: dict | None = None
@dataclass class Resolution: entity_id: str; archetype: str; basis: list[str]   # basis ⊆ ["anon","ga"]
@dataclass class AttributionResult: granted: bool; record: dict | None

class Census:
    _s: Store                                   # .entities: dict[str, Entity], .bindings: dict[str, str]
    def observe(self, signals: Signals, *, consent_observe: bool) -> Resolution | None
    def alias(self, *, anon_id: str | None, user_token: str) -> str
    def classify(self, entity_id: str) -> str   # new_anonymous | repeat_anonymous | logged_out_then_in
    def bind(self, entity_id: str, customer_id: str) -> None
    def set_consent(self, entity_id: str, purpose: str, granted: bool) -> None
    def attribute(self, entity_id: str, *, purpose: str) -> AttributionResult
    def erase_identity(self, entity_id: str) -> None
    def analytics(self) -> dict                 # {"total": int, "by_archetype": dict[str,int]} — no PII

# census/scanners.py
ANON_COOKIE = "cx_anon"
def scan(cookies: dict, body: dict, *, enabled: dict[str, bool]) -> dict
# returns {"anon_id": ...} and/or {"ga_client_id": ...}; "_minted_anon" when it minted a new anon id

# census/vendors/google_analytics.py
class MeasurementProtocolForwarder:
    sent: list[dict]
    def __init__(self, measurement_id, api_secret, transport=None)
    def forward(self, ga_client_id, event_name, params) -> None   # no-op without creds or client id

# census/config.py — the existing root config.py (CensusConfig, PRESETS), moved into the package.
```
Static assets move to `static/` (app.py serves them from there). `index.html` gains a consent
banner: beacon fires without consent, and if the gate is closed the banner appears; **Accept**
sets `cx_consent=true` and re-beacons; **Decline** leaves the visitor untracked.

## Acceptance criteria
1. observe with consent creates an entity: archetype `new_anonymous`, basis lists the scanned sources.
2. observe without consent returns None and creates no entity.
3. Repeat observe with the same anon id resolves to the same entity, archetype `repeat_anonymous`.
4. Vendor fusion: once anon+ga ids are seen together, a beacon with only the ga id resolves to the same entity.
5. alias(anon_id, user_token) merges anonymous history: same entity id, archetype `logged_out_then_in`.
6. analytics() reports total and by_archetype counts only — no identifiers, no PII.
7. attribute is denied while unbound or unconsented.
8. attribute is granted after bind + consent; record carries customer_id and purpose.
9. Revoking consent denies attribute again and leaves analytics unchanged.
10. erase_identity removes binding/consents (attribute denied) but the entity still resolves on the next beacon.
11. scan mints an anon id when the cookie scanner is on and none exists; returns the existing one otherwise; cookie scanner off → no anon id.
12. scan parses `_ga` "GA1.1.555.666" → ga_client_id "555.666" when the ga scanner is on; off → absent.
13. forward with credentials + client id records the event in .sent and passes a Measurement Protocol payload to the transport.
14. forward without credentials, or without a client id, is a silent no-op (.sent unchanged, no error).
15. GET / and GET /console return 200 HTML from static/.
16. The visitor page contains the consent banner (accept sets `cx_consent`) and no unconditional consent in the beacon.
17. POST /collect that passes the gate returns entity_id and sets the cx_anon cookie when one was minted.

## Increment 2 — fingerprint source, per-source enforcement, legal tally, loan-site UX
The visitor page becomes a convincing loan-seller site (products, rates, EMI calculator,
sign-in) with a visible beacon log. A third scanner, `fingerprint` (client-computed device
hash), joins cookie and ga. Each source gains an **enforcement** lever — its legal basis:
`"consent"` (withheld until the visitor agrees) or `"legitimate_interest"` (admitted
immediately). `require_observe_consent` stays the master switch: when off, nothing waits.
A cookie is only ever SET once the cookie source is admitted. `/login` reports how many
prior anonymous sessions were backpopulated into the account. `GET /tally` exposes the
legal tally: per source `{seen, admitted, withheld}` and consent `{prompted, granted,
declined}` (the page sends `consent_event` on accept/decline).

### Acceptance criteria (continued)
18. scan returns the body fingerprint when the fingerprint lever is on; absent when off.
19. A legitimate-interest source is admitted without consent: a fingerprint-only beacon is
    observed with basis ["fp"], ask_consent is true (cookie still needs permission), and NO
    cx_anon cookie is set.
20. Granting consent admits the cookie and joins it to the fingerprint entity: same entity id,
    basis now includes "anon", cx_anon set.
21. Flipping enforcement of fingerprint to "consent" makes the same pre-consent beacon
    unobserved.
22. Login backpopulates: after N anonymous consented sessions, /login returns
    backpopulated_sessions == N and archetype logged_out_then_in.
23. /tally reflects traffic: seen/admitted/withheld per source and prompted/granted/declined
    consent events.
24. The visitor page is a loan site: EMI calculator, fingerprint script, sign-in, and the
    consent banner.

## Increment 3 — the PII scanner and the identity vault
A fourth source, `pii`, captures attribution/PII signals: form fields the page sends
(`body["pii"] = {email, phone, ...}`) plus server-observed signals (client IP, user agent)
injected whenever the lever is on. Like every source it has a reach lever and an enforcement
basis (default **consent** — withheld until the visitor agrees; `dpdp_strict` turns it off
entirely, `dev_open` runs it on legitimate interest). Admitted PII lands in a per-entity
**identity vault**; a captured email additionally becomes an `email` identifier, so the same
address seen from a cookie-less device resolves to the same entity. The vault rides the PII
boundary: `attribute` records carry it, `erase` wipes it, and the operator ledger shows it
**masked only**. `/login` also vaults the account token.

### Acceptance criteria (continued)
25. scan reads the body pii dict only when the pii lever is on.
26. Pre-consent, pii is withheld: response lists "pii" in awaiting_consent, nothing lands in
    any vault.
27. With consent, pii is admitted: response's pii_captured names the fields (form fields + ip),
    and the same email from a fresh cookie-less client resolves to the same entity.
28. attribute records carry the vaulted PII fields once bound + consented.
29. erase_identity wipes the vault (ledger shows none) and attribution stays denied.
30. The operator ledger never exposes raw PII — values are masked.

### Increment 4 — demo reset
31. POST /reset wipes all entities, vaults, and the tally, but leaves the configured levers
    untouched; the console offers it as a "Clean all entries" action.

### Increment 5 — raw record view
32. GET /entities/{id} returns the exact stored record as JSON — identifiers with raw values,
    the raw PII vault, sessions, consents, and the customer link (404 for unknown ids). The
    console shows it in the person panel; the ledger table stays masked.

### Increment 6 — Overwatch: associators (data vendors) + platform framing
Rebrand the operator surface as **Overwatch**, a data-intelligence platform a website is
*channeled through*: the loan site is the hosted website; Overwatch's data layer (sensors +
associators) feeds its intelligence layer (analytics). The console is a workflow: 1 Host your
website · 2 Setup in Overwatch · 3 DNS (disabled) · 4 Sensors · 5 Associators · 6 Analytics.

**Associators** are pluggable data vendors that resolve an admitted identifier against a data
source and promote an anonymous visitor to a known person. The seed vendor `loan_users` maps
an email to a loan-customer record `{customer_id, name, product, stage}`. Config gains
`associators: dict[str,bool]` (loan_users on by default). When an associator is on and an
admitted email matches, the entity is auto-bound to the vendor's customer_id and the vendor
attributes are vaulted.

### Acceptance criteria (continued)
33. Config exposes `associators` (loan_users default True); PUT /config toggles it.
34. With loan_users on, a beacon whose admitted PII email matches a vendor record auto-binds the
    entity to the vendor customer_id, vaults the vendor attributes, and the response carries
    `associated` naming the vendor and customer_id.
35. With loan_users off, a matching email does NOT auto-bind (entity stays unbound).
36. GET /vendors lists each associator with its enabled flag and how many visitors it matched.
37. GET /console is the Overwatch console (HTML mentioning "Overwatch").

### Increment 7 — prefilled / autofilled PII capture
38. The visitor page harvests PII values that arrive WITHOUT a keystroke — server-prefilled
    field values on load and browser-autofilled values (via the CSS `:-webkit-autofill` +
    `animationstart` hook) — routed through the same gated pii beacon, and never reads the
    password field.

### Increment 8 — pull everything (full within-origin sweep)
39. The visitor page's full sweep reads the whole within-origin surface — device/environment
    (userAgent, languages, screen, timezone, cores, memory, network), readable cookies,
    local/session storage, and every non-password form field — and sends it through the one
    gated pii beacon. It stays same-origin; passwords are never read.

### Increment 9 — live third-party associator (IP enrichment)
A second associator, `ip_enrichment`, demonstrates integration with a real external data
vendor: it resolves the visitor's IP against **ip-api.com** (free, no key, no regulated data)
and vaults geo/ISP/ASN. It is *enrich-only* — it appends attributes but binds no customer_id —
proving the `match()` seam supports both identity-resolution (loan_users) and pure enrichment.
The HTTP transport is injectable for offline tests; private/loopback IPs fall back to the
server's own public IP. Multiple associators may match one visitor.

### Acceptance criteria (continued)
40. With ip_enrichment on, a beacon carrying an IP is enriched: the vendor's geo/ISP fields are
    vaulted, the response's `associated` list includes an `ip_enrichment` hit (no customer_id),
    and /vendors reports its match count — all via an injected transport (no real network in tests).

## Out of scope
- Persistence (store stays in-memory), probabilistic fingerprinting, real CMP/DPDP integration,
  real GA credentials (transport is injected in tests), auth on the operator surface.

## Assumptions
- Consent banner visibility is driven by the /collect response ("gate closed"), not by polling /config.
- Entity ids are opaque `e_…` strings; identifiers are (type, value) pairs with types "anon"/"ga".
- Existing test_config.py (7 tests) remains the config-plane suite; this spec adds the engine/scanner/vendor/surface suite.
