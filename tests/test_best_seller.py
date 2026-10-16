"""The best-seller answer counted from real orders, and the test-data scripts in supabase/."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import order_service
from app.services.ai_service import AIResult, Intent
from app.services.conversation_service import BEST_SELLER_MIN_SOLD, ConversationService
from app.services.faq import FAQS
from app.services.messages import t

USER = "1001"
NOW = datetime.now(timezone.utc)
PRODUCTS = [
    {"product_id": 1, "name": "Yema Cake", "description": None, "price": 400, "is_available": True, "stock": None},
    {"product_id": 2, "name": "Mango Float", "description": None, "price": 400, "is_available": True, "stock": None},
    {"product_id": 3, "name": "Leche Flan", "description": None, "price": 400, "is_available": True, "stock": None},
    {"product_id": 4, "name": "Nutella Ferrero Cake", "description": None, "price": 400, "is_available": False, "stock": 0},
]


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
    def neq(self, c, v): self.filters.append((c, lambda x: x != v)); return self
    def gte(self, c, v): self.filters.append((c, lambda x: x is not None and x >= v)); return self
    def order(self, *a, **k): return self
    def limit(self, n): self.max = n; return self
    def insert(self, row): self.op, self.payload = "insert", row; return self
    def upsert(self, row, on_conflict=None): self.op, self.payload, self.conflict = "upsert", row, on_conflict; return self

    def execute(self):
        if self.db.broken == self.name:
            raise RuntimeError("database down")
        rows = self.db.tables.setdefault(self.name, [])
        if self.op == "insert":
            rows.append(dict(self.payload))
            return _Result([self.payload])
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
    def __init__(self, broken=None, **tables):
        self.tables, self.broken = {k: [dict(r) for r in v] for k, v in tables.items()}, broken

    def table(self, name):
        return _Query(self, name)


class ScriptedAI:
    def __init__(self, result):
        self.result = result

    def interpret_message(self, message, state, names, topics=None, context=None):
        return self.result


def order(days_ago, status, *items):
    return {"order_id": id(items) % 100000, "status": status, "order_date": (NOW - timedelta(days=days_ago)).isoformat(),
            "order_items": [{"quantity": q, "products": {"name": n}} for n, q in items]}


def sold(*orders):
    return FakeDB(products=PRODUCTS, orders=list(orders))


def ask(database, lang="en"):
    result = AIResult(intent=Intent.FAQ_QUESTION, faq_topic="best_seller", language=lang)
    return ConversationService(ScriptedAI(result), database).handle(USER, "best seller?", None)[0]


# ---------- counting ----------
def test_cakes_are_ranked_by_quantity_ordered():
    database = sold(order(1, "completed", ("Yema Cake", 5), ("Mango Float", 2)), order(2, "completed", ("Mango Float", 4), ("Leche Flan", 1)))
    ranked, total = order_service.best_sellers(database)
    assert ranked == [("Mango Float", 6), ("Yema Cake", 5), ("Leche Flan", 1)] and total == 12


def test_cancelled_orders_and_orders_outside_the_window_do_not_count():
    database = sold(order(1, "completed", ("Yema Cake", 3)), order(1, "cancelled", ("Leche Flan", 9)), order(45, "completed", ("Mango Float", 9)))
    assert order_service.best_sellers(database, days=30) == ([("Yema Cake", 3)], 3)


def test_equal_quantities_are_listed_alphabetically():
    database = sold(order(1, "completed", ("Yema Cake", 2), ("Mango Float", 2)))
    assert [name for name, _ in order_service.best_sellers(database)[0]] == ["Mango Float", "Yema Cake"]


def test_no_orders_gives_an_empty_ranking():
    assert order_service.best_sellers(FakeDB(orders=[])) == ([], 0)


# ---------- the answer ----------
def enough():
    return sold(order(1, "completed", ("Yema Cake", 8), ("Mango Float", 4)), order(3, "completed", ("Leche Flan", 3), ("Yema Cake", 2)))


def test_the_best_seller_is_named_with_its_count_and_an_order_button():
    reply = ask(enough())
    assert "Yema Cake" in reply.text and "10 sold in the last 30 days" in reply.text
    assert "Also popular: Mango Float, Leche Flan." in reply.text
    assert reply.options[0][1] == "ORDER:1"


def test_too_few_orders_and_the_faq_text_answers_instead():
    reply = ask(sold(order(1, "completed", ("Yema Cake", BEST_SELLER_MIN_SOLD - 1))))
    assert reply.text.startswith(FAQS["best_seller"]["en"]) and "ORDER:1" not in [p for _, p in reply.options]


def test_a_sold_out_cake_is_never_named():
    reply = ask(sold(order(1, "completed", ("Nutella Ferrero Cake", 20), ("Mango Float", 6), ("Leche Flan", 5))))
    assert "Nutella" not in reply.text and "Mango Float" in reply.text


def test_when_no_ranked_cake_can_be_ordered_the_faq_text_answers():
    assert ask(sold(order(1, "completed", ("Nutella Ferrero Cake", 20)))).text.startswith(FAQS["best_seller"]["en"])


def test_a_database_error_falls_back_to_the_faq_text():
    database = FakeDB(broken="orders", products=PRODUCTS)
    assert ask(database).text.startswith(FAQS["best_seller"]["en"])


@pytest.mark.parametrize("lang", ["tl", "hil"])
def test_the_answer_is_written_in_the_customers_language(lang):
    reply = ask(enough(), lang)
    assert reply.text.startswith(t(lang, "best_seller_top", name="Yema Cake", n=10, days=30))


def test_the_topic_has_text_in_every_language():
    assert all(FAQS["best_seller"].get(lang) for lang in ("en", "tl", "hil"))


# ---------- the test-data scripts ----------
SQL = Path(__file__).resolve().parent.parent / "supabase"


def seed_rows():
    text = (SQL / "seed_test_orders.sql").read_text(encoding="utf-8")
    return json.loads(re.search(r"jsonb_to_recordset\('(\[.*?\])'::jsonb\)", text, re.S).group(1))


def test_the_seed_makes_yema_cake_the_clear_best_seller():
    orders = []
    for i, r in enumerate(seed_rows()):
        items = [(r["p1"], r["q1"])] + ([(r["p2"], r["q2"])] if r["p2"] else [])
        orders.append(order(r["d"], r["s"], *items) | {"order_id": i})
    ranked, total = order_service.best_sellers(FakeDB(orders=orders))
    assert [name for name, _ in ranked] == ["Yema Cake", "Mango Float", "Leche Flan", "Nutella Ferrero Cake", "Chocolate Moist Cake"]
    assert total >= BEST_SELLER_MIN_SOLD and ranked[0][1] > ranked[1][1]


def test_the_seed_covers_every_status_and_both_ways_of_getting_the_cake():
    rows = seed_rows()
    assert {r["s"] for r in rows} == {"pending", "confirmed", "preparing", "ready", "out_for_delivery", "completed", "cancelled"}
    assert {r["f"] for r in rows} == {"pickup", "delivery"} and {r["c"] for r in rows} <= set(range(1, 9))


def test_the_seed_and_the_cleanup_only_touch_rows_marked_seed():
    seed = (SQL / "seed_test_orders.sql").read_text(encoding="utf-8")
    clean = (SQL / "remove_test_orders.sql").read_text(encoding="utf-8")
    assert "Test orders are already there" in seed  # a second run stops instead of doubling the data
    assert "seed-" in seed and clean.count("like 'seed-%'") >= 4
    for line in clean.splitlines():
        if line.strip().startswith("delete from"):
            assert "seed-%" in line or "like 'seed-%'" in line or "select customer_id from public.customers where messenger_id like 'seed-%'" in line
