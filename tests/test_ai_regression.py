"""The regression runner (evals/ai_regression.py) and its starter message file. No network: the AI is faked."""
import json

import pytest

from app.services.ai_service import AIResult, AIUnavailableError, Intent, QuoteItem
from app.services.faq import FAQS
from evals import ai_regression as reg

LANGS = {"en", "tl", "hil"}


# ---------- the starter file ----------
def test_starter_file_is_well_formed():
    menu, cases = reg.load_cases()
    assert len(cases) >= 45
    texts = [c["text"] for c in cases]
    assert len(texts) == len(set(texts)), "duplicate message"
    valid_intents = {i.value for i in Intent}
    for c in cases:
        exp = c["expect"]
        assert exp["language"] in LANGS and exp["intent"] in valid_intents, c
        if exp["intent"] == "faq_question":
            assert exp["faq_topic"] in FAQS, c
        for item in exp.get("items", []):
            assert item["product"] in menu, c
        if "product" in exp:
            assert exp["product"] in menu, c


@pytest.mark.parametrize("lang", sorted(LANGS))
def test_each_language_has_enough_messages(lang):
    _, cases = reg.load_cases()
    assert sum(c["expect"]["language"] == lang for c in cases) >= 15


def test_the_price_scenarios_are_covered_in_every_language():
    _, cases = reg.load_cases()
    for lang in LANGS:
        quotes = [c["expect"] for c in cases if c["expect"]["language"] == lang and c["expect"]["intent"] == "price_question"]
        assert any(len(q["items"]) == 2 for q in quotes), lang  # two different cakes
        assert any(q.get("place") for q in quotes), lang  # delivered to a place
        assert any(q["items"][0]["quantity"] is None for q in quotes), lang  # no quantity


# ---------- comparing a reading with the expected one ----------
def result(**fields):
    return AIResult(intent=fields.pop("intent", Intent.GREETING), **fields)


def test_a_matching_reading_has_no_differences():
    got = result(intent=Intent.PRICE_QUESTION, language="en", place="molo", items=[QuoteItem(product="yema cake", quantity=3)])
    expected = {"language": "en", "intent": "price_question", "place": "Molo", "items": [{"product": "Yema Cake", "quantity": 3}]}
    assert reg.compare(got, expected) == []


def test_each_wrong_field_is_reported():
    got = result(intent=Intent.FAQ_QUESTION, language="tl", faq_topic="hours")
    wrong = reg.compare(got, {"language": "hil", "intent": "faq_question", "faq_topic": "delivery"})
    assert [(f, w, g) for f, w, g in wrong] == [("language", "hil", "tl"), ("faq_topic", "delivery", "hours")]


def test_items_are_compared_regardless_of_order_and_quantity_matters():
    got = result(intent=Intent.PRICE_QUESTION, items=[QuoteItem(product="Mango Float", quantity=1), QuoteItem(product="Yema Cake", quantity=3)])
    same = {"items": [{"product": "Yema Cake", "quantity": 3}, {"product": "Mango Float", "quantity": 1}]}
    other = {"items": [{"product": "Yema Cake", "quantity": 2}, {"product": "Mango Float", "quantity": 1}]}
    assert reg.compare(got, same) == [] and [f for f, _, _ in reg.compare(got, other)] == ["items"]


def test_fields_that_are_not_listed_are_not_checked():
    assert reg.compare(result(language="hil", place="Jaro"), {"intent": "greeting"}) == []


def test_an_expected_null_must_really_be_null():
    assert [f for f, _, _ in reg.compare(result(place="Jaro"), {"place": None})] == ["place"]


# ---------- running and reporting ----------
class ScriptedAI:
    """Answers every message with the expected reading, except where told otherwise."""

    def __init__(self, cases, breakages=None):
        self.cases, self.breakages, self.calls = {c["text"]: c["expect"] for c in cases}, breakages or {}, []

    def interpret_message(self, text, state, menu, topics):
        self.calls.append((text, state, menu, topics))
        if text in self.breakages:
            if self.breakages[text] == "down":
                raise AIUnavailableError
            return self.breakages[text]
        e = self.cases[text]
        return AIResult(intent=Intent(e["intent"]), language=e["language"], faq_topic=e.get("faq_topic"), place=e.get("place"),
                        product=e.get("product"), items=[QuoteItem(**i) for i in e["items"]] if "items" in e else None)


def test_a_perfect_ai_scores_100_percent():
    menu, cases = reg.load_cases()
    ai = ScriptedAI(cases)
    outcomes = reg.run(ai, menu, {"hours": "opening hours"}, cases)
    assert reg.accuracy(outcomes) == 100.0 and len(ai.calls) == len(cases)
    assert all(state == "idle" and given_menu == menu for _, state, given_menu, _ in ai.calls)


def test_a_wrong_reading_and_an_ai_failure_are_listed_in_the_report():
    menu, cases = reg.load_cases()
    first, second = cases[0]["text"], cases[1]["text"]
    ai = ScriptedAI(cases, {first: AIResult(intent=Intent.THANKS, language="en"), second: "down"})
    outcomes = reg.run(ai, menu, {}, cases)
    assert reg.accuracy(outcomes) == pytest.approx(100 * (len(cases) - 2) / len(cases))
    text = reg.report(outcomes)
    assert first in text and "expected 'greeting', got 'thanks'" in text and second in text and "the AI call failed" in text
    assert "By language:" in text and "By field:" in text


def test_the_delay_is_applied_between_messages_only():
    menu, cases = reg.load_cases()
    waits = []
    reg.run(ScriptedAI(cases), menu, {}, cases[:4], delay=0.5, sleep=waits.append)
    assert waits == [0.5, 0.5, 0.5]


def test_main_returns_an_error_code_below_the_minimum(monkeypatch, tmp_path, capsys):
    menu, cases = reg.load_cases()
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"menu": menu, "cases": cases[:3]}), encoding="utf-8")
    broken = {cases[0]["text"]: AIResult(intent=Intent.THANKS, language="en")}
    models = []

    class FakeGemini:
        def __init__(self, model=None):
            models.append(model)
            self._ai = ScriptedAI(cases[:3], broken)

        def interpret_message(self, *args):
            return self._ai.interpret_message(*args)

    monkeypatch.setattr(reg, "GeminiAIService", FakeGemini)
    assert reg.main(["--file", str(path), "--model", "gemini-x"]) == 0  # no minimum asked for
    assert reg.main(["--file", str(path), "--min-accuracy", "90"]) == 1  # 2 of 3 right
    assert models == ["gemini-x", None]
    assert "Messages read completely right" in capsys.readouterr().out


def test_when_every_call_fails_the_report_suggests_checking_the_key(monkeypatch, tmp_path, capsys):
    menu, cases = reg.load_cases()
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"menu": menu, "cases": cases[:2]}), encoding="utf-8")

    class Down:
        def __init__(self, model=None):
            pass

        def interpret_message(self, *args):
            raise AIUnavailableError

    monkeypatch.setattr(reg, "GeminiAIService", Down)
    assert reg.main(["--file", str(path), "--min-accuracy", "1"]) == 1
    assert "GEMINI_API_KEY" in capsys.readouterr().out
