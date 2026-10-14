"""Language switching, delivery/hours/stock answers, order status and cancel, attachments, draft expiry,
missed orders, handoff wording, the automated-assistant note and the Insights endpoint."""
from datetime import datetime, time, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import admin
from app.services import conversation_service, shop_hours
from app.services.ai_service import AIResult, Intent
from app.services.conversation_service import COLLECTING, IDLE, TALK_OWNER, ConversationService, _draft_expired, _new_draft
from app.services.faq import FAQS
from app.services.messages import t
from app.services.messenger_service import build_product_carousel_message, extract_text_messages
from app.services.shop_hours import PH_TIME, ShopStatus

USER, OTHER = "1001", "2002"
PRODUCTS = [
    {"product_id": 1, "name": "Mango Float", "description": "Layered dessert", "price": 400, "is_available": True, "stock": 3},
    {"product_id": 2, "name": "Leche Flan", "description": None, "price": 400, "is_available": True, "stock": None},
]
AREAS = [
    {"id": 1, "name": "Jaro", "fee": 50, "is_active": True, "is_other": False},
    {"id": 2, "name": "Molo", "fee": 70, "is_active": True, "is_other": False},
]
OTHER_ON = {"id": 3, "name": "Other areas", "fee": None, "is_active": True, "is_other": True}
CLOSED = ShopStatus(False, "day", "Family event", datetime(2026, 10, 16, 8, 0, tzinfo=PH_TIME), True)


# ---------- a small in-memory database ----------
class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, name):
        self.db, self.name, self.op, self.payload, self.conflict = db, name, "select", None, None
        self.filters, self.sort, self.desc, self.max = [], None, False, None

    def select(self, *a, **k): return self
    def eq(self, c, v): self.filters.append((c, lambda x: x == v)); return self
    def in_(self, c, vals): self.filters.append((c, lambda x: x in vals)); return self
    def gte(self, c, v): self.filters.append((c, lambda x: x is not None and x >= v)); return self
    def lt(self, c, v): self.filters.append((c, lambda x: x is not None and x < v)); return self
    def order(self, col, desc=False): self.sort, self.desc = col, desc; return self
    def limit(self, n): self.max = n; return self
    def insert(self, row): self.op, self.payload = "insert", row; return self
    def update(self, row): self.op, self.payload = "update", row; return self
    def upsert(self, row, on_conflict=None): self.op, self.payload, self.conflict = "upsert", row, on_conflict; return self
    def delete(self): self.op = "delete"; return self

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self.op == "insert":
            row = {"id": len(rows) + 1, "created_at": datetime.now(timezone.utc).isoformat(), **self.payload}
            rows.append(row)
            return _Result([row])
        if self.op == "upsert":
            hit = next((r for r in rows if self.conflict and r.get(self.conflict) == self.payload.get(self.conflict)), None)
            if hit:
                hit.update(self.payload)
                return _Result([hit])
            rows.append(dict(self.payload))
            return _Result([self.payload])
        matched = [r for r in rows if all(f(r.get(c)) for c, f in self.filters)]
        if self.op == "update":
            for r in matched:
                r.update(self.payload)
            return _Result(matched)
        if self.op == "delete":
            self.db.tables[self.name] = [r for r in rows if r not in matched]
            return _Result([])
        if self.sort:
            matched = sorted(matched, key=lambda r: r[self.sort], reverse=self.desc)
        return _Result(matched[: self.max] if self.max else matched)


class FakeDB:
    def __init__(self, **tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}

    def table(self, name):
        return _Query(self, name)

    def rpc(self, fn, params):
        raise AssertionError("no order may be created in these tests")


class FakeAI:
    def __init__(self, *results):
        self.results, self.calls = list(results), 0

    def interpret_message(self, message, state, names, topics=None):
        self.calls += 1
        return self.results.pop(0)


def db(**tables):
    return FakeDB(products=PRODUCTS, **tables)


def say(database, ai, text="", payload=None, mid=None, attachment=None):
    return ConversationService(ai, database).handle(USER, text, mid, payload, attachment)


def ask(result):
    return FakeAI(result)


def faq(topic, place=None, lang="en"):
    return AIResult(intent=Intent.FAQ_QUESTION, faq_topic=topic, place=place, language=lang)


def option_payloads(reply):
    return [p for _, p in reply.options]


def status(monkeypatch, value):
    monkeypatch.setattr(shop_hours, "current_status", lambda client, now=None: value)


@pytest.fixture(autouse=True)
def shop_open(monkeypatch):
    """By default the shop is open with no opening hours set."""
    status(monkeypatch, ShopStatus())


