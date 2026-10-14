"""Price quotes for one or several cakes, with or without delivery. The AI only says which cakes, how many and
where; every number comes from the database."""
from datetime import datetime, timezone

import pytest

from app.services import shop_hours
from app.services.ai_service import AIResult, Intent, QuoteItem
from app.services.conversation_service import COLLECTING, ConversationService, _new_draft
from app.services.messages import t
from app.services.shop_hours import ShopStatus

USER = "1001"
PRODUCTS = [
    {"product_id": 1, "name": "Yema Cake", "description": "Chiffon with yema frosting", "price": 400, "is_available": True, "stock": None},
    {"product_id": 2, "name": "Mango Float", "description": None, "price": 400, "is_available": True, "stock": 3},
    {"product_id": 3, "name": "Leche Flan", "description": None, "price": 350, "is_available": True, "stock": 0},  # sold out
    {"product_id": 4, "name": "Chocolate Moist Cake", "description": None, "price": 450.5, "is_available": True, "stock": 10},
]
AREAS = [
    {"id": 1, "name": "Jaro", "fee": 50, "is_active": True, "is_other": False},
    {"id": 2, "name": "Molo", "fee": 70, "is_active": True, "is_other": False},
]
OTHER_ON = {"id": 3, "name": "Other areas", "fee": None, "is_active": True, "is_other": True}


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
    def order(self, col, desc=False): self.sort, self.desc = col, desc; return self
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
        if self.sort:
            matched = sorted(matched, key=lambda r: r[self.sort], reverse=self.desc)
        return _Result(matched[: self.max] if self.max else matched)


class FakeDB:
    def __init__(self, **tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}

    def table(self, name):
        return _Query(self, name)

    def rpc(self, fn, params):
        raise AssertionError("a price quote must never create an order")


class FakeAI:
    def __init__(self, result=None):
        self.result = result

    def interpret_message(self, message, state, names, topics=None):
        return self.result


@pytest.fixture(autouse=True)
def shop_open(monkeypatch):
    monkeypatch.setattr(shop_hours, "current_status", lambda client, now=None: ShopStatus())


def db(areas=AREAS):
    return FakeDB(products=PRODUCTS, delivery_areas=areas)


def quote(items, place=None, lang="en", database=None):
    result = AIResult(intent=Intent.PRICE_QUESTION, product=items[0][0], quantity=items[0][1], place=place, language=lang,
                      items=[QuoteItem(product=n, quantity=q) for n, q in items])
    database = database or db()
    return ConversationService(FakeAI(result), database).handle(USER, "price?", None)[0], database


def payloads(reply):
    return [p for _, p in reply.options]


# ---------- the four scenarios ----------
def test_three_cakes_without_an_address_gives_the_cake_price_and_asks_for_the_area():
    reply, _ = quote([("Yema Cake", 3)])
    assert "3 × Yema Cake — ₱1,200.00" in reply.text and "Cakes: ₱1,200.00" in reply.text
    assert "Total:" not in reply.text and "depends on your area" in reply.text
    assert "QUOTE_AREA:1" in payloads(reply) and "QUOTE_AREA:2" in payloads(reply)


def test_three_cakes_to_molo_adds_the_delivery_fee():
    reply, _ = quote([("Yema Cake", 3)], place="Molo")
    assert "Delivery to Molo: ₱70.00" in reply.text and "Total: ₱1,270.00" in reply.text


def test_two_different_cakes_without_an_address():
    reply, _ = quote([("Yema Cake", 3), ("Mango Float", 1)])
    assert "3 × Yema Cake — ₱1,200.00" in reply.text and "1 × Mango Float — ₱400.00" in reply.text
    assert "Cakes: ₱1,600.00" in reply.text and "Total:" not in reply.text


def test_two_different_cakes_to_molo():
    reply, _ = quote([("Yema Cake", 3), ("Mango Float", 1)], place="Molo")
    assert "Total: ₱1,670.00" in reply.text


# ---------- the follow-up: tap an area under the quote ----------
def test_tapping_an_area_finishes_the_quote():
    reply, database = quote([("Yema Cake", 3), ("Mango Float", 1)])
    assert database.tables["conversations"][0]["draft"]["quote"] == [[1, 3], [2, 1]]
    tap = ConversationService(FakeAI(), database).handle(USER, "", None, "QUOTE_AREA:2")[0]
    assert "3 × Yema Cake" in tap.text and "Delivery to Molo: ₱70.00" in tap.text and "Total: ₱1,670.00" in tap.text


def test_area_tap_with_no_stored_quote_asks_again():
    tap = ConversationService(FakeAI(), db()).handle(USER, "", None, "QUOTE_AREA:2")[0]
    assert tap.text == t("en", "quote_lost")


