"""Order business logic.

The AI never touches the database. It produces structured data; this module
validates it and only then writes - atomically, via the Postgres RPC function
`create_order_with_items` (see supabase/schema.sql).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.schemas.order import OrderCreated, OrderItemCreated, OrderRequest

logger = logging.getLogger(__name__)
MAX_QUANTITY_PER_ITEM = 10  # per cake adjust to how many the shop can do
_CENT = Decimal("0.01")


class OrderValidationError(ValueError):
    """Base class for all order validation failures."""


class InvalidQuantityError(OrderValidationError): ...
class ProductNotFoundError(OrderValidationError): ...
class ProductUnavailableError(OrderValidationError): ...
class OrderNotConfirmedError(OrderValidationError): ...


class InsufficientStockError(OrderValidationError):
    """Fewer cakes in stock than requested. left == 0 means sold out."""

    def __init__(self, product_name: str = "", left: int = 0) -> None:
        super().__init__(f"Not enough stock for {product_name!r}: {left} left")
        self.product_name, self.left = product_name, left


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
    stock = product.get("stock")  # None = unlimited, 0 = sold out
    if stock is not None and stock <= 0:
        raise InsufficientStockError(str(product.get("name", "")), 0)
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
        stock = product.get("stock")
        if stock is not None and qty > stock:
            raise InsufficientStockError(product["name"], int(stock))
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

def get_latest_order(client: Any, messenger_id: str) -> dict[str, Any] | None:
    """The newest order of the customer with this Messenger ID, or None if there is none."""
    customers = client.table("customers").select("customer_id").eq("messenger_id", messenger_id).limit(1).execute().data
    if not customers:
        return None
    rows = (
        client.table("orders")
        .select("order_id, status, total_amount, order_items(quantity, products(name))")
        .eq("customer_id", customers[0]["customer_id"])
        .order("order_date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "order_id": r["order_id"],
        "status": r["status"],
        "total_amount": Decimal(str(r["total_amount"])),
        "items": [((i.get("products") or {}).get("name", "?"), i["quantity"]) for i in r.get("order_items") or []],
    }

def best_sellers(client: Any, days: int = 30) -> tuple[list[tuple[str, int]], int]:
    """Cakes ranked by how many were ordered in the last `days` days, cancelled orders left out.

    Returns ([(cake name, quantity), ...] best first, total quantity of all cakes).
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = (
        client.table("orders")
        .select("order_id, order_items(quantity, products(name))")
        .gte("order_date", since)
        .neq("status", "cancelled")
        .limit(5000)
        .execute()
        .data
        or []
    )
    totals: dict[str, int] = {}
    for row in rows:
        for item in row.get("order_items") or []:
            name = (item.get("products") or {}).get("name")
            if name:
                totals[name] = totals.get(name, 0) + int(item["quantity"])
    return sorted(totals.items(), key=lambda pair: (-pair[1], pair[0])), sum(totals.values())


ACTIVE_STATUSES = ("pending", "confirmed", "preparing", "ready", "out_for_delivery")  # not completed, not cancelled


def _customer_id(client: Any, messenger_id: str) -> Any | None:
    rows = client.table("customers").select("customer_id").eq("messenger_id", messenger_id).limit(1).execute().data
    return rows[0]["customer_id"] if rows else None


def get_active_orders(client: Any, messenger_id: str, limit: int = 3) -> list[dict[str, Any]]:
    """The newest orders of this Messenger account that are still open, newest first."""
    customer_id = _customer_id(client, messenger_id)
    if customer_id is None:
        return []
    rows = (
        client.table("orders")
        .select("order_id, status, total_amount, order_items(quantity, products(name))")
        .eq("customer_id", customer_id)
        .in_("status", list(ACTIVE_STATUSES))
        .order("order_date", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )
    return [
        {
            "order_id": r["order_id"],
            "status": r["status"],
            "total_amount": Decimal(str(r["total_amount"])),
            "items": [((i.get("products") or {}).get("name", "?"), i["quantity"]) for i in r.get("order_items") or []],
        }
        for r in rows
    ]


def cancel_pending_order(client: Any, messenger_id: str, order_id: int) -> str:
    """Cancel an order for its own customer, but only while it is still 'pending'.

    Returns "cancelled", "late" (the owner already started on it) or "none" (no such order on this account).
    The status check is part of the UPDATE itself, so an owner accepting the order at the same moment wins.
    """
    customer_id = _customer_id(client, messenger_id)
    if customer_id is None:
        return "none"
    found = client.table("orders").select("order_id, status").eq("order_id", order_id).eq("customer_id", customer_id).limit(1).execute().data
    if not found:
        return "none"
    if found[0]["status"] == "cancelled":
        return "cancelled"
    done = (
        client.table("orders").update({"status": "cancelled"})
        .eq("order_id", order_id).eq("customer_id", customer_id).eq("status", "pending").execute().data
    )
    return "cancelled" if done else "late"


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
        if "not enough stock" in str(exc).lower():  # someone else took the cakes a moment ago
            raise InsufficientStockError() from exc
        logger.exception("create_order_with_items RPC failed")
        raise OrderCreationError("Could not save the order.") from exc

    data = response.data  # restored: this line was lost in an earlier commit, so no order could be saved

    # Delivery/pick-up and notes are saved right after the order. If this fails (for example the
    # columns were not added yet), the order itself is already saved, so only log it.
    try:
        extra: dict[str, Any] = {"fulfillment": request.fulfillment}
        if request.notes:
            extra["notes"] = request.notes
        client.table("orders").update(extra).eq("order_id", data["order_id"]).execute()
    except Exception:
        logger.warning("Could not save fulfillment/notes for order %s", data.get("order_id"))   

    if request.dedication:
        try:
            client.table("orders").update({"dedication": request.dedication}).eq("order_id", data["order_id"]).execute()
        except Exception:
            logger.warning("Could not save the dedication for order %s; did you run the dedication SQL?", data.get("order_id"))

    created = OrderCreated(
        **{**data, "items": [OrderItemCreated(**i) for i in data["items"]]}
    )
    # The DB prices items itself; if it disagrees with our preview, log loudly.
    if _money(created.total_amount) != expected_total:
        logger.warning(
            "Total mismatch for order %s: backend=%s db=%s", created.order_id, expected_total, created.total_amount
        )

    if request.fulfillment == "delivery" and request.delivery_area:
        # The delivery fee is added to the total. A fee of None means the owner will confirm it later
        # in the dashboard, so the total stays at the cakes only until then.
        fee = _money(request.delivery_fee) if request.delivery_fee is not None else None
        total = _money(created.total_amount + (fee or Decimal("0")))
        try:
            client.table("orders").update(
                {"delivery_area": request.delivery_area, "delivery_fee": float(fee) if fee is not None else None,
                 "total_amount": float(total)}
            ).eq("order_id", created.order_id).execute()
        except Exception:
            logger.error("Could not save the delivery area/fee for order %s; did you run the delivery areas SQL?", created.order_id)
        created = created.model_copy(update={"total_amount": total})  # the customer is told the right total either way
    return created