# ---------- phase 1: language ----------
def test_language_follows_every_message_with_no_waiting():
    d = db()
    say(d, ask(AIResult(intent=Intent.GREETING, language="en")), "hello")
    assert say(d, ask(AIResult(intent=Intent.GREETING, language="hil")), "kamusta")[0].text.startswith(t("hil", "greeting"))
    assert say(d, ask(AIResult(intent=Intent.GREETING, language=None)), "ok")[0].text.startswith(t("hil", "greeting"))  # unclear: keep
    assert say(d, ask(AIResult(intent=Intent.GREETING, language="tl")), "kumusta po")[0].text.startswith(t("tl", "greeting"))
    assert say(d, ask(AIResult(intent=Intent.GREETING, language="en")), "hi")[0].text.startswith(t("en", "greeting"))


# ---------- phase 1: delivery areas and fees ----------
def test_delivery_question_lists_areas_and_fees_from_the_database():
    reply = say(db(delivery_areas=AREAS + [OTHER_ON]), ask(faq("delivery")), "do you deliver?")[0]
    assert "Jaro" in reply.text and "Molo" in reply.text and "50" in reply.text and "70" in reply.text
    assert "owner will confirm" in reply.text


def test_delivery_question_names_the_fee_of_the_area_asked_about():
    text = say(db(delivery_areas=AREAS), ask(faq("delivery", place="Jaro", lang="hil")), "ga deliver kmo jaro?")[0].text
    assert "Jaro" in text and "50" in text and text.startswith(t("hil", "delivery_area_fee", area="Jaro", fee="₱50.00"))


def test_unlisted_area_with_other_areas_on_leaves_the_fee_to_the_owner():
    text = say(db(delivery_areas=AREAS + [OTHER_ON]), ask(faq("delivery", place="Pavia")), "do you deliver to Pavia?")[0].text
    assert "Pavia" in text and "isn't on our delivery list" in text


def test_unlisted_area_with_other_areas_off_is_refused_and_listed():
    text = say(db(delivery_areas=AREAS), ask(faq("delivery", place="Pavia")), "deliver to Pavia?")[0].text
    assert "don't deliver to Pavia" in text and "Jaro" in text and "Molo" in text


def test_flat_fee_mode_keeps_the_faq_text():
    text = say(db(), ask(faq("delivery")), "do you deliver?")[0].text
    assert text.startswith(FAQS["delivery"]["en"])


# ---------- phase 1: live hours in pickup and location answers ----------
def test_pickup_answer_adds_todays_closing_time(monkeypatch):
    status(monkeypatch, ShopStatus(closes=time(20, 0)))
    assert "open today until 8:00 PM" in say(db(), ask(faq("pickup")), "can I pick up?")[0].text


def test_location_answer_says_when_closed(monkeypatch):
    status(monkeypatch, CLOSED)
    text = say(db(), ask(faq("location")), "where are you?")[0].text
    assert "closed" in text and "Oct 16, 8:00 AM" in text and "Family event" in text


def test_pickup_text_no_longer_hard_codes_hours():
    assert all("11:00" not in FAQS["pickup"][lang] for lang in ("en", "tl", "hil"))


# ---------- phase 1: low stock ----------
def test_price_answer_warns_when_stock_is_low_but_not_when_unlimited():
    low = say(db(), ask(AIResult(intent=Intent.PRICE_QUESTION, product="Mango Float", language="en")), "price mango float")[0].text
    assert "Only 3 left!" in low
    plenty = say(db(), ask(AIResult(intent=Intent.PRICE_QUESTION, product="Leche Flan", language="en")), "price leche flan")[0].text
    assert "left" not in plenty


def test_menu_carries_a_low_stock_label_and_cards_show_it():
    reply = say(db(), FakeAI(), payload="MENU")[0]
    assert reply.low_stock == "Only {n} left!"
    cards = build_product_carousel_message(PRODUCTS, "Order", reply.low_stock)["attachment"]["payload"]["elements"]
    assert "Only 3 left!" in cards[0]["subtitle"] and "left" not in cards[1]["subtitle"]
    plain = build_product_carousel_message(PRODUCTS, "Order")["attachment"]["payload"]["elements"]
    assert "left" not in plain[0]["subtitle"]


# ---------- phase 2: new FAQ topics ----------
@pytest.mark.parametrize("topic", ["lead_time", "advance_order", "bulk_orders", "cancellation"])
def test_new_faq_topics_exist_in_every_language(topic):
    assert all(FAQS[topic].get(lang) for lang in ("en", "tl", "hil"))


def test_same_day_only_answer():
    assert "same-day" in say(db(), ask(faq("advance_order")), "can I order for tomorrow?")[0].text


