"""Shop hours, locked days and the order calendar."""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import admin
from app.services import shop_hours
from app.services.ai_service import AIResult, Intent
from app.services.conversation_service import IDLE, ConversationService
from app.services.shop_hours import PH_TIME, ShopStatus, evaluate

USER = "1001"


def ph(y, mo, d, h=12, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=PH_TIME)


# ---------- evaluate(): the pure rules ----------
def test_no_settings_means_open():
    assert evaluate(None, {}, ph(2026, 10, 14)).is_open


def test_master_switch_closes_the_shop():
    status = evaluate({"is_open": False}, {}, ph(2026, 10, 14))
    assert not status.is_open and status.reason == "switch" and status.reopens is None


def test_locked_day_closes_until_the_next_unlocked_day():
    locked = {date(2026, 10, 14): "Holiday", date(2026, 10, 15): None}
    status = evaluate({}, locked, ph(2026, 10, 14, 9))
    assert not status.is_open and status.reason == "day" and status.note == "Holiday"
    assert status.reopens.date() == date(2026, 10, 16) and not status.timed
    assert shop_hours.reopening_text(status) == "Oct 16"


def test_other_days_are_unaffected_by_a_locked_day():
    assert evaluate({}, {date(2026, 10, 14): None}, ph(2026, 10, 13)).is_open


HOURS = {"is_open": True, "hours_enabled": True, "open_time": "08:00:00", "close_time": "20:00:00"}


@pytest.mark.parametrize(
    "h, mi, expect_open",
    [(7, 59, False), (8, 0, True), (12, 0, True), (19, 59, True), (20, 0, False), (22, 0, False)],
)
def test_opening_hours_boundaries(h, mi, expect_open):
    assert evaluate(HOURS, {}, ph(2026, 10, 14, h, mi)).is_open is expect_open


def test_after_closing_reopens_tomorrow_at_opening_time():
    status = evaluate(HOURS, {}, ph(2026, 10, 14, 22))
    assert status.reason == "hours" and status.timed
    assert shop_hours.reopening_text(status) == "Oct 15, 8:00 AM"


def test_before_opening_reopens_today():
    assert shop_hours.reopening_text(evaluate(HOURS, {}, ph(2026, 10, 14, 6))) == "Oct 14, 8:00 AM"


def test_after_closing_skips_a_locked_tomorrow():
    status = evaluate(HOURS, {date(2026, 10, 15): None}, ph(2026, 10, 14, 21))
    assert shop_hours.reopening_text(status) == "Oct 16, 8:00 AM"


def test_hours_switched_off_ignore_the_times():
    assert evaluate({**HOURS, "hours_enabled": False}, {}, ph(2026, 10, 14, 23)).is_open


def test_unusable_hours_never_lock_customers_out():
    assert evaluate({**HOURS, "open_time": "20:00", "close_time": "08:00"}, {}, ph(2026, 10, 14, 23)).is_open
    assert evaluate({**HOURS, "open_time": None}, {}, ph(2026, 10, 14, 23)).is_open


def test_philippine_day_is_used_not_utc():
    utc_evening = datetime(2026, 10, 13, 16, 30, tzinfo=timezone.utc)  # already Oct 14, 00:30 in Manila
    assert not evaluate({}, {date(2026, 10, 14): None}, utc_evening).is_open
    assert evaluate({}, {date(2026, 10, 13): None}, utc_evening).is_open


def test_clock_formatting():
    assert shop_hours.clock(datetime(2026, 1, 1, 0, 5).time()) == "12:05 AM"
    assert shop_hours.clock(datetime(2026, 1, 1, 13, 30).time()) == "1:30 PM"


