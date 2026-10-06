from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_get_webhook_exists():
    r = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "1"})
    assert r.status_code != 404  # placeholder: currently 501


def test_post_webhook_exists():
    r = client.post("/webhook", json={"object": "page", "entry": []})
    assert r.status_code == 200
