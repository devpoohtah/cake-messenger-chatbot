"""Order business logic.

The AI never touches the database. It produces structured data; this module
validates it and only then writes - atomically, via the Postgres RPC function
`create_order_with_items` (see supabase/schema.sql).
"""
import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.schemas.order import OrderCreated, OrderItemCreated, OrderRequest

logger = logging.getLogger(__name__)

MAX_QUANTITY_PER_ITEM = 50  # sanity cap; adjust as the business needs
_CENT = Decimal("0.01")


class OrderValidationError(ValueError):
    """Base class for all order validation failures."""


class InvalidQuantityError(OrderValidationError): ...
class ProductNotFoundError(OrderValidationError): ...
class ProductUnavailableError(OrderValidationError): ...
class OrderNotConfirmedError(OrderValidationError): ...
class OrderCreationError(RuntimeError): ...


@dataclass(frozen=True)
class OrderLine:
    product_id: int
    product_name: str
    quantity: int
    unit_price: Decimal
    subtotal: Decimal


# ---------- pure helpers (no database) ----------

def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def validate_quantity(quantity: int) -> int:
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise InvalidQuantityError("Quantity must be a whole number.")
    if quantity <= 0:
        raise InvalidQuantityError("Quantity must be greater than zero.")
    if quantity > MAX_QUANTITY_PER_ITEM:
        raise InvalidQuantityError(f"Quantity cannot exceed {MAX_QUANTITY_PER_ITEM} per item.")
    return quantity


def validate_product(product: dict[str, Any] | None, requested_name: str = "") -> dict[str, Any]:
    if product is None:
        raise ProductNotFoundError(f"Product not found: {requested_name!r}")
    if not product.get("is_available", False):
        raise ProductUnavailableError(f"Product is currently unavailable: {product.get('name')!r}")
    return product


def calculate_subtotal(unit_price: Any, quantity: int) -> Decimal:
    return _money(_money(unit_price) * validate_quantity(quantity))


def calculate_total(lines: list[OrderLine]) -> Decimal:
    return _money(sum((line.subtotal for line in lines), Decimal("0")))


def build_order_lines(request: OrderRequest, products: list[dict[str, Any]]) -> list[OrderLine]:
    """Validate each requested item against the product catalog and price it.

    Duplicate products are merged (quantities summed) so the DB's
    UNIQUE (order_id, product_id) constraint is never violated.
    """
    by_name = {p["name"].strip().lower(): p for p in products}
    merged: dict[int, tuple[dict[str, Any], int]] = {}

    for item in request.items:
        product = validate_product(by_name.get(item.product_name.strip().lower()), item.product_name)
        qty = validate_quantity(item.quantity)
        pid = int(product["product_id"])
        prev_qty = merged[pid][1] if pid in merged else 0
        merged[pid] = (product, prev_qty + qty)

    lines = []
    for product, qty in merged.values():
        qty = validate_quantity(qty)  # re-check merged total against the cap
        unit_price = _money(product["price"])
        lines.append(
            OrderLine(
                product_id=int(product["product_id"]),
                product_name=product["name"],
                quantity=qty,
                unit_price=unit_price,
                subtotal=calculate_subtotal(unit_price, qty),
            )
        )
    return lines


# ---------- database-backed operations ----------

def _fetch_products(client: Any) -> list[dict[str, Any]]:
    # The catalog is tiny, so fetch it all and match names case-insensitively in Python.
    return client.table("products").select("*").execute().data or []


def create_order(request: OrderRequest, client: Any | None = None) -> OrderCreated:
    """Create an order. Refuses unless the customer explicitly confirmed."""
    if not request.confirmed:
        raise OrderNotConfirmedError("Order has not been explicitly confirmed by the customer.")

    if client is None:
        from app.db.supabase import get_supabase_client

        client = get_supabase_client()

    lines = build_order_lines(request, _fetch_products(client))
    expected_total = calculate_total(lines)

    try:
        response = client.rpc(
            "create_order_with_items",
            {
                "p_customer_name": request.customer_name,
                "p_contact_number": request.contact_number,
                "p_messenger_id": request.messenger_id,
                "p_location": request.location,
                "p_items": [{"product_id": l.product_id, "quantity": l.quantity} for l in lines],
            },
        ).execute()
    except Exception as exc:  # supabase/postgrest errors
        logger.exception("create_order_with_items RPC failed")
        raise OrderCreationError("Could not save the order.") from exc

    data = response.data
    if not data:
        raise OrderCreationError("Order function returned no data.")

    created = OrderCreated(
        **{**data, "items": [OrderItemCreated(**i) for i in data["items"]]}
    )
    # The DB prices items itself; if it disagrees with our preview, log loudly.
    if _money(created.total_amount) != expected_total:
        logger.warning(
            "Total mismatch for order %s: backend=%s db=%s", created.order_id, expected_total, created.total_amount
        )
    return created