# ---------- a tiny in-memory database ----------
class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.op, self.payload, self.conflict = db, name, [], "select", None, None
        self.sort = None

    def select(self, *a, **k): return self
    def eq(self, c, v): self.filters.append((c, lambda x: x == v)); return self
    def gte(self, c, v): self.filters.append((c, lambda x: x >= v)); return self
    def lt(self, c, v): self.filters.append((c, lambda x: x < v)); return self
    def limit(self, n): return self
    def order(self, col, desc=False): self.sort = col; return self
    def upsert(self, row, on_conflict=None): self.op, self.payload, self.conflict = "upsert", row, on_conflict; return self
    def delete(self): self.op = "delete"; return self

    def _rows(self):
        return [r for r in self.db.tables.setdefault(self.name, []) if all(f(r.get(c)) for c, f in self.filters)]

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self.op == "upsert":
            key = self.conflict
            existing = next((r for r in rows if key and r.get(key) == self.payload.get(key)), None)
            if existing:
                existing.update(self.payload)
                return _Result([existing])
            rows.append(dict(self.payload))
            return _Result([self.payload])
        if self.op == "delete":
            self.db.tables[self.name] = [r for r in rows if r not in self._rows()]
            return _Result([])
        found = self._rows()
        if self.sort:
            found.sort(key=lambda r: r[self.sort])
        return _Result(found)


class FakeDB:
    def __init__(self, **tables):
        self.tables = {k: list(v) for k, v in tables.items()}
        self.rpc_calls = []

    def table(self, name):
        return _Query(self, name)

    def rpc(self, fn, params):
        self.rpc_calls.append((fn, params))
        raise AssertionError("no order may be created in these tests")


# ---------- current_status(): reads the database ----------
def test_current_status_reads_settings_and_locked_days():
    db = FakeDB(shop_settings=[{"id": 1, "is_open": True}], closed_days=[{"day": "2026-10-14", "reason": "Holiday"}])
    assert not shop_hours.current_status(db, ph(2026, 10, 14)).is_open
    assert shop_hours.current_status(db, ph(2026, 10, 13)).is_open


def test_past_locked_days_are_ignored():
    db = FakeDB(closed_days=[{"day": "2026-10-01", "reason": None}])
    assert shop_hours.current_status(db, ph(2026, 10, 14)).is_open


def test_database_failure_fails_open():
    class Broken:
        def table(self, name):
            raise RuntimeError("database down")

    assert shop_hours.current_status(Broken(), ph(2026, 10, 14)).is_open


# ---------- the bot ----------
CLOSED = ShopStatus(False, "day", "Family event", datetime(2026, 10, 16, 8, 0, tzinfo=PH_TIME), True)
PRODUCTS = [{"product_id": 1, "name": "Mango Float", "description": None, "price": 400, "is_available": True}]


class _NoAI:
    def interpret_message(self, *a, **k):
        raise AssertionError("the AI must not be needed")


def _service(monkeypatch, status):
    monkeypatch.setattr(shop_hours, "current_status", lambda client, now=None: status)
    return ConversationService(_NoAI(), FakeDB(products=PRODUCTS))


def test_closed_shop_refuses_to_start_an_order(monkeypatch):
    svc = _service(monkeypatch, CLOSED)
    replies = svc.handle(USER, "", "m1", payload="ORDER:1")
    assert "closed" in replies[0].text and "Oct 16, 8:00 AM" in replies[0].text and "Family event" in replies[0].text
    assert [payload for _, payload in replies[0].options] == ["MENU", "TALK_OWNER"]  # menu and owner still reachable
    assert svc._client.tables["conversations"][0]["state"] == IDLE


def test_open_shop_starts_the_order_normally(monkeypatch):
    svc = _service(monkeypatch, ShopStatus())
    replies = svc.handle(USER, "", "m1", payload="ORDER:1")
    assert "How many" in replies[0].text


def test_closed_shop_cannot_confirm_an_order(monkeypatch):
    svc = _service(monkeypatch, CLOSED)
    replies, state, _ = svc._place_order(USER, {"lang": "en"}, PRODUCTS)  # would raise if it reached the database
    assert "closed" in replies[0].text and state == IDLE and svc._client.rpc_calls == []


def test_closed_message_without_a_known_reopening_date(monkeypatch):
    svc = _service(monkeypatch, ShopStatus(False, "switch"))
    assert "not taking orders" in svc.handle(USER, "", "m1", payload="ORDER:1")[0].text


# ---------- dashboard API ----------
@pytest.fixture
def api():
    db = FakeDB()
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[admin.get_db] = lambda: db
    return TestClient(app), db


def _today():
    return datetime.now(PH_TIME).date()


