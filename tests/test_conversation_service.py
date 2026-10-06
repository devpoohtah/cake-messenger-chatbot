from app.services.ai_service import AIResult, Intent
from app.services.conversation_service import AWAITING, COLLECTING, IDLE, ConversationService

PRODUCTS = [
    {"product_id": 1, "name": "Mango Float", "description": None, "price": 400, "is_available": True},
    {"product_id": 2, "name": "Leche Flan", "description": None, "price": 400, "is_available": True},
    {"product_id": 3, "name": "Yema Cake", "description": None, "price": 400, "is_available": False},
]
USER = "1001"


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, name):
        self.db, self.name, self.mode, self.row, self.filters = db, name, "select", None, []

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def limit(self, n):
        return self

    def order(self, *a, **k):
        return self

    def upsert(self, row, **k):
        self.mode, self.row = "upsert", row
        return self

    def execute(self):
        if self.mode == "upsert":
            self.db.conversations[self.row["messenger_id"]] = self.row
            return _Result([self.row])
        if self.name == "products":
            return _Result(PRODUCTS)
        return _Result([r for r in self.db.conversations.values() if all(r.get(c) == v for c, v in self.filters)])


class FakeDB:
    def __init__(self):
        self.conversations, self.rpc_calls = {}, []

    def table(self, name):
        return _Query(self, name)

    def rpc(self, fn, params):
        self.rpc_calls.append((fn, params))
        order = {
            "order_id": 10, "customer_id": 5, "status": "pending", "total_amount": 800,
            "location": "Rizal St", "order_date": "2026-10-04T10:00:00+00:00",
            "items": [{"product_id": 1, "product_name": "Mango Float", "quantity": 2,
                       "unit_price": 400, "subtotal": 800}],
        }
        return type("R", (), {"execute": lambda self: _Result(order)})()


class FakeAI:
    """Returns scripted results and records whether it was called."""

    def __init__(self, *results):
        self.results, self.calls = list(results), 0

    def interpret_message(self, message, state, names):
        self.calls += 1
        return self.results.pop(0) if self.results else AIResult(intent=Intent.UNKNOWN)


def state_of(db):
    return db.conversations[USER]["state"]


def test_full_order_flow_creates_order_only_after_confirmation():
    db = FakeDB()
    ai = FakeAI(
        AIResult(intent=Intent.START_ORDER, product="Mango Float", quantity=2),
        AIResult(intent=Intent.PROVIDE_ORDER_DETAILS, customer_name="Ana Cruz"),
        AIResult(intent=Intent.PROVIDE_ORDER_DETAILS, contact_number="09171234567"),
        AIResult(intent=Intent.PROVIDE_ORDER_DETAILS, location="Rizal St"),
    )
    svc = ConversationService(ai, db)

    assert "name" in svc.handle(USER, "i want 2 mango float", "m1").lower()
    assert state_of(db) == COLLECTING
    assert "contact" in svc.handle(USER, "Ana Cruz", "m2").lower()
    assert "address" in svc.handle(USER, "09171234567", "m3").lower()
    summary = svc.handle(USER, "Rizal St", "m4")
    assert "₱800.00" in summary and state_of(db) == AWAITING
    assert db.rpc_calls == []  # nothing saved before confirmation

    reply = svc.handle(USER, "YES", "m5")
    assert "#10" in reply and state_of(db) == IDLE
    assert len(db.rpc_calls) == 1
    assert db.rpc_calls[0][1]["p_messenger_id"] == USER


def test_retried_message_is_not_processed_twice():
    db = FakeDB()
    svc = ConversationService(FakeAI(AIResult(intent=Intent.GREETING)), db)
    assert svc.handle(USER, "hi", "m1") is not None
    assert svc.handle(USER, "hi", "m1") is None


def test_yes_while_idle_never_creates_an_order():
    db = FakeDB()
    svc = ConversationService(FakeAI(AIResult(intent=Intent.CONFIRM_ORDER)), db)
    svc.handle(USER, "yes", "m1")
    assert db.rpc_calls == [] and state_of(db) == IDLE


def test_cancel_resets_the_order():
    db = FakeDB()
    ai = FakeAI(AIResult(intent=Intent.START_ORDER, product="Leche Flan", quantity=1))
    svc = ConversationService(ai, db)
    svc.handle(USER, "order leche flan", "m1")
    assert state_of(db) == COLLECTING
    assert "cancelled" in svc.handle(USER, "cancel", "m2")
    assert state_of(db) == IDLE and ai.calls == 1  # cancel needed no AI call


def test_invalid_ai_data_is_rejected():
    db = FakeDB()
    ai = FakeAI(AIResult(intent=Intent.START_ORDER, product="Pizza", quantity=0, contact_number="abc"))
    svc = ConversationService(ai, db)
    reply = svc.handle(USER, "pizza please", "m1")
    assert "Which cake" in reply  # unknown product and bad quantity ignored


def test_unavailable_product_is_not_accepted():
    db = FakeDB()
    ai = FakeAI(AIResult(intent=Intent.START_ORDER, product="Yema Cake", quantity=1))
    reply = ConversationService(ai, db).handle(USER, "yema cake", "m1")
    assert "unavailable" in reply


def test_price_answer_comes_from_database():
    db = FakeDB()
    ai = FakeAI(AIResult(intent=Intent.PRICE_QUESTION, product="Leche Flan"))
    assert "₱400.00" in ConversationService(ai, db).handle(USER, "how much leche flan", "m1")