"""
Config-plane tests: compliance decisions are levers, and flipping a lever changes behavior.
"""
import pytest
from fastapi.testclient import TestClient

import app as appmod
from census import Census
from census.config import CensusConfig


@pytest.fixture
def client() -> TestClient:
    appmod.cx = Census()
    appmod.config = CensusConfig()       # defaults: strict (consent required, ga on)
    appmod.ga.sent.clear()
    return TestClient(appmod.app)


# === existence gate lever ===

def test_existence_gate_blocks_without_consent(client):
    """With require_observe_consent on, a beacon without consent creates no entity."""
    r = client.post("/collect", json={"session_id": "s1"}, cookies={"cx_anon": "A"})

    assert r.json()["observed"] is False
    assert client.get("/analytics").json()["total"] == 0


def test_existence_gate_opens_with_consent(client):
    """With consent supplied, the same beacon creates an entity."""
    r = client.post("/collect", json={"session_id": "s1", "consent": True}, cookies={"cx_anon": "A"})

    assert r.json()["observed"] is True


# === scanner lever toggles a source on/off ===

def test_ga_scanner_lever_off_disables_vendor_fusion(client):
    """With the GA scanner lever off, a cleared cookie is NOT recovered by the GA id."""
    client.put("/config", json={"scanners": {"ga": False}, "require_observe_consent": False})
    ga = "GA1.1.9.9"
    r1 = client.post("/collect", json={"session_id": "s1"}, cookies={"cx_anon": "A", "_ga": ga})
    r2 = client.post("/collect", json={"session_id": "s2"}, cookies={"_ga": ga})

    assert r2.json()["entity_id"] != r1.json()["entity_id"]


def test_ga_scanner_lever_on_enables_vendor_fusion(client):
    """With the GA scanner lever on, the GA id bridges the cleared cookie."""
    client.put("/config", json={"require_observe_consent": False})   # ga on by default
    ga = "GA1.1.9.9"
    r1 = client.post("/collect", json={"session_id": "s1"}, cookies={"cx_anon": "A", "_ga": ga})
    r2 = client.post("/collect", json={"session_id": "s2"}, cookies={"_ga": ga})

    assert r2.json()["entity_id"] == r1.json()["entity_id"]


# === presets and patches ===

def test_preset_dpdp_strict_sets_posture(client):
    """Applying the dpdp_strict preset turns the GA source off and requires observe-consent."""
    cfg = client.post("/config/preset", json={"name": "dpdp_strict"}).json()

    assert cfg["scanners"]["ga"] is False
    assert cfg["require_observe_consent"] is True


def test_put_config_patches_a_single_lever(client):
    """A partial PUT updates one lever and leaves the rest intact."""
    cfg = client.put("/config", json={"retention_days": 30}).json()

    assert cfg["retention_days"] == 30
    assert cfg["scanners"]["cookie"] is True