def test_shop_defaults_and_validation(api):
    client, _ = api
    body = client.get("/admin/api/shop").json()
    assert body["is_open"] and not body["hours_enabled"] and body["accepting_orders"]
    bad = client.patch("/admin/api/shop", json={"hours_enabled": True, "open_time": "20:00", "close_time": "08:00"})
    assert bad.status_code == 400
    assert client.patch("/admin/api/shop", json={"close_time": "25:00"}).status_code == 422
    ok = client.patch("/admin/api/shop", json={"hours_enabled": True, "open_time": "09:00", "close_time": "22:30"}).json()
    assert ok["hours_enabled"] and ok["close_time"] == "22:30"


def test_master_switch_is_reflected(api):
    client, _ = api
    body = client.patch("/admin/api/shop", json={"is_open": False}).json()
    assert not body["is_open"] and not body["accepting_orders"] and body["closed_because"] == "switch"


def test_lock_and_unlock_a_day(api):
    client, db = api
    day = (_today() + timedelta(days=3)).isoformat()
    assert client.put(f"/admin/api/closed-days/{day}", json={"reason": "Holiday"}).json()["locked"]
    assert db.tables["closed_days"] == [{"day": day, "reason": "Holiday"}]
    assert client.get(f"/admin/api/calendar/{day}").json()["reason"] == "Holiday"
    assert not client.delete(f"/admin/api/closed-days/{day}").json()["locked"]
    assert db.tables["closed_days"] == []


def test_cannot_lock_a_past_day(api):
    client, _ = api
    assert client.put(f"/admin/api/closed-days/{(_today() - timedelta(days=1)).isoformat()}").status_code == 400


def test_calendar_counts_orders_by_manila_day(api):
    client, db = api
    db.tables["orders"] = [
        {"order_id": 1, "order_date": "2026-10-13T17:30:00+00:00", "status": "pending"},  # Oct 14 01:30 in Manila
        {"order_id": 2, "order_date": "2026-10-14T03:00:00+00:00", "status": "completed"},
        {"order_id": 3, "order_date": "2026-10-14T04:00:00+00:00", "status": "cancelled"},  # not counted
    ]
    db.tables["closed_days"] = [{"day": "2026-10-20", "reason": "Trip"}]
    days = client.get("/admin/api/calendar?month=2026-10").json()["days"]
    assert days["2026-10-14"]["orders"] == 2 and not days["2026-10-14"]["locked"]
    assert days["2026-10-20"] == {"orders": 0, "locked": True, "reason": "Trip"}
    assert client.get("/admin/api/calendar?month=2026-13").status_code == 400


# ---------- "are you open?" is answered from the database ----------
def _ask_hours(monkeypatch, status, topic="hours"):
    svc = _service(monkeypatch, status)
    return svc._answer(AIResult(intent=Intent.FAQ_QUESTION, faq_topic=topic), {"lang": "en"}, PRODUCTS, PRODUCTS)[0]


def test_open_with_hours_says_until_when(monkeypatch):
    reply = _ask_hours(monkeypatch, evaluate(HOURS, {}, ph(2026, 10, 14, 12)))
    assert "open right now" in reply.text and "8:00 PM" in reply.text


def test_closed_answer_includes_the_owners_reason_and_reopening(monkeypatch):
    reply = _ask_hours(monkeypatch, CLOSED)
    assert "Family event" in reply.text and "Oct 16, 8:00 AM" in reply.text


def test_open_without_hours_falls_back_to_the_faq_text(monkeypatch):
    reply = _ask_hours(monkeypatch, ShopStatus())
    assert "until" not in reply.text and reply.text  # the owner's FAQ answer, unchanged


def test_other_faq_topics_never_check_the_shop(monkeypatch):
    def boom(client, now=None):
        raise AssertionError("shop status must not be read for other topics")

    monkeypatch.setattr(shop_hours, "current_status", boom)
    svc = ConversationService(_NoAI(), FakeDB(products=PRODUCTS))
    assert svc._answer(AIResult(intent=Intent.FAQ_QUESTION, faq_topic="payment"), {"lang": "en"}, PRODUCTS, PRODUCTS)


def test_evaluate_reports_the_closing_time_while_open():
    assert evaluate(HOURS, {}, ph(2026, 10, 14, 12)).closes.hour == 20
    assert evaluate({}, {}, ph(2026, 10, 14, 12)).closes is None
