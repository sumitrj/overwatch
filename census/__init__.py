"""
Census engine — Scanner signals resolve into pseudonymous entities; identity is a
separately gated layer on top.

Two boundaries, enforced here and only here:
  * existence — observe() refuses to create an entity without observe-consent
  * PII       — attribute() refuses to emit a named record unless bound AND consented
"""
from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Signals:
    session_id: str
    anon_id: str | None = None
    ga_client_id: str | None = None
    fingerprint: str | None = None
    pii: dict | None = None
    context: dict | None = None


@dataclass
class Resolution:
    entity_id: str
    archetype: str
    basis: list[str]


@dataclass
class AttributionResult:
    granted: bool
    record: dict | None = None


@dataclass
class Entity:
    entity_id: str
    sessions: set[str] = field(default_factory=set)
    identifiers: set[tuple[str, str]] = field(default_factory=set)
    merged_user: bool = False


class InMemoryStore:
    """The one seam: swap for SQLite/Postgres without touching the engine."""

    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self.by_identifier: dict[tuple[str, str], str] = {}
        self.bindings: dict[str, str] = {}
        self.consents: dict[str, dict[str, bool]] = {}
        self.pii_vault: dict[str, dict[str, str]] = {}


class Census:
    def __init__(self, store: InMemoryStore | None = None) -> None:
        self._s = store or InMemoryStore()

    # --- existence boundary ---------------------------------------------

    def observe(self, signals: Signals, *, consent_observe: bool) -> Resolution | None:
        if not consent_observe:
            return None

        identifiers = []
        if signals.anon_id:
            identifiers.append(("anon", signals.anon_id))
        if signals.ga_client_id:
            identifiers.append(("ga", signals.ga_client_id))
        if signals.fingerprint:
            identifiers.append(("fp", signals.fingerprint))
        if signals.pii and signals.pii.get("email"):
            identifiers.append(("email", signals.pii["email"]))

        entity = self._resolve(identifiers)
        entity.sessions.add(signals.session_id)
        for identifier in identifiers:
            entity.identifiers.add(identifier)
            self._s.by_identifier[identifier] = entity.entity_id
        if signals.pii:
            self.capture_pii(entity.entity_id, signals.pii)

        return Resolution(entity_id=entity.entity_id,
                          archetype=self.classify(entity.entity_id),
                          basis=[kind for kind, _ in identifiers])

    def _resolve(self, identifiers: list[tuple[str, str]]) -> Entity:
        matched = [self._s.by_identifier[i] for i in identifiers if i in self._s.by_identifier]
        if not matched:
            return self._mint()
        target = self._s.entities[matched[0]]
        for other_id in matched[1:]:
            if other_id != target.entity_id:
                self._merge(target, self._s.entities[other_id])
        return target

    def _mint(self) -> Entity:
        entity = Entity(entity_id=f"e_{uuid.uuid4().hex[:8]}")
        self._s.entities[entity.entity_id] = entity
        return entity

    def _merge(self, target: Entity, other: Entity) -> None:
        target.sessions |= other.sessions
        target.identifiers |= other.identifiers
        target.merged_user = target.merged_user or other.merged_user
        for identifier in other.identifiers:
            self._s.by_identifier[identifier] = target.entity_id
        if other.entity_id in self._s.bindings:
            self._s.bindings.setdefault(target.entity_id, self._s.bindings.pop(other.entity_id))
        if other.entity_id in self._s.consents:
            merged = self._s.consents.pop(other.entity_id) | self._s.consents.get(target.entity_id, {})
            self._s.consents[target.entity_id] = merged
        if other.entity_id in self._s.pii_vault:
            vaulted = self._s.pii_vault.pop(other.entity_id) | self._s.pii_vault.get(target.entity_id, {})
            self._s.pii_vault[target.entity_id] = vaulted
        del self._s.entities[other.entity_id]

    # --- identity layer ---------------------------------------------------

    def alias(self, *, anon_id: str | None, user_token: str) -> str:
        identifiers = [("user", user_token)] + ([("anon", anon_id)] if anon_id else [])
        entity = self._resolve(identifiers)
        for identifier in identifiers:
            entity.identifiers.add(identifier)
            self._s.by_identifier[identifier] = entity.entity_id
        entity.merged_user = True
        return entity.entity_id

    def classify(self, entity_id: str) -> str:
        entity = self._s.entities[entity_id]
        if entity.merged_user:
            return "logged_out_then_in"
        if len(entity.sessions) > 1:
            return "repeat_anonymous"
        return "new_anonymous"

    def bind(self, entity_id: str, customer_id: str) -> None:
        self._s.bindings[entity_id] = customer_id

    def set_consent(self, entity_id: str, purpose: str, granted: bool) -> None:
        self._s.consents.setdefault(entity_id, {})[purpose] = granted

    def capture_pii(self, entity_id: str, fields: dict) -> None:
        clean = {k: str(v) for k, v in fields.items() if v}
        if clean:
            self._s.pii_vault.setdefault(entity_id, {}).update(clean)

    # --- PII boundary -----------------------------------------------------

    def attribute(self, entity_id: str, *, purpose: str) -> AttributionResult:
        bound = entity_id in self._s.bindings
        consented = self._s.consents.get(entity_id, {}).get(purpose) is True
        if not (bound and consented):
            return AttributionResult(granted=False)
        return AttributionResult(granted=True, record={
            "entity_id": entity_id,
            "customer_id": self._s.bindings[entity_id],
            "purpose": purpose,
            "pii": dict(self._s.pii_vault.get(entity_id, {})),
        })

    def erase_identity(self, entity_id: str) -> None:
        """Right-to-erasure: drop the person, keep the pseudonymous graph."""
        self._s.bindings.pop(entity_id, None)
        self._s.consents.pop(entity_id, None)
        self._s.pii_vault.pop(entity_id, None)
        entity = self._s.entities[entity_id]
        for identifier in [i for i in entity.identifiers if i[0] in ("user", "email")]:
            entity.identifiers.discard(identifier)
            self._s.by_identifier.pop(identifier, None)
        entity.merged_user = False

    # --- pseudonymous analytics --------------------------------------------

    def analytics(self) -> dict:
        counts = Counter(self.classify(eid) for eid in self._s.entities)
        return {"total": len(self._s.entities), "by_archetype": dict(counts)}
