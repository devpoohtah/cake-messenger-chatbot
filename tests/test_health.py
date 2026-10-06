from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root_endpoint():
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "running"
