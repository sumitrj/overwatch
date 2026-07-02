# Overwatch — lawful visitor tracking, as a configuration problem

![Overwatch demo](docs/overwatch-demo.gif)

**[Overview → sumitrj.github.io/overwatch](https://sumitrj.github.io/overwatch)**  ·  **[Approach & solution (one-pager)](docs/approach.html)**

Track visitor activity and identity **legally**. The engineering is the easy part; the real
problem is compliance. Overwatch turns every compliance decision into a **declared lever** on
one pipeline, and gives operators a **console** to set the posture and watch its effect.
Changing jurisdiction or policy is changing config, not code. (The engine is `census`; the
operator platform is Overwatch.)

## The model (unchanged, now governed by config)

Scanner → Index → Identity, with two gates:
- **Existence boundary** (`observe`) — a signal becomes a tracked entity only past the observe-consent lever.
- **PII boundary** (`attribute`) — an entity becomes a named person only when bound *and* consented.

## The two surfaces

**Visitor** — `GET /` a loan-seller site (products, EMI calculator, sign-in) with a Scanner
beacon and a layer-tagged beacon log; `POST /collect` (cookie + GA + fingerprint + PII
scanners → `observe`; form fields are sensed on blur, IP/UA server-side — kept only if the
collection rule allows), `POST /login` (alias/merge — reports how many anonymous sessions
were backpopulated into the account).

**Operator** — `GET /console` is **Overwatch**, the data-intelligence platform the site is
*channeled through*. It reads as a 6-step onboarding workflow: **1 Host your website** ·
**2 Setup in Overwatch** (drop the sensor tag) · **3 DNS delegation** (disabled — reverse-proxy
passthrough for the demo) · **4 Sensors** (the data layer — which signals, kept how) ·
**5 Associators** (identity resolution — data vendors that turn a sensed visitor into a known
person) · **6 Analytics** (the intelligence layer). Levers:

| Lever | Governs | Endpoint |
|---|---|---|
| Sources (cookie, GA, fingerprint, PII) on/off | which signals we may collect | `PUT /config {scanners}` |
| Enforcement per source (consent / legitimate interest) | when a source is admitted | `PUT /config {enforcement}` |
| Require observe-consent | the existence gate (master switch) | `PUT /config {require_observe_consent}` |
| Associators (loan_users …) on/off | which vendors may resolve visitors to known people | `PUT /config {associators}` |
| Purposes + default consent | the PII gate | `PUT /config {purposes, default_consent}` |
| Posture presets | bundles the above | `POST /config/preset` |

Each source's lever pairs with a **legal tally** (`GET /tally`): signals seen / admitted /
withheld per source, plus consent asked / granted / declined. **Associators** (`GET /vendors`)
are pluggable data vendors behind a `match()` seam; `loan_users` resolves an admitted email to
a loan-customer record and auto-binds the visitor (identity resolution), while `ip_enrichment`
integrates a **live external vendor** — ip-api.com (free, keyless) — to append geo/ISP/ASN from
the visitor IP (enrich-only, binds nothing). Both shapes share the one seam; a real CIBIL /
Account Aggregator / Karza connector drops in the same way. Presets: `dpdp_strict`
(GA + fingerprint off, everything consent-gated), `balanced` (all sources on; fingerprint
under legitimate interest, cookie/GA wait for consent), `dev_open` (engineering only).
Live views: `GET /analytics` (pseudonymous), `GET /entities` (the ledger, masked),
`GET /entities/{id}` (the raw record exactly as captured). Identity ops:
`POST /bind /consent /attribute /erase`; `POST /reset` cleans all entries, keeps the posture.

## Run

```bash
docker compose up              # Overwatch on :8000  (visitor /  ·  operator /console)

# or without Docker:
pip install -r requirements.txt
python demo.py                 # config-driven walkthrough (flip posture, watch gates change)
pytest -q                      # 47 tests: engine + scanners + enforcement + PII vault + associators + tally + surfaces + config
uvicorn app:app --host 0.0.0.0 --port 8000
# visitor:  http://<host>:8000/          operator: http://<host>:8000/console
```

## Deploy for the demo (you have a domain)

```bash
uvicorn app:app --host 0.0.0.0 --port 8000       # behind your TLS terminator / reverse proxy
```
Serve over **HTTPS** — the first-party cookie uses `SameSite=Lax`; on your real domain set
`Secure` too. To forward real Google Analytics events: `export GA_MEASUREMENT_ID=G-XXXX
GA_API_SECRET=...` before launch (otherwise forwarding is a no-op and the demo uses an injected
transport). Point the demo domain at the box, open `/console` on the projector, `/` on a phone.

## Honest scope

**Overwatch sees only its own tab.** The visitor page pulls everything a page can within its
own origin: PII arriving without a keystroke (server-prefilled fields, browser-autofilled
fields via `:-webkit-autofill` + `animationstart`, `mailto:`/`tel:` links, named URL params,
patterns in the visible text) **and** the full device/environment surface (userAgent, languages,
screen, timezone, cores, memory, network), readable cookies, local/session storage, and every
non-password form field — one gated beacon carries the lot. But it is ordinary page JavaScript,
walled inside the same-origin policy. It **cannot**
read other tabs, other origins, browser history, or the wider autofill store; nothing in this
project attempts to. Reaching across the browser needs a privileged extension or malware and
has no lawful basis — out of scope by design. Every harvested value still passes the PII
collection gate, so it is kept only when the posture allows.

Google Analytics is a **pseudonymous source, not a PII vendor** — its id strengthens resolution,
never attribution; a real PII vendor plugs in behind `attribute`. Store is in-memory (resets on
restart; swap `InMemoryStore` for SQLite/Postgres through the one seam). The fingerprint
scanner hashes stable browser traits and is treated as deterministic here; a real probabilistic
matcher needs confidence scores and an unmerge path first — that is why it ships behind both a
reach lever and its own enforcement basis. The config plane is the compliance interface; wiring an actual DPDP
consent-management system behind `set_consent` is the production follow-on.