def test_area_tap_rechecks_stock_at_that_moment():
    reply, database = quote([("Mango Float", 3)])
    next(p for p in database.tables["products"] if p["product_id"] == 2)["stock"] = 1  # someone else bought two
    tap = ConversationService(FakeAI(), database).handle(USER, "", None, "QUOTE_AREA:1")[0]
    assert "only have 1 Mango Float left" in tap.text and "Total:" not in tap.text


# ---------- delivery edge cases ----------
def test_unlisted_place_with_other_areas_on_leaves_the_fee_to_the_owner():
    reply, _ = quote([("Yema Cake", 2)], place="Pavia", database=db(AREAS + [OTHER_ON]))
    assert "Cakes: ₱800.00" in reply.text and "isn't on our delivery list" in reply.text and "Total:" not in reply.text


def test_unlisted_place_with_other_areas_off_is_refused_but_the_cake_price_stays():
    reply, _ = quote([("Yema Cake", 2)], place="Pavia")
    assert "Cakes: ₱800.00" in reply.text and "don't deliver to Pavia" in reply.text


def test_flat_fee_mode_quotes_the_flat_delivery_fee():
    reply, _ = quote([("Yema Cake", 3)], database=db(areas=[]))
    assert "Delivery adds ₱50.00" in reply.text and "₱1,250.00" in reply.text


# ---------- quantity and menu edge cases ----------
def test_quantity_above_stock_gives_no_total():
    reply, _ = quote([("Mango Float", 5)], place="Molo")
    assert "only have 3 Mango Float left" in reply.text and "Total:" not in reply.text and "Cakes:" not in reply.text


def test_a_sold_out_cake_is_named_and_no_total_is_given():
    reply, _ = quote([("Yema Cake", 1), ("Leche Flan", 1)])
    assert "Leche Flan" in reply.text and "Total:" not in reply.text and "Cakes:" not in reply.text


def test_an_unknown_cake_is_named_and_no_total_is_given():
    reply, _ = quote([("Yema Cake", 1), ("Ube Cake", 2)])
    assert "couldn't find Ube Cake" in reply.text and "1 × Yema Cake" in reply.text and "Total:" not in reply.text


def test_missing_quantities_show_unit_prices_and_ask_how_many():
    reply, _ = quote([("Yema Cake", None), ("Mango Float", None)])
    assert "Yema Cake — ₱400.00 each" in reply.text and "How many" in reply.text and "Total:" not in reply.text


def test_an_absurd_quantity_is_not_quoted():
    reply, _ = quote([("Yema Cake", 500), ("Chocolate Moist Cake", 1)])
    assert "How many" in reply.text and "₱" in reply.text and "Total:" not in reply.text


def test_the_same_cake_named_twice_is_added_up():
    reply, _ = quote([("Yema Cake", 2), ("Yema Cake", 1)], place="Jaro")
    assert "3 × Yema Cake — ₱1,200.00" in reply.text and "Total: ₱1,250.00" in reply.text


def test_prices_with_centavos_add_up_exactly():
    reply, _ = quote([("Chocolate Moist Cake", 3)], place="Jaro")
    assert "₱1,351.50" in reply.text and "Total: ₱1,401.50" in reply.text


def test_a_plain_single_price_question_is_answered_as_before():
    reply, _ = quote([("Yema Cake", None)])
    assert reply.text == t("en", "price_line", name="Yema Cake", price="₱400.00", desc="\nChiffon with yema frosting")


def test_a_single_cake_with_a_quantity_is_a_quote():
    reply, _ = quote([("Yema Cake", 2)])
    assert "Cakes: ₱800.00" in reply.text


# ---------- language and safety ----------
@pytest.mark.parametrize("lang", ["tl", "hil"])
def test_quotes_are_written_in_the_customers_language(lang):
    reply, _ = quote([("Yema Cake", 3)], place="Molo", lang=lang)
    assert reply.text.startswith(t(lang, "quote_header")) and "₱1,270.00" in reply.text


def test_a_quote_never_creates_an_order_and_repeats_what_it_understood():
    reply, database = quote([("Yema Cake", 3)], place="Molo")  # rpc() would raise if an order were created
    assert "what I understood" in reply.text and "final total before you confirm" in reply.text
    assert "orders" not in database.tables


def test_a_quote_during_an_order_does_not_disturb_it():
    result = AIResult(intent=Intent.PRICE_QUESTION, language="en", items=[QuoteItem(product="Yema Cake", quantity=2)])
    database = db()
    draft = _new_draft("en") | {"cart": [{"product": "Mango Float", "quantity": 1}]}  # an order with a cake already in it
    database.tables["conversations"] = [{"messenger_id": USER, "state": COLLECTING, "draft": draft}]
    replies = ConversationService(FakeAI(result), database).handle(USER, "how much for 2 yema?", None)
    assert "Cakes: ₱800.00" in replies[0].text
    saved = database.tables["conversations"][0]
    assert saved["state"] == COLLECTING and saved["draft"]["cart"] == [{"product": "Mango Float", "quantity": 1}]
