"""Ordering several cakes in one sentence (confirm first), "how many of each?", the language fallback, the note shown
once a day, the corrected cancel wording and the AI retry settings."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import ai_service, shop_hours
from app.services.ai_service import AIResult, Intent, QuoteItem
from app.services.conversation_service import (COLLECTING, IDLE, ConversationService, _guess_language, ask_context)
from app.services.messages import MESSAGES, t
from app.services.shop_hours import ShopStatus

USER = "1001"
PRODUCTS = [
    {"product_id": 1, "name": "Nutella Ferrero Cake", "description": None, "price": 400, "is_available": True, "stock": None},
    {"product_id": 2, "name": "Leche Flan", "description": None, "price": 350, "is_available": True, "stock": 3},
    {"product_id": 3, "name": "Mango Float", "description": None, "price": 400, "is_available": True, "stock": None},
    {"product_id": 4, "name": "Yema Cake", "description": None, "price": 400, "is_available": True, "stock": None},
    {"product_id": 5, "name": "Chocolate Moist Cake", "description": None, "price": 450, "is_available": False, "stock": 0},
]
AREAS = [{"id": 1, "name": "Jaro", "fee": 50, "is_active": True, "is_other": False}]


# ---------- a small in-memory database ----------
class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, name):
        self.db, self.name, self.op, self.payload, self.conflict = db, name, "select", None, None
        self.filters, self.max = [], None

    def select(self, *a, **k): return self
    def eq(self, c, v): self.filters.append((c, lambda x: x == v)); return self
    def gte(self, c, v): self.filters.append((c, lambda x: x is not None and x >= v)); return self
    def order(self, *a, **k): return self
    def limit(self, n): self.max = n; return self
    def insert(self, row): self.op, self.payload = "insert", row; return self
    def upsert(self, row, on_conflict=None): self.op, self.payload, self.conflict = "upsert", row, on_conflict; return self

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
        return _Result(matched[: self.max] if self.max else matched)


class FakeDB:
    def __init__(self, **tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}

    def table(self, name):
        return _Query(self, name)

    def rpc(self, fn, params):
        raise AssertionError("none of these tests may create an order")


class ScriptedAI:
    """Returns the queued results in order and remembers the context it was given with each message."""

    def __init__(self, *results):
        self.results, self.contexts = list(results), []

    def interpret_message(self, message, state, names, topics=None, context=None):
        self.contexts.append(context)
        return self.results.pop(0)


@pytest.fixture(autouse=True)
def shop_open(monkeypatch):
    monkeypatch.setattr(shop_hours, "current_status", lambda client, now=None: ShopStatus())


def db(**tables):
    return FakeDB(products=PRODUCTS, **tables)


def say(database, ai, text="", payload=None):
    return ConversationService(ai, database).handle(USER, text, None, payload)


def order(*items, lang="en", intent=Intent.START_ORDER, place=None):
    return AIResult(intent=intent, language=lang, place=place, product=items[0][0], quantity=items[0][1],
                    items=[QuoteItem(product=n, quantity=q) for n, q in items])


def payloads(reply):
    return [p for _, p in reply.options]


def draft_of(database):
    return database.tables["conversations"][0]["draft"]


# ---------- several cakes in one ordering sentence: repeat back, wait for Yes ----------
def test_two_cakes_in_one_sentence_are_repeated_back_and_nothing_is_added_yet():
    database = db()
    reply = say(database, ScriptedAI(order(("Nutella Ferrero Cake", 2), ("Leche Flan", 2))), "I want 2 nutella and 2 leche flan")[0]
    assert "2 × Nutella Ferrero Cake — ₱800.00" in reply.text and "2 × Leche Flan — ₱700.00" in reply.text and "Is that right?" in reply.text
    assert payloads(reply) == ["ITEMS_YES", "ITEMS_NO"]
    row = database.tables["conversations"][0]
    assert row["state"] == IDLE and row["draft"]["cart"] == []  # the cart stays empty until the customer says Yes
    assert row["draft"]["proposed"] == [[1, 2], [2, 2]]


def test_yes_puts_both_cakes_in_the_cart_and_the_order_carries_on():
    database = db()
    say(database, ScriptedAI(order(("Nutella Ferrero Cake", 2), ("Leche Flan", 2))), "I want 2 nutella and 2 leche flan")
    reply = say(database, ScriptedAI(), payload="ITEMS_YES")[0]
    row = database.tables["conversations"][0]
    assert row["state"] == COLLECTING
    assert sorted((i["product"], i["quantity"]) for i in row["draft"]["cart"]) == [("Leche Flan", 2), ("Nutella Ferrero Cake", 2)]
    assert payloads(reply) == ["FULFILL:delivery", "FULFILL:pickup"]  # no "add more?" detour: the customer already listed everything


def test_no_adds_nothing():
    database = db()
    say(database, ScriptedAI(order(("Nutella Ferrero Cake", 2), ("Leche Flan", 1))), "2 nutella and 1 leche flan")
    reply = say(database, ScriptedAI(), payload="ITEMS_NO")[0]
    assert reply.text == t("en", "items_no") and database.tables["conversations"][0]["draft"]["cart"] == []


def test_yes_with_no_list_waiting_says_so_and_adds_nothing():
    database = db()
    reply = say(database, ScriptedAI(), payload="ITEMS_YES")[0]
    assert reply.text == t("en", "items_lost") and database.tables.get("conversations", [{"draft": {"cart": []}}])[0]["draft"]["cart"] == []


def test_the_list_is_forgotten_if_the_customer_goes_on_to_something_else():
    database = db()
    say(database, ScriptedAI(order(("Nutella Ferrero Cake", 2), ("Leche Flan", 1))), "2 nutella and 1 leche flan")
    say(database, ScriptedAI(AIResult(intent=Intent.GREETING, language="en")), "hello")
    assert say(database, ScriptedAI(), payload="ITEMS_YES")[0].text == t("en", "items_lost")


def test_stock_is_checked_before_asking_yes():
    reply = say(db(), ScriptedAI(order(("Nutella Ferrero Cake", 1), ("Leche Flan", 9))), "1 nutella and 9 leche flan")[0]
    assert "only have 3 Leche Flan left" in reply.text and payloads(reply) != ["ITEMS_YES", "ITEMS_NO"]


def test_a_sold_out_or_unknown_cake_is_named_and_nothing_is_proposed():
    reply = say(db(), ScriptedAI(order(("Nutella Ferrero Cake", 1), ("Chocolate Moist Cake", 1), ("Ube Cake", 1))), "nutella, choco and ube")[0]
    assert "Chocolate Moist Cake" in reply.text and "couldn't find Ube Cake" in reply.text and "ITEMS_YES" not in payloads(reply)


def test_one_cake_still_goes_through_the_normal_flow():
    database = db()
    reply = say(database, ScriptedAI(order(("Nutella Ferrero Cake", 2))), "I want 2 nutella")[0]
    assert "ITEMS_YES" not in payloads(reply) and database.tables["conversations"][0]["state"] == COLLECTING
    assert draft_of(database)["cart"] == [{"product": "Nutella Ferrero Cake", "quantity": 2}]


# ---------- "how many of each?" ----------
def test_missing_quantities_are_asked_and_the_answer_is_read_with_that_context():
    database = db()
    ask = say(database, ScriptedAI(order(("Mango Float", None), ("Yema Cake", None))), "I want mango float and yema cake")[0]
    assert "How many of each" in ask.text
    assert draft_of(database)["ask_items"]["names"] == ["Mango Float", "Yema Cake"]
    ai = ScriptedAI(order(("Mango Float", 1), ("Yema Cake", 1), intent=Intent.PROVIDE_ORDER_DETAILS))
    reply = say(database, ai, "1 of each")[0]
    assert ai.contexts == [ask_context(["Mango Float", "Yema Cake"])]
    assert "1 × Mango Float" in reply.text and "1 × Yema Cake" in reply.text and payloads(reply) == ["ITEMS_YES", "ITEMS_NO"]


def test_answering_a_quote_question_gives_the_total_and_an_order_button():
    database = db(delivery_areas=AREAS)
    first = say(database, ScriptedAI(order(("Mango Float", None), ("Yema Cake", None), intent=Intent.PRICE_QUESTION)), "how much is mango float and yema cake?")[0]
    assert "How many would you like" in first.text
    reply = say(database, ScriptedAI(order(("Mango Float", 1), ("Yema Cake", 1), intent=Intent.PRICE_QUESTION)), "id like 1 of each")[0]
    assert "Cakes: ₱800.00" in reply.text and "ORDER_QUOTE" in payloads(reply)


def test_the_question_is_forgotten_after_one_message():
    database = db()
    say(database, ScriptedAI(order(("Mango Float", None), ("Yema Cake", None))), "mango float and yema cake")
    say(database, ScriptedAI(AIResult(intent=Intent.GREETING, language="en")), "hello")
    ai = ScriptedAI(AIResult(intent=Intent.GREETING, language="en"))
    say(database, ai, "hi again")
    assert ai.contexts == [None]


def test_order_these_adds_the_quoted_cakes_and_the_order_carries_on():
    database = db(delivery_areas=AREAS)
    say(database, ScriptedAI(order(("Mango Float", 1), ("Yema Cake", 2), intent=Intent.PRICE_QUESTION)), "price for 1 mango float and 2 yema?")
    say(database, ScriptedAI(), payload="ORDER_QUOTE")
    row = database.tables["conversations"][0]
    assert row["state"] == COLLECTING
    assert sorted((i["product"], i["quantity"]) for i in row["draft"]["cart"]) == [("Mango Float", 1), ("Yema Cake", 2)]


def test_the_ai_is_not_given_context_for_button_taps_or_ordinary_messages():
    ai = ScriptedAI(AIResult(intent=Intent.GREETING, language="en"))
    say(db(), ai, "hello")
    assert ai.contexts == [None]


# ---------- language fallback ----------
@pytest.mark.parametrize("text, expected", [
    ("pila ang 3 yema cake?", "hil"), ("pila ang 2 leche flan kag 1 mango float?", "hil"), ("bukas kmo subong?", "hil"),
    ("nagadeliver kamo sa Jaro?", "hil"), ("magkano ang 2 leche flan at 1 mango float?", "tl"), ("bukas po ba kayo ngayon?", "tl"),
    ("how much is the yema cake?", "en"), ("how much for 3 yema cakes?", "en"), ("do you deliver?", "en"),
    ("ok", None), ("123", None), ("🙂", None), ("pwede po makita ang menu kamo", None),
])
def test_language_clues(text, expected):
    assert _guess_language(text) == expected


def test_when_the_ai_cannot_tell_the_language_the_clue_words_decide():
    for text, lang in (("pila ang 3 yema cake?", "hil"), ("magkano po ang yema cake?", "tl"), ("how much is the yema cake?", "en")):
        reply = say(db(), ScriptedAI(AIResult(intent=Intent.GREETING, language=None)), text)[0]
        assert reply.text.startswith(t(lang, "greeting")), (text, lang)


def test_an_unclear_message_keeps_the_current_language():
    database = db()
    say(database, ScriptedAI(AIResult(intent=Intent.GREETING, language="hil")), "kamusta kamo")
    reply = say(database, ScriptedAI(AIResult(intent=Intent.GREETING, language=None)), "ok")[0]
    assert reply.text.startswith(t("hil", "greeting"))


# ---------- the automated-assistant note: once a day ----------
def faq(topic):
    return AIResult(intent=Intent.FAQ_QUESTION, faq_topic=topic, language="en")


def test_the_note_is_shown_once_not_on_every_answer():
    database = db()
    note = t("en", "ai_disclaimer")
    assert note in say(database, ScriptedAI(faq("payment")), "how to pay?")[0].text
    assert note not in say(database, ScriptedAI(faq("payment")), "how to pay?")[0].text
    assert note not in say(database, ScriptedAI(faq("hours")), "are you open?")[0].text


def test_the_note_comes_back_after_a_day():
    database = db()
    say(database, ScriptedAI(faq("payment")), "how to pay?")
    database.tables["conversations"][0]["draft"]["noted_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    assert t("en", "ai_disclaimer") in say(database, ScriptedAI(faq("payment")), "how to pay?")[0].text


def test_the_note_is_added_to_only_one_reply_in_a_turn():
    database = db()
    replies = say(database, ScriptedAI(faq("payment")), "how to pay?")
    assert sum(t("en", "ai_disclaimer") in r.text for r in replies) == 1


# ---------- wording ----------
def test_no_message_still_says_confirmed_orders_cannot_be_cancelled():
    for lang in ("en", "tl", "hil"):
        text = MESSAGES[lang]["summary"]
        assert "cancelled once" not in text and "kapag nakumpirma" not in text and "na-confirm na" not in text
        assert "pending" in text


def test_an_unknown_area_message_only_apologises_because_the_next_question_lists_the_areas():
    for lang in ("en", "tl", "hil"):
        assert "{areas}" not in MESSAGES[lang]["area_not_found"]


def test_the_order_received_reply_offers_my_orders(monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace
    from app.services import conversation_service as cs

    monkeypatch.setattr(cs.orders, "create_order", lambda request, client=None: SimpleNamespace(order_id=7, total_amount=Decimal("800"), status="pending", items=[SimpleNamespace(quantity=2, product_name="Nutella Ferrero Cake")]))
    draft = cs._new_draft("en") | {"cart": [{"product": "Nutella Ferrero Cake", "quantity": 2}], "customer_name": "Ana Cruz",
                                    "contact_number": "09171234567", "notes": "", "dedication": ""}
    draft = ConversationService._set_fulfillment(draft, "pickup")  # also fills in the shop address as the location
    replies, state, _ = ConversationService(ScriptedAI(), db())._place_order(USER, draft, PRODUCTS)
    assert "MY_ORDERS" in payloads(replies[0]) and "#7" in replies[0].text and state == IDLE


# ---------- the AI client: retries busy errors, sends no timeout header ----------
class Settings:
    gemini_model = "gemini-test"

    class gemini_api_key:
        @staticmethod
        def get_secret_value():
            return "dummy-key"


def service_behind_a_fake_server(monkeypatch, statuses):
    """A GeminiAIService whose HTTP traffic goes to a fake Google that answers with the queued status codes."""
    import json
    import time

    import httpx
    from google import genai

    sent = []

    def handler(request):
        sent.append(request)
        status = statuses.pop(0) if statuses else 200
        if status == 200:
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps({"intent": "greeting"})}]}}]})
        return httpx.Response(status, json={"error": {"code": status, "message": "fake error", "status": "FAKE"}})

    real_client = genai.Client

    def client_with_fake_server(*args, http_options=None, **kwargs):
        http_options.httpx_client = httpx.Client(transport=httpx.MockTransport(handler))
        return real_client(*args, http_options=http_options, **kwargs)

    monkeypatch.setattr(ai_service.genai, "Client", client_with_fake_server)
    monkeypatch.setattr(ai_service, "get_settings", lambda: Settings)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)  # do not really wait between retries
    return ai_service.GeminiAIService(), sent


def read(service):
    return service.interpret_message("hi", "idle", ["Yema Cake"], {})


def test_the_client_is_set_up_to_retry_and_has_no_timeout_option(monkeypatch):
    monkeypatch.setattr(ai_service, "get_settings", lambda: Settings)
    options = ai_service.GeminiAIService()._get_client()._api_client._http_options
    assert options.retry_options.attempts == ai_service.AI_ATTEMPTS == 3
    assert {429, 503} <= set(options.retry_options.http_status_codes)
    assert options.timeout is None  # a timeout makes the SDK send X-Server-Timeout, which Google rejected with a 400


def test_a_rate_limit_is_retried_and_the_second_try_is_used(monkeypatch):
    service, sent = service_behind_a_fake_server(monkeypatch, [429, 200])
    assert read(service).intent == Intent.GREETING and len(sent) == 2


def test_a_busy_model_is_retried_but_not_forever(monkeypatch):
    service, sent = service_behind_a_fake_server(monkeypatch, [503, 503, 503, 503])
    with pytest.raises(ai_service.AIUnavailableError):
        read(service)
    assert len(sent) == 3


def test_a_rejected_request_is_not_retried(monkeypatch):
    service, sent = service_behind_a_fake_server(monkeypatch, [400, 200])
    with pytest.raises(ai_service.AIUnavailableError):
        read(service)
    assert len(sent) == 1


def test_no_server_timeout_header_is_sent(monkeypatch):
    service, sent = service_behind_a_fake_server(monkeypatch, [200])
    read(service)
    assert "x-server-timeout" not in {name.lower() for name in sent[0].headers}
