"""Owner dashboard: one HTML page plus a small JSON API for orders and products.

For now there is NO login: the dashboard is open while ADMIN_PASSWORD (in .env) is empty.
Set ADMIN_PASSWORD to switch on HTTP Basic login (username "admin") for the page and the API.
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from app.core.config import get_settings

logger = logging.getLogger("uvicorn.error")
basic = HTTPBasic(auto_error=False)
PH_TIME = timezone(timedelta(hours=8))  # the Philippines has no daylight saving

# The page is looked for in app/templates first, then in <project>/templates.
_HERE = Path(__file__).resolve()
TEMPLATE_CANDIDATES = [
    _HERE.parents[2] / "templates" / "dashboard" / "index.html",
    _HERE.parents[3] / "templates" / "dashboard" / "index.html",
]


def require_admin(credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)]) -> None:
    password = get_settings().admin_password.get_secret_value()
    if not password:
        return  # no login for now: the dashboard is open while ADMIN_PASSWORD is empty
    ok = (
        credentials is not None
        and secrets.compare_digest(credentials.username.encode(), b"admin")
        and secrets.compare_digest(credentials.password.encode(), password.encode())
    )
    if not ok:
        raise HTTPException(
            status_code=401, detail="Login required", headers={"WWW-Authenticate": 'Basic realm="Mrs Brave admin"'}
        )


def get_db() -> Any:
    from app.db.supabase import get_supabase_client

    return get_supabase_client()


router = APIRouter(prefix="/admin")  # NO login for now. 

ORDER_SELECT = "order_id, order_date, status, total_amount, location, customers(name, contact_number), order_items(quantity, products(name))"


def _when(value: str | None) -> str:
    try:
        return datetime.fromisoformat(value).astimezone(PH_TIME).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value or "")


def _shape_order(r: dict[str, Any]) -> dict[str, Any]:
    customer = r.get("customers") or {}
    items = ", ".join(
        f"{i['quantity']} × {(i.get('products') or {}).get('name', '?')}" for i in r.get("order_items") or []
    )
    return {
        "id": r["order_id"],
        "date": _when(r.get("order_date")),
        "name": customer.get("name", ""),
        "phone": customer.get("contact_number", ""),
        "items": items,
        "total": float(r["total_amount"]),
        "fulfillment": r.get("fulfillment") or "delivery",
        "where": r["location"],
        "notes": r.get("notes") or "",
        "status": r["status"],
    }


@router.get("", include_in_schema=False)
def dashboard_page() -> FileResponse:
    for path in TEMPLATE_CANDIDATES:
        if path.is_file():
            return FileResponse(path, media_type="text/html", headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="templates/dashboard/index.html not found")


@router.get("/api/orders")
def list_orders(db: Any = Depends(get_db)) -> list[dict[str, Any]]:
    def fetch(columns: str) -> list[dict[str, Any]]:
        return db.table("orders").select(columns).order("order_date", desc=True).limit(200).execute().data or []

    try:
        rows = fetch(ORDER_SELECT + ", fulfillment, notes")
    except Exception:
        # The Phase 3 columns are missing: run the Phase 3 SQL. Meanwhile show orders without them.
        logger.warning("orders.fulfillment/notes not found; run the Phase 3 SQL. Retrying without them.")
        try:
            rows = fetch(ORDER_SELECT)
        except Exception:
            logger.exception("Could not load orders")
            raise HTTPException(status_code=502, detail="Could not load orders")
    return [_shape_order(r) for r in rows]


class OrderPatch(BaseModel):
    status: Literal["pending", "completed", "cancelled"]


@router.patch("/api/orders/{order_id}")
def update_order(order_id: int, body: OrderPatch, db: Any = Depends(get_db)) -> dict[str, Any]:
    rows = db.table("orders").update({"status": body.status}).eq("order_id", order_id).execute().data
    if not rows:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"id": order_id, "status": rows[0]["status"]}


@router.get("/api/products")
def list_products(db: Any = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.table("products").select("product_id, name, price, is_available").order("product_id").execute().data or []
    return [
        {"id": r["product_id"], "name": r["name"], "price": float(r["price"]), "is_available": bool(r["is_available"])}
        for r in rows
    ]


class ProductPatch(BaseModel):
    price: float | None = Field(default=None, ge=0, le=100000)
    is_available: bool | None = None


@router.patch("/api/products/{product_id}")
def update_product(product_id: int, body: ProductPatch, db: Any = Depends(get_db)) -> dict[str, Any]:
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to update")
    if "price" in changes:
        changes["price"] = round(changes["price"], 2)
    rows = db.table("products").update(changes).eq("product_id", product_id).execute().data
    if not rows:
        raise HTTPException(status_code=404, detail="Product not found")
    r = rows[0]
    return {"id": product_id, "name": r["name"], "price": float(r["price"]), "is_available": bool(r["is_available"])}