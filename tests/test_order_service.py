from decimal import Decimal

import pytest

from app.schemas.order import OrderRequest
from app.services import order_service as svc

PRODUCTS = [
    {"product_id": 1, "name": "Nutella Ferrero Cake", "price": 400, "is_available": True},
    {"product_id": 2, "name": "Leche Flan", "price": 400, "is_available": True},
    {"product_id": 3, "name": "Yema Cake", "price": 400, "is_available": False},
]


def make_request(items, confirmed=True) -> OrderRequest:
    return OrderRequest(
        customer_name="Juan Dela Cruz",
        contact_number="0917 123 4567",
        location="Iloilo City",
        items=items,
        confirmed=confirmed,
    )


# ---- quantity validation ----
@pytest.mark.parametrize("qty", [0, -1, svc.MAX_QUANTITY_PER_ITEM + 1])
def test_invalid_quantity_rejected(qty):
    with pytest.raises(svc.InvalidQuantityError):
        svc.validate_quantity(qty)


def test_valid_quantity_accepted():
    assert svc.validate_quantity(2) == 2


# ---- product validation ----
def test_unknown_product_rejected():
    req = make_request([{"product_name": "Pizza", "quantity": 1}])
    with pytest.raises(svc.ProductNotFoundError):
        svc.build_order_lines(req, PRODUCTS)


def test_unavailable_product_rejected():
    req = make_request([{"product_name": "Yema Cake", "quantity": 1}])
    with pytest.raises(svc.ProductUnavailableError):
        svc.build_order_lines(req, PRODUCTS)


def test_product_name_is_case_insensitive():
    req = make_request([{"product_name": "leche flan", "quantity": 1}])
    assert svc.build_order_lines(req, PRODUCTS)[0].product_id == 2


# ---- totals ----
def test_subtotal_and_total():
    req = make_request(
        [{"product_name": "Nutella Ferrero Cake", "quantity": 2}, {"product_name": "Leche Flan", "quantity": 1}]
    )
    lines = svc.build_order_lines(req, PRODUCTS)
    assert [l.subtotal for l in lines] == [Decimal("800.00"), Decimal("400.00")]
    assert svc.calculate_total(lines) == Decimal("1200.00")


def test_duplicate_lines_are_merged():
    req = make_request(
        [{"product_name": "Leche Flan", "quantity": 1}, {"product_name": "Leche Flan", "quantity": 2}]
    )
    lines = svc.build_order_lines(req, PRODUCTS)
    assert len(lines) == 1 and lines[0].quantity == 3


# ---- confirmation gate + create_order with a fake client ----
class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, data):
        self._data = data

    def select(self, *_a, **_k):
        return self

    def execute(self):
        return _Result(self._data)


class FakeClient:
    def __init__(self):
        self.rpc_calls = []

    def table(self, name):
        assert name == "products"
        return _Query(PRODUCTS)

    def rpc(self, fn, params):
        self.rpc_calls.append((fn, params))
        return _Query(
            {
                "order_id": 10,
                "customer_id": 5,
                "status": "pending",
                "total_amount": 800,
                "location": "Iloilo City",
                "order_date": "2026-10-04T10:00:00+00:00",
                "items": [
                    {"product_id": 1, "product_name": "Nutella Ferrero Cake", "quantity": 2,
                     "unit_price": 400, "subtotal": 800}
                ],
            }
        )


def test_unconfirmed_order_never_touches_database():
    client = FakeClient()
    req = make_request([{"product_name": "Leche Flan", "quantity": 1}], confirmed=False)
    with pytest.raises(svc.OrderNotConfirmedError):
        svc.create_order(req, client=client)
    assert client.rpc_calls == []


def test_confirmed_order_calls_rpc_and_returns_pending():
    client = FakeClient()
    req = make_request([{"product_name": "Nutella Ferrero Cake", "quantity": 2}])
    order = svc.create_order(req, client=client)
    assert client.rpc_calls[0][0] == "create_order_with_items"
    assert client.rpc_calls[0][1]["p_items"] == [{"product_id": 1, "quantity": 2}]
    assert order.status == "pending" and order.total_amount == Decimal("800")


def test_invalid_phone_rejected_by_schema():
    with pytest.raises(ValueError):
        OrderRequest(customer_name="A", contact_number="abc", location="X",
                     items=[{"product_name": "Leche Flan", "quantity": 1}])
