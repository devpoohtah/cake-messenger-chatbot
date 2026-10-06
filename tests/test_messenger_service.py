from types import SimpleNamespace

from pydantic import SecretStr

from app.services import messenger_service as ms
from app.services.messenger_service import (
    build_product_carousel_message,
    build_quick_replies_message,
    extract_text_messages,
)

SAMPLE = {
    "object": "page",
    "entry": [{
        "id": "956850824188399",
        "messaging": [{
            "sender": {"id": "29952629271003601"},
            "recipient": {"id": "956850824188399"},
            "message": {"mid": "m_abc", "text": "Hi"},
        }],
    }],
}


def wrap(event):
    return {"object": "page", "entry": [{"id": "1", "messaging": [event]}]}


def test_extracts_text_message():
    msgs = extract_text_messages(SAMPLE)
    assert len(msgs) == 1
    assert msgs[0].sender_id == "29952629271003601" and msgs[0].text == "Hi"
    assert msgs[0].payload is None


def test_ignores_non_page_and_empty_payloads():
    assert extract_text_messages({}) == []
    assert extract_text_messages({"object": "user", "entry": []}) == []


def test_quick_reply_tap_keeps_text_and_payload():
    event = {"sender": {"id": "5"}, "recipient": {"id": "1"},
             "message": {"mid": "m1", "text": "2", "quick_reply": {"payload": "QTY:2"}}}
    msg = extract_text_messages(wrap(event))[0]
    assert msg.text == "2" and msg.payload == "QTY:2"


def test_postback_tap_is_extracted():
    event = {"sender": {"id": "5"}, "recipient": {"id": "1"},
             "postback": {"title": "Order this", "payload": "ORDER:3", "mid": "m2"}}
    msg = extract_text_messages(wrap(event))[0]
    assert msg.payload == "ORDER:3" and msg.text == "Order this" and msg.message_id == "m2"


def test_echo_events_are_skipped():
    event = {"sender": {"id": "5"}, "message": {"mid": "m", "text": "hi", "is_echo": True}}
    assert extract_text_messages(wrap(event)) == []


def test_quick_replies_message_shape_and_limits():
    options = [(f"Option number {i} is long", f"P:{i}") for i in range(20)]
    msg = build_quick_replies_message("Pick one", options, ask_phone=True)
    assert msg["text"] == "Pick one"
    assert len(msg["quick_replies"]) == 13
    assert all(len(q["title"]) <= 20 for q in msg["quick_replies"] if q["content_type"] == "text")
    phone = build_quick_replies_message("Phone?", [("Skip", "SKIP")], ask_phone=True)["quick_replies"]
    assert phone[-1] == {"content_type": "user_phone_number"}


def test_product_carousel_shape():
    products = [{"product_id": 7, "name": "Leche Flan", "price": 400, "description": None}]
    msg = build_product_carousel_message(products)
    el = msg["attachment"]["payload"]["elements"][0]
    assert msg["attachment"]["payload"]["template_type"] == "generic"
    assert el["title"] == "Leche Flan" and el["subtitle"] == "₱400.00"
    assert el["buttons"][0] == {"type": "postback", "title": "Order this", "payload": "ORDER:7"}


def test_send_posts_to_send_api(monkeypatch):
    sent = {}

    def fake_post(url, params=None, json=None, timeout=None):
        sent.update(url=url, params=params, json=json)
        return SimpleNamespace(status_code=200, text="ok")

    monkeypatch.setattr(ms.httpx, "post", fake_post)
    monkeypatch.setattr(ms, "get_settings", lambda: SimpleNamespace(meta_page_access_token=SecretStr("tok")))
    assert ms.MessengerService().send_text("5", "hello") is True
    assert sent["url"].endswith("/me/messages")
    assert sent["json"]["recipient"] == {"id": "5"} and sent["json"]["message"] == {"text": "hello"}


def test_send_fails_cleanly_without_token(monkeypatch):
    monkeypatch.setattr(ms, "get_settings", lambda: SimpleNamespace(meta_page_access_token=SecretStr("")))
    assert ms.MessengerService().send_text("5", "hello") is False