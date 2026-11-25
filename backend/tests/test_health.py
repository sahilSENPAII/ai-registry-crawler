from fastapi.testclient import TestClient

from app.api import app

# No `with` block: the startup hook (wait_for_db) is skipped, so this passes with the DB down.
client = TestClient(app)


def test_health_returns_status():
    r = client.get("/health")
    assert r.status_code == 200
    assert "status" in r.json()