# ---------- phase 2: where is my order / cancel ----------
def orders_db():
    def order(oid, cid, st, minutes):
        return {"order_id": oid, "customer_id": cid, "status": st, "total_amount": 400,
                "order_date": (datetime(2026, 10, 14, tzinfo=timezone.utc) + timedelta(minutes=minutes)).isoformat(),
                "order_items": [{"quantity": 1, "products": {"name": "Mango Float"}}]}
    return db(customers=[{"customer_id": 7, "messenger_id": USER}, {"customer_id": 8, "messenger_id": OTHER}],
              orders=[order(11, 7, "pending", 1), order(12, 7, "preparing", 2), order(13, 8, "pending", 3), order(14, 7, "completed", 4)])


def test_my_orders_lists_open_orders_and_offers_cancel_only_for_pending():
    reply = say(orders_db(), FakeAI(), payload="MY_ORDERS")[0]
    assert "#11" in reply.text and "#12" in reply.text and "#13" not in reply.text and "#14" not in reply.text
    pays = option_payloads(reply)
    assert "CANCEL_ORDER:11" in pays and "CANCEL_ORDER:12" not in pays and "CANCEL_ORDER:13" not in pays


def test_cancel_pending_order_asks_first_then_cancels():
    d = orders_db()
    ask_reply = say(d, FakeAI(), payload="CANCEL_ORDER:11")[0]
    assert option_payloads(ask_reply) == ["CANCEL_ORDER_YES:11", "KEEP_ORDER:11"]
    assert next(o for o in d.tables["orders"] if o["order_id"] == 11)["status"] == "pending"  # asking changes nothing
    assert say(d, FakeAI(), payload="CANCEL_ORDER_YES:11")[0].text.startswith(t("en", "order_cancelled_ok", order_id=11))
    assert next(o for o in d.tables["orders"] if o["order_id"] == 11)["status"] == "cancelled"


def test_keeping_an_order_leaves_it_pending():
    d = orders_db()
    say(d, FakeAI(), payload="KEEP_ORDER:11")
    assert next(o for o in d.tables["orders"] if o["order_id"] == 11)["status"] == "pending"


def test_an_order_the_owner_started_cannot_be_cancelled():
    d = orders_db()
    reply = say(d, FakeAI(), payload="CANCEL_ORDER_YES:12")[0]
    assert reply.text == t("en", "order_cancel_late", order_id=12) and TALK_OWNER in option_payloads(reply)
    assert next(o for o in d.tables["orders"] if o["order_id"] == 12)["status"] == "preparing"


def test_nobody_can_cancel_another_customers_order():
    d = orders_db()
    say(d, FakeAI(), payload="CANCEL_ORDER_YES:13")
    assert next(o for o in d.tables["orders"] if o["order_id"] == 13)["status"] == "pending"


def test_cancel_intent_while_idle_shows_the_orders():
    reply = say(orders_db(), ask(AIResult(intent=Intent.CANCEL_ORDER, language="en")), "i-cancel ko na ang order ko")[0]
    assert "CANCEL_ORDER:11" in option_payloads(reply)


def test_cancellation_faq_offers_my_orders():
    assert "MY_ORDERS" in option_payloads(say(db(), ask(faq("cancellation")), "can I cancel?")[0])


# ---------- phase 3: attachments ----------
def test_attachments_are_picked_out_of_the_webhook_payload():
    def event(mid, **message):
        return {"sender": {"id": "1"}, "recipient": {"id": "9"}, "message": {"mid": mid, **message}}
    payload = {"object": "page", "entry": [{"messaging": [
        event("m1", attachments=[{"type": "image", "payload": {"url": "x"}}]),
        event("m2", attachments=[{"type": "image", "payload": {"sticker_id": 369239263222822}}]),
        event("m3", text="hi"),
    ]}]}
    assert [m.attachment for m in extract_text_messages(payload)] == ["image", "sticker", None]


def test_a_photo_gets_a_polite_reply_with_the_owner_button_and_no_ai_call():
    ai = FakeAI()
    reply = say(db(), ai, attachment="image")[0]
    assert reply.text == t("en", "attachment_received") and TALK_OWNER in option_payloads(reply) and ai.calls == 0


def test_a_sticker_gets_no_reply():
    assert say(db(), FakeAI(), attachment="sticker") == []


def test_a_photo_does_not_disturb_an_order_in_progress():
    d = db(conversations=[{"messenger_id": USER, "state": COLLECTING, "draft": _new_draft("en")}])
    say(d, FakeAI(), attachment="image")
    assert d.tables["conversations"][0]["state"] == COLLECTING


# ---------- phase 3: draft expiry ----------
def test_draft_expiry_rule():
    now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    assert not _draft_expired({}, now)  # no stamp: kept
    assert not _draft_expired({"touched": (now - timedelta(hours=23)).isoformat()}, now)
    assert _draft_expired({"touched": (now - timedelta(hours=25)).isoformat()}, now)
    assert not _draft_expired({"touched": "garbage"}, now)


