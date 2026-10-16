"""Guards against a looping model, repeated webhooks and Hiligaynon being read as Tagalog."""
import threading
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.services import ai_service
from app.services.ai_service import AI_MAX_ITEMS, AIResult, AIUnavailableError, GeminiAIService, Intent, QuoteItem
from app.services.conversation_service import ConversationService, _claim, _pick_language, _release

PRODUCTS = [
    {"product_id": 3, "name": "Yema Cake", "price": 400, "is_available": True},
    {"product_id": 1, "name": "Mango Float", "price": 400, "is_available": True},
]


def _items(*pairs):
    return [SimpleNamespace(product=name, quantity=qty) for name, qty in pairs]


# ---- repeated items from a looping model ----
def test_identical_repeats_are_not_summed():
    _, merged, _ = ConversationService._resolve_items(_items(("Yema Cake", 3), ("Yema Cake", 3), ("Yema Cake", 3)), PRODUCTS)
    assert merged == {3: 3}


def test_different_amounts_of_the_same_cake_still_add_up():
    _, merged, _ = ConversationService._resolve_items(_items(("Yema Cake", 2), ("Yema Cake", 3)), PRODUCTS)
    assert merged == {3: 5}


def test_two_different_cakes_are_kept():
    chosen, merged, _ = ConversationService._resolve_items(_items(("Yema Cake", 3), ("Mango Float", 1)), PRODUCTS)
    assert [c["name"] for c in chosen] == ["Yema Cake", "Mango Float"] and merged == {3: 3, 1: 1}


def test_schema_rejects_a_runaway_item_list():
    with pytest.raises(ValidationError):
        AIResult(intent=Intent.PRICE_QUESTION, items=[QuoteItem(product="Yema Cake", quantity=3)] * (AI_MAX_ITEMS + 1))


# ---- language ----
@pytest.mark.parametrize("text", ["pwede mag-order para buwas?", "may ube cake kamo?"])
def test_hiligaynon_clues_override_a_tagalog_guess(text):
    assert _pick_language("tl", text) == "hil"


def test_ai_language_is_kept_when_there_are_no_hiligaynon_clues():
    assert _pick_language("tl", "magkano po ang 3 yema cake?") == "tl"
    assert _pick_language("en", "how much for 3 yema cakes?") == "en"


def test_missing_language_falls_back_to_clue_words():
    assert _pick_language(None, "magkano po ang 3 yema cake?") == "tl"
    assert _pick_language(None, "I want to order 2 leche flan") == "en"
    assert _pick_language(None, "gusto ko pong umorder ng 2 leche flan") == "tl"
    assert _pick_language(None, "1 of each") == "en"
    assert _pick_language(None, "tig-isa po") == "tl"
    assert _pick_language(None, "12345") is None  # no signal: the conversation's language is kept


# ---- Meta retries ----
def test_a_message_being_processed_is_claimed_once():
    assert _claim("u1", "mid-x") is True
    assert _claim("u1", "mid-x") is False  # the retry arrives while the first is still running
    _release("u1", "mid-x")
    assert _claim("u1", "mid-x") is True
    _release("u1", "mid-x")


def test_concurrent_retries_get_one_winner():
    wins, barrier = [], threading.Barrier(8)

    def go():
        barrier.wait()
        wins.append(_claim("u2", "mid-y"))

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    _release("u2", "mid-y")
    assert wins.count(True) == 1


# ---- AI call: retry on bad JSON, token cap ----
class _FakeModels:
    def __init__(self, texts):
        self.texts, self.calls, self.configs = list(texts), 0, []

    def generate_content(self, model, contents, config):
        self.calls += 1
        self.configs.append(config)
        return SimpleNamespace(text=self.texts.pop(0))


def _service(texts):
    svc = GeminiAIService(model="m")
    models = _FakeModels(texts)
    svc._client = SimpleNamespace(models=models)
    return svc, models


