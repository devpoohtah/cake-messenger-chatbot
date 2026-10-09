"""Owner dashboard: one HTML page plus a small JSON API for orders and products.

For now there is NO login: the dashboard is open while ADMIN_PASSWORD (in .env) is empty.
Set ADMIN_PASSWORD to switch on HTTP Basic login (username "admin") for the page and the API.
"""
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.services import faq_store
from app.services.faq import FAQS as DEFAULT_FAQS

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



# ---------- FAQ topics ----------
TOPIC_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
MAX_FAQ_TOPICS = 30  # the topic list is sent to the AI with every message, so keep it short
FAQ_COLUMNS = "topic, description, answer_en, answer_tl, answer_hil"


class FaqIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    description: str = Field(min_length=10, max_length=300)
    answer_en: str = Field(min_length=1, max_length=1000)
    answer_tl: str | None = Field(default=None, max_length=1000)
    answer_hil: str | None = Field(default=None, max_length=1000)


def _check_topic(topic: str) -> None:
    if not TOPIC_RE.match(topic):
        raise HTTPException(
            status_code=400,
            detail="Topic name must be 2-40 characters: lowercase letters, numbers and underscores, starting with a letter",
        )


@router.get("/api/faqs")
def list_faqs(db: Any = Depends(get_db)) -> list[dict[str, Any]]:
    try:
        return db.table("faqs").select(FAQ_COLUMNS).order("topic").execute().data or []
    except Exception:
        logger.exception("Could not load FAQs")
        raise HTTPException(status_code=502, detail="Could not load the FAQ. Did you run the FAQ SQL in Supabase?")


@router.put("/api/faqs/{topic}")
def save_faq(topic: str, body: FaqIn, db: Any = Depends(get_db)) -> dict[str, Any]:
    """Create or update one topic."""
    _check_topic(topic)
    existing = {r["topic"] for r in db.table("faqs").select("topic").execute().data or []}
    if topic not in existing and len(existing) >= MAX_FAQ_TOPICS:
        raise HTTPException(status_code=400, detail=f"At most {MAX_FAQ_TOPICS} topics")
    row = {
        "topic": topic,
        "description": body.description,
        "answer_en": body.answer_en,
        "answer_tl": body.answer_tl or None,
        "answer_hil": body.answer_hil or None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    db.table("faqs").upsert(row, on_conflict="topic").execute()
    faq_store.clear_cache()
    return {k: row[k] for k in ("topic", "description", "answer_en", "answer_tl", "answer_hil")}


@router.delete("/api/faqs/{topic}")
def delete_faq(topic: str, db: Any = Depends(get_db)) -> dict[str, Any]:
    _check_topic(topic)
    rows = db.table("faqs").delete().eq("topic", topic).execute().data
    if not rows:
        raise HTTPException(status_code=404, detail="Topic not found")
    faq_store.clear_cache()
    return {"topic": topic, "deleted": True}


@router.post("/api/faqs-load-defaults")
def load_default_faqs(db: Any = Depends(get_db)) -> list[dict[str, Any]]:
    """Copy the built-in answers from faq.py into the table, so the owner can edit them."""
    if db.table("faqs").select("topic").limit(1).execute().data:
        raise HTTPException(status_code=400, detail="There are already saved topics")
    rows = [
        {
            "topic": topic,
            "description": entry["description"],
            "answer_en": entry["en"],
            "answer_tl": entry.get("tl"),
            "answer_hil": entry.get("hil"),
        }
        for topic, entry in DEFAULT_FAQS.items()
    ]
    db.table("faqs").insert(rows).execute()
    faq_store.clear_cache()
    return [{k: r[k] for k in ("topic", "description", "answer_en", "answer_tl", "answer_hil")} for r in rows]