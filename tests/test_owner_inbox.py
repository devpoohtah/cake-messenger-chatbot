"""Phase 6: talk to the owner, quiet bot while the owner has the customer, saved questions."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.routes import admin
from app.main import app
from app.services import owner_inbox
from app.services.ai_service import AIResult, AIUnavailableError, Intent
from app.services.conversation_service import (
    COLLECTING, IDLE, TALK_OWNER, ConversationService, _wants_owner,
)
from app.services.messages import t

USER = "1001"
PRODUCTS = [
    {"product_id": 1, "name": "Mango Float", "description": None, "price": 400, "is_available": True},
    {"product_id": 2, "name": "Leche Flan", "description": None, "price": 400, "is_available": True},
]


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """Just enough of the Supabase query builder for these tests."""

    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op, self.payload, self.filters, self.desc, self.max = "select", None, [], False, None

    def select(self, *a, **k): return self
    def eq(self, col, val): self.filters.append((col, lambda v, val=val: v == val)); return self
    def in_(self, col, vals): self.filters.append((col, lambda v, vals=vals: v in vals)); return self
    def order(self, col, desc=False): self.order_col, self.desc = col, desc; return self
    def limit(self, n): self.max = n; return self
    def insert(self, row): self.op, self.payload = "insert", row; return self
    def update(self, row): self.op, self.payload = "update", row; return self
    def upsert(self, row, **k): self.op, self.payload = "upsert", row; return self

    def _match(self, row): return all(f(row.get(c)) for c, f in self.filters)

    def execute(self):
        db = self.db
        if self.db.broken_tables and self.name in self.db.broken_tables:
            raise RuntimeError("relation does not exist")
        if self.name == "products":
            return _Result(PRODUCTS)
        if self.name == "faqs":
            return _Result([])
        if self.name == "conversations":
            if self.op == "upsert":
                db.conversations[self.payload["messenger_id"]] = dict(self.payload)
                return _Result([self.payload])
            return _Result([r for r in db.conversations.values() if self._match(r)])
        rows = db.tables.setdefault(self.name, [])
        if self.op == "insert":
            row = {"id": len(rows) + 1, "status": "open", "back_notice_pending": False,
                   "created_at": datetime.now(timezone.utc).isoformat(), **self.payload}
            rows.append(row)
            return _Result([row])
        found = [r for r in rows if self._match(r)]
        if self.op == "update":
            for r in found:
                r.update(self.payload)
            return _Result(found)
        found = sorted(found, key=lambda r: r.get(getattr(self, "order_col", "id"), 0), reverse=self.desc)
        return _Result(found[: self.max] if self.max else found)


class FakeDB:
    def __init__(self, broken_tables=()):
        self.conversations, self.tables, self.broken_tables = {}, {}, set(broken_tables)

    def table(self, name): return _Query(self, name)

    @property
    def inbox(self): return self.tables.get("owner_inbox", [])


class FakeAI:
    def __init__(self, *results):
        self.results, self.calls = list(results), 0

    def interpret_message(self, message, state, names, topics=None):
        self.calls += 1
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def say(db, ai, text="", payload=None, mid=None):
    """One customer message through a fresh service, the way the webhook does it."""
    return ConversationService(ai, db).handle(USER, text, mid, payload)


def texts(replies):
    return [r.text for r in replies]


def test_wants_owner_matches_requests_not_questions_about_the_owner():
    assert _wants_owner("talk to the owner")
    assert _wants_owner("can i talk to a real person")
    assert _wants_owner("gusto ko makausap ang owner")
    assert _wants_owner("kausapon ko ang owner")
    assert _wants_owner("owner")
    assert not _wants_owner("who is the owner of the shop")
    assert not _wants_owner("")
    assert not _wants_owner("talk to the owner " + "blah " * 10)  # long messages go to the AI


def test_button_opens_a_handoff_without_calling_the_ai():
    db, ai = FakeDB(), FakeAI()
    replies = say(db, ai, "Talk to the owner", payload=TALK_OWNER)
    assert texts(replies) == [t("en", "owner_handoff")]
    assert [(r["kind"], r["status"]) for r in db.inbox] == [("handoff", "open")]
    assert ai.calls == 0


@pytest.mark.parametrize("typed", ["talk to the owner", "Gusto ko makausap ang owner!", "kausapon ko ang owner"])
def test_typed_request_opens_a_handoff_without_calling_the_ai(typed):
    db, ai = FakeDB(), FakeAI()
    replies = say(db, ai, typed)
    assert texts(replies) == [t("en", "owner_handoff")]
    assert db.inbox[0]["message"] == typed
    assert ai.calls == 0


def test_ai_intent_opens_a_handoff():
    db = FakeDB()
    ai = FakeAI(AIResult(intent=Intent.TALK_TO_OWNER, language="tl"))
    replies = say(db, ai, "pa-contact naman sa owner")
    assert texts(replies) == [t("tl", "owner_handoff")]  # the first detected language is used straight away
    assert db.inbox[0]["kind"] == "handoff"


def test_bot_stays_quiet_while_the_owner_has_the_customer():
    db, ai = FakeDB(), FakeAI()
    say(db, ai, "talk to the owner")
    assert say(db, ai, "magkano po ba ang leche flan?") == []
    assert say(db, ai, "hello??") == []
    assert ai.calls == 0
    assert len(db.inbox) == 1 and db.inbox[0]["message"] == "hello??"  # owner sees the latest message


def test_quiet_mode_still_ignores_meta_retries():
    db, ai = FakeDB(), FakeAI()
    say(db, ai, "talk to the owner")
    assert say(db, ai, "hello", mid="m1") == []
    assert say(db, ai, "hello", mid="m1") is None


def test_second_tap_says_the_owner_was_already_told():
    db, ai = FakeDB(), FakeAI()
    say(db, ai, "talk to the owner")
    replies = say(db, ai, "Talk to the owner", payload=TALK_OWNER)
    assert texts(replies) == [t("en", "owner_waiting")]
    assert len(db.inbox) == 1


def test_tapping_a_bot_button_brings_the_bot_back():
    db, ai = FakeDB(), FakeAI()
    say(db, ai, "talk to the owner")
    replies = say(db, ai, "See menu", payload="MENU")
    assert texts(replies)[0] == t("en", "owner_back")
    assert replies[1].products  # the menu was answered too
    assert db.inbox[0]["status"] == "resolved" and db.inbox[0]["resolved_by"] == "customer"
    assert say(db, FakeAI(AIResult(intent=Intent.GREETING)), "hi")[0].text != t("en", "owner_back")  # told only once


def test_owner_done_in_dashboard_lets_the_bot_answer_with_a_notice_once(monkeypatch):
    db = FakeDB()
    say(db, FakeAI(), "talk to the owner")
    app.dependency_overrides[admin.get_db] = lambda: db
    try:
        res = TestClient(app).patch("/admin/api/inbox/1")
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 200 and db.inbox[0]["resolved_by"] == "owner"
    first = say(db, FakeAI(AIResult(intent=Intent.GREETING)), "hi")
    assert texts(first)[0] == t("en", "owner_back") and len(first) == 2
    second = say(db, FakeAI(AIResult(intent=Intent.GREETING)), "hi again")
    assert texts(second)[0] != t("en", "owner_back")


def test_handoff_expires_after_the_timeout():
    db = FakeDB()
    say(db, FakeAI(), "talk to the owner")
    db.inbox[0]["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=owner_inbox.HANDOFF_HOURS + 1)).isoformat()
    replies = say(db, FakeAI(AIResult(intent=Intent.GREETING)), "hi")
    assert texts(replies)[0] == t("en", "owner_back")
    assert db.inbox[0]["status"] == "resolved" and db.inbox[0]["resolved_by"] == "timeout"


def test_an_order_in_progress_survives_a_handoff():
    db = FakeDB()
    db.conversations[USER] = {"messenger_id": USER, "state": COLLECTING,
                              "draft": {"cart": [{"product": "Leche Flan", "quantity": 2}], "lang": "en", "asking": "add_more"}}
    say(db, FakeAI(), "talk to the owner")
    state = db.conversations[USER]
    assert state["state"] == COLLECTING and state["draft"]["cart"][0]["quantity"] == 2


def test_missing_table_tells_the_customer_instead_of_pretending():
    db = FakeDB(broken_tables={"owner_inbox"})
    replies = say(db, FakeAI(), "talk to the owner")
    assert texts(replies) == [t("en", "owner_unavailable")]


def test_bot_keeps_working_if_the_inbox_cannot_be_read():
    db = FakeDB(broken_tables={"owner_inbox"})
    replies = say(db, FakeAI(AIResult(intent=Intent.GREETING)), "hi")
    assert texts(replies) == [t("en", "greeting")]


def test_second_miss_offers_the_owner_and_questions_are_saved():
    db = FakeDB()
    ai = FakeAI(AIResult(intent=Intent.UNKNOWN), AIResult(intent=Intent.UNKNOWN))
    first = say(db, ai, "asdf qwer")
    assert TALK_OWNER not in [p for _, p in first[0].options]
    second = say(db, ai, "zxcv")
    assert TALK_OWNER in [p for _, p in second[0].options]
    assert [(r["kind"], r["message"]) for r in db.inbox] == [("unanswered", "asdf qwer"), ("unanswered", "zxcv")]


def test_ai_outage_is_not_saved_as_an_unanswered_question():
    db = FakeDB()
    say(db, FakeAI(AIUnavailableError()), "magkano ang cake")
    assert db.inbox == []


def test_dashboard_lists_the_inbox_with_customer_names():
    db = FakeDB()
    say(db, FakeAI(), "talk to the owner")
    db.tables["customers"] = [{"messenger_id": USER, "name": "Maria"}]
    app.dependency_overrides[admin.get_db] = lambda: db
    try:
        rows = TestClient(app).get("/admin/api/inbox").json()
    finally:
        app.dependency_overrides.clear()
    assert rows[0]["kind"] == "handoff" and rows[0]["customer"] == "Maria" and rows[0]["status"] == "open"


def test_resolving_an_unknown_item_is_a_404():
    app.dependency_overrides[admin.get_db] = lambda: FakeDB()
    try:
        assert TestClient(app).patch("/admin/api/inbox/99").status_code == 404
    finally:
        app.dependency_overrides.clear()