def test_bad_json_is_retried_once_then_succeeds():
    svc, models = _service(['{"intent": "price_qu', '{"intent": "greeting"}'])
    assert svc.interpret_message("hi", "idle", ["Yema Cake"]).intent == Intent.GREETING
    assert models.calls == 2


def test_bad_json_twice_raises_unavailable():
    svc, models = _service(['{"intent": "price_qu', '{"intent": "price_qu'])
    with pytest.raises(AIUnavailableError):
        svc.interpret_message("pila ang 3 yema cake?", "idle", ["Yema Cake"])
    assert models.calls == ai_service.AI_PARSE_ATTEMPTS


def test_output_is_capped():
    svc, models = _service(['{"intent": "greeting"}'])
    svc.interpret_message("hi", "idle", ["Yema Cake"])
    assert models.configs[0].max_output_tokens == ai_service.AI_MAX_OUTPUT_TOKENS


# ---- delivery place found by us when the AI leaves it empty ----
def _quote_db():
    from tests.test_quotes import AREAS, PRODUCTS, FakeDB
    return FakeDB(products=PRODUCTS, delivery_areas=AREAS)


class _NoPlaceAI:
    """Behaves like the real model on the report: right cakes, but `place` left empty."""

    def __init__(self, intent=Intent.PRICE_QUESTION, **extra):
        self.result = AIResult(intent=intent, items=[QuoteItem(product="Yema Cake", quantity=3)], **extra)

    def interpret_message(self, message, state, names, topics=None, context=None):
        return self.result


@pytest.mark.parametrize("text", [
    "how much for 3 yema cakes delivered to Molo?",
    "magkano po ang 3 yema cake kung i-deliver sa Molo?",
    "pila ang 3 yema cake kon ideliver sa Molo?",
])
def test_quote_gets_the_delivery_fee_even_when_the_ai_forgot_the_place(text, monkeypatch):
    from app.services import shop_hours
    from app.services.shop_hours import ShopStatus
    monkeypatch.setattr(shop_hours, "current_status", lambda client, now=None: ShopStatus())
    reply = ConversationService(_NoPlaceAI(language="en"), _quote_db()).handle("1001", text, None)[0]
    assert "Delivery to Molo: ₱70.00" in reply.text and "Total: ₱1,270.00" in reply.text


def test_no_area_in_the_message_still_asks_for_the_area(monkeypatch):
    from app.services import shop_hours
    from app.services.shop_hours import ShopStatus
    monkeypatch.setattr(shop_hours, "current_status", lambda client, now=None: ShopStatus())
    reply = ConversationService(_NoPlaceAI(language="en"), _quote_db()).handle("1001", "how much for 3 yema cakes?", None)[0]
    assert "Delivery to" not in reply.text and "depends on your area" in reply.text


def test_place_from_text_matches_only_listed_areas():
    from app.services.conversation_service import _place_from_text
    areas = [{"name": "Molo"}, {"name": "Jaro"}]
    assert _place_from_text("deliver sa molo?", areas) == "Molo"
    assert _place_from_text("how much is the yema cake?", areas) is None
    assert _place_from_text("deliver sa Molo", []) is None


# ---- the eval scores what the bot really does ----
def test_eval_collapses_repeats_but_keeps_the_raw_number():
    from evals import ai_regression as reg

    class Repeater:
        def interpret_message(self, message, state, names, topics=None, context=None):
            return AIResult(intent=Intent.PRICE_QUESTION, language="en",
                            items=[QuoteItem(product="Yema Cake", quantity=3)] * 5)

    case = {"text": "how much for 3 yema cakes delivered to Molo?",
            "expect": {"language": "en", "intent": "price_question", "place": "Molo",
                       "items": [{"product": "Yema Cake", "quantity": 3}]}}
    out = reg.run(Repeater(), [], {}, [case], areas=[{"name": "Molo"}])
    assert out[0]["wrong"] == []  # repeats collapsed, place found from the text
    text = reg.report(out)
    assert "items from the AI alone (before the bot's own fallbacks): 0% (0/1)" in text
    assert "place from the AI alone (before the bot's own fallbacks): 0% (0/1)" in text
