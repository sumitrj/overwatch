"""
Census configuration plane.

Every compliance decision is a declared lever, not a code path:
  * which Scanners are on (which sources we legally reach)
  * whether observe-consent is required before an entity may exist (existence gate policy)
  * which attribution purposes exist and their default consent (PII gate policy)
  * jurisdiction preset (bundles the above into a named posture, e.g. DPDP-strict)

The engine and app read ONLY this object. Changing posture = changing config, not code.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class CensusConfig:
    # Scanner levers: which sources are we reaching?
    scanners: dict[str, bool] = field(default_factory=lambda: {
        "cookie": True, "ga": True, "fingerprint": True, "pii": True})

    # Enforcement levers: the legal basis of each source. "consent" sources are withheld
    # until the visitor agrees; "legitimate_interest" sources are admitted immediately.
    enforcement: dict[str, str] = field(default_factory=lambda: {
        "cookie": "consent", "ga": "consent", "fingerprint": "legitimate_interest",
        "pii": "consent"})

    # Existence gate master switch: when off, no source waits for consent at all.
    require_observe_consent: bool = True

    # Associator levers: which data vendors may resolve/enrich visitors?
    associators: dict[str, bool] = field(default_factory=lambda: {"loan_users": True, "ip_enrichment": True})

    # PII gate levers: named purposes and their default consent
    purposes: list[str] = field(default_factory=lambda: ["analytics", "outreach"])
    default_consent: dict[str, bool] = field(default_factory=lambda: {"analytics": False, "outreach": False})

    # Posture metadata
    jurisdiction: str = "IN-DPDP"
    retention_days: int = 365

    def to_dict(self) -> dict:
        return asdict(self)

    def apply(self, patch: dict) -> "CensusConfig":
        """Apply a partial update (from the console) and return self."""
        for key, value in patch.items():
            if not hasattr(self, key):
                raise KeyError(key)
            current = getattr(self, key)
            if isinstance(current, dict) and isinstance(value, dict):
                current.update(value)
            else:
                setattr(self, key, value)
        return self


PRESETS: dict[str, dict] = {
    # Strict DPDP posture: nothing exists or crosses without explicit consent.
    "dpdp_strict": {
        "scanners": {"cookie": True, "ga": False, "fingerprint": False, "pii": False},
        "enforcement": {"cookie": "consent", "ga": "consent", "fingerprint": "consent",
                        "pii": "consent"},
        "require_observe_consent": True,
        "default_consent": {"analytics": False, "outreach": False},
        "jurisdiction": "IN-DPDP",
    },
    # Balanced: all sources on; cookie/ga wait for consent, fingerprint runs on
    # legitimate interest; consent still gates attribution.
    "balanced": {
        "scanners": {"cookie": True, "ga": True, "fingerprint": True, "pii": True},
        "enforcement": {"cookie": "consent", "ga": "consent", "fingerprint": "legitimate_interest",
                        "pii": "consent"},
        "require_observe_consent": True,
        "default_consent": {"analytics": True, "outreach": False},
        "jurisdiction": "IN-DPDP",
    },
    # Internal/dev: everything on, nothing waits, for engineering demos only.
    "dev_open": {
        "scanners": {"cookie": True, "ga": True, "fingerprint": True, "pii": True},
        "enforcement": {"cookie": "legitimate_interest", "ga": "legitimate_interest",
                        "fingerprint": "legitimate_interest", "pii": "legitimate_interest"},
        "require_observe_consent": False,
        "default_consent": {"analytics": True, "outreach": True},
        "jurisdiction": "DEV",
    },
}