def test_an_old_unfinished_order_is_cleared_with_a_notice():
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    d = db(conversations=[{"messenger_id": USER, "state": COLLECTING, "draft": _new_draft("en") | {"touched": old}}])
    replies = say(d, ask(AIResult(intent=Intent.GREETING, language="en")), "hi")
    assert replies[0].text == t("en", "draft_expired")
    assert d.tables["conversations"][0]["state"] == IDLE


def test_a_recent_unfinished_order_is_kept():
    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    d = db(conversations=[{"messenger_id": USER, "state": COLLECTING, "draft": _new_draft("en") | {"touched": recent}}])
    replies = say(d, ask(AIResult(intent=Intent.GREETING, language="en")), "hi")
    assert all(r.text != t("en", "draft_expired") for r in replies)


# ---------- phase 3: missed orders ----------
def test_an_order_attempt_while_closed_is_recorded_once_per_half_hour(monkeypatch):
    status(monkeypatch, CLOSED)
    d = db()
    say(d, FakeAI(), payload="ORDER:1")
    say(d, FakeAI(), payload="ORDER:1")
    assert [(r["messenger_id"], r["reason"]) for r in d.tables["missed_orders"]] == [(USER, "day")]


def test_asking_whether_we_are_open_is_not_a_missed_order(monkeypatch):
    status(monkeypatch, CLOSED)
    d = db()
    say(d, ask(faq("hours")), "are you open today?")
    assert d.tables.get("missed_orders", []) == []


def test_an_open_shop_records_nothing():
    d = db()
    say(d, FakeAI(), payload="ORDER:1")
    assert d.tables.get("missed_orders", []) == []


# ---------- phase 3: handoff wording ----------
def test_handoff_while_closed_says_when_the_owner_is_back(monkeypatch):
    status(monkeypatch, CLOSED)
    assert "Oct 16, 8:00 AM" in say(db(), FakeAI(), payload=TALK_OWNER)[0].text


def test_handoff_while_open_keeps_the_normal_message():
    assert say(db(), FakeAI(), payload=TALK_OWNER)[0].text == t("en", "owner_handoff")


# ---------- the automated-assistant note ----------
def test_greeting_and_faq_answers_carry_the_note_and_it_can_be_switched_off(monkeypatch):
    note = t("en", "ai_disclaimer")
    assert note in say(db(), ask(AIResult(intent=Intent.GREETING, language="en")), "hi")[0].text
    assert note in say(db(), ask(faq("payment")), "how to pay?")[0].text
    monkeypatch.setattr(conversation_service, "SHOW_DISCLAIMER", False)
    assert note not in say(db(), ask(AIResult(intent=Intent.GREETING, language="en")), "hi")[0].text


def test_the_note_never_claims_the_answers_are_ai_written():
    assert "pre-written" in t("en", "ai_disclaimer")


# ---------- the Insights endpoint ----------
@pytest.fixture
def api():
    database = FakeDB()
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[admin.get_db] = lambda: database
    return TestClient(app), database


def test_insights_counts_missed_orders_by_manila_day_and_ranks_unanswered_questions(api):
    client, database = api
    now = datetime.now(timezone.utc)
    database.tables["missed_orders"] = [
        {"created_at": now.isoformat(), "reason": "hours"},
        {"created_at": now.isoformat(), "reason": "hours"},
        {"created_at": now.isoformat(), "reason": "day"},
        {"created_at": (now - timedelta(days=60)).isoformat(), "reason": "day"},  # outside the window
    ]
    database.tables["owner_inbox"] = [
        {"kind": "unanswered", "message": "Ube cake?", "created_at": now.isoformat()},
        {"kind": "unanswered", "message": "  ube   CAKE? ", "created_at": now.isoformat()},
        {"kind": "unanswered", "message": "Gluten free?", "created_at": now.isoformat()},
        {"kind": "handoff", "message": "talk to owner", "created_at": now.isoformat()},
    ]
    body = client.get("/admin/api/insights").json()
    assert body["missed"]["total"] == 3 and sum(d["count"] for d in body["missed"]["days"]) == 3
    assert body["missed"]["days"][0]["reasons"] == {"hours": 2, "day": 1}
    assert body["unanswered"] == [{"message": "ube cake?", "count": 2}, {"message": "gluten free?", "count": 1}]


def test_insights_still_works_when_the_missed_orders_table_is_missing(api):
    client, database = api
    database.table = lambda name: (_ for _ in ()).throw(RuntimeError("no such table")) if name == "missed_orders" else _Query(database, name)
    body = client.get("/admin/api/insights").json()
    assert body["missed"] is None and body["unanswered"] == []
