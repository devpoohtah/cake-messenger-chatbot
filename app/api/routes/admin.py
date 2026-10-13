"""Owner dashboard: one HTML page plus a small JSON API for orders and products.

For now there is NO login: the dashboard is open while ADMIN_PASSWORD (in .env) is empty.
Set ADMIN_PASSWORD to switch on HTTP Basic login (username "admin") for the page and the API.
"""
import base64
import logging
import re
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.services import faq_store, owner_inbox, shop_hours
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
        "dedication": r.get("dedication") or "",
        "delivery_area": r.get("delivery_area") or "",
        "delivery_fee": float(r["delivery_fee"]) if r.get("delivery_fee") is not None else None,
        # a delivery order with an area but no fee yet: the owner still has to confirm the fee
        "fee_pending": r.get("fulfillment") == "delivery" and bool(r.get("delivery_area")) and r.get("delivery_fee") is None,
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

    # Newer columns may not exist yet (run the SQL for them). Meanwhile show the orders without them.
    for extra in (", fulfillment, notes, dedication, delivery_area, delivery_fee", ", fulfillment, notes, dedication", ", fulfillment, notes", ""):
        try:
            return [_shape_order(r) for r in fetch(ORDER_SELECT + extra)]
        except Exception:
            if extra:
                logger.warning("Order columns missing (%s); run the SQL for them. Retrying without.", extra.strip(", "))
    logger.exception("Could not load orders")
    raise HTTPException(status_code=502, detail="Could not load orders")


class OrderPatch(BaseModel):
    # ready = ready for pick up. The database must allow all of these (see the order statuses SQL).
    status: Literal["pending", "confirmed", "preparing", "ready", "out_for_delivery", "completed", "cancelled"]


@router.patch("/api/orders/{order_id}")
def update_order(order_id: int, body: OrderPatch, db: Any = Depends(get_db)) -> dict[str, Any]:
    try:
        rows = db.table("orders").update({"status": body.status}).eq("order_id", order_id).execute().data
    except Exception as exc:
        if "not enough stock" in str(exc).lower():  # reopening a cancelled order needs the cakes back
            raise HTTPException(status_code=409, detail="Not enough stock to reopen this order")
        raise
    if not rows:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"id": order_id, "status": rows[0]["status"]}


IMAGE_BUCKET = "cakes"  # a public Supabase Storage bucket (created by the Step 1 SQL)
MAX_IMAGE_BYTES = 1_500_000  # the dashboard shrinks photos to about 150 KB before sending them


def _product_out(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["product_id"],
        "name": r["name"],
        "price": float(r["price"]),
        "description": r.get("description") or "",
        "stock": r.get("stock"),  # None = unlimited, 0 = sold out
        "image_url": r.get("image_url") or "",
        "is_available": bool(r["is_available"]),
    }


def _decode_image(image_data: str) -> bytes:
    """The dashboard sends the photo as a JPEG data URL. Check it before anything is saved."""
    raw = image_data.split(",", 1)[1] if image_data.startswith("data:") else image_data
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="The photo could not be read")
    if len(blob) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="The photo is too big")
    if not blob.startswith(b"\xff\xd8\xff"):
        raise HTTPException(status_code=400, detail="The photo must be a JPEG")
    return blob


def _upload_image(db: Any, blob: bytes) -> str:
    path = f"products/{uuid.uuid4().hex}.jpg"
    bucket = db.storage.from_(IMAGE_BUCKET)
    try:
        bucket.upload(path, blob, {"content-type": "image/jpeg", "cache-control": "31536000"})
    except Exception:
        logger.exception("Photo upload failed")
        raise HTTPException(status_code=502, detail="Could not upload the photo. Did you run the Step 1 SQL (cakes bucket)?")
    return bucket.get_public_url(path)


def _remove_image(db: Any, url: str | None) -> None:
    """Best effort: delete the old file from storage. A leftover file is harmless."""
    marker = f"/{IMAGE_BUCKET}/"
    if not url or marker not in url:
        return
    try:
        db.storage.from_(IMAGE_BUCKET).remove([url.split(marker, 1)[1].split("?", 1)[0]])
    except Exception:
        logger.warning("Could not delete an old cake photo from storage")


def _name_taken(db: Any, name: str, except_id: int | None = None) -> bool:
    rows = db.table("products").select("product_id, name").execute().data or []
    return any(r["name"].strip().lower() == name.strip().lower() and r["product_id"] != except_id for r in rows)


@router.get("/api/products")
def list_products(db: Any = Depends(get_db)) -> list[dict[str, Any]]:
    try:
        rows = db.table("products").select("*").order("product_id").execute().data or []
    except Exception:
        logger.exception("Could not load products")
        raise HTTPException(status_code=502, detail="Could not load the cakes")
    return [_product_out(r) for r in rows]


class ProductIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    price: float = Field(ge=0, le=100000)
    description: str | None = Field(default=None, max_length=60)  # shown under the price in Messenger (80 characters)
    stock: int | None = Field(default=None, ge=0, le=100000)  # how many are available; empty = unlimited
    image_data: str | None = None


@router.post("/api/products")
def create_product(body: ProductIn, db: Any = Depends(get_db)) -> dict[str, Any]:
    if _name_taken(db, body.name):
        raise HTTPException(status_code=409, detail="A cake with that name already exists")
    blob = _decode_image(body.image_data) if body.image_data else None  # checked before anything is saved
    row = {
        "name": body.name,
        "price": round(body.price, 2),
        "description": body.description or None,
        "stock": body.stock,
        "is_available": True,
    }
    if blob:
        row["image_url"] = _upload_image(db, blob)
    try:
        rows = db.table("products").insert(row).execute().data
    except Exception:
        _remove_image(db, row.get("image_url"))
        logger.exception("Could not save the new cake")
        raise HTTPException(status_code=502, detail="Could not save the cake. Did you run the Step 1 SQL?")
    return _product_out(rows[0])


class ProductPatch(BaseModel):
    price: float | None = Field(default=None, ge=0, le=100000)
    is_available: bool | None = None
    description: str | None = Field(default=None, max_length=60)
    stock: int | None = Field(default=None, ge=0, le=100000)  # sent as null = unlimited
    image_data: str | None = None  # a new photo
    remove_image: bool | None = None


@router.patch("/api/products/{product_id}")
def update_product(product_id: int, body: ProductPatch, db: Any = Depends(get_db)) -> dict[str, Any]:
    sent = body.model_dump(exclude_unset=True)  # only what the dashboard actually sent, so null can mean 'unlimited'
    image_data, remove_image = sent.pop("image_data", None), sent.pop("remove_image", None)
    changes = {k: v for k, v in sent.items() if v is not None or k in ("stock", "description")}
    if "price" in changes:
        changes["price"] = round(changes["price"], 2)
    if "description" in changes:
        changes["description"] = (changes["description"] or "").strip() or None
    if not changes and not image_data and not remove_image:
        raise HTTPException(status_code=400, detail="Nothing to update")

    existing = db.table("products").select("product_id, image_url").eq("product_id", product_id).limit(1).execute().data
    if not existing:
        raise HTTPException(status_code=404, detail="Product not found")
    old_url = existing[0].get("image_url")

    new_url = None
    if image_data:
        new_url = _upload_image(db, _decode_image(image_data))
        changes["image_url"] = new_url
    elif remove_image:
        changes["image_url"] = None
    try:
        rows = db.table("products").update(changes).eq("product_id", product_id).execute().data
    except Exception:
        _remove_image(db, new_url)  # the save failed, so the new photo is not needed
        logger.exception("Could not update product %s", product_id)
        raise HTTPException(status_code=502, detail="Could not save the change. Did you run the Step 1 SQL?")
    if not rows:
        _remove_image(db, new_url)
        raise HTTPException(status_code=404, detail="Product not found")
    if "image_url" in changes:
        _remove_image(db, old_url)
    return _product_out(rows[0])


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


# ---------- Delivery areas and fees ----------
MAX_ACTIVE_AREAS = 12  # Messenger shows at most 13 buttons: up to 12 areas plus "Other area"
AREA_NAME_MAX = 20  # a Messenger button title has at most 20 characters


def _area_out(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "name": r["name"],
        "fee": float(r["fee"]) if r.get("fee") is not None else None,  # None = the owner confirms the fee
        "is_active": bool(r["is_active"]),
        "is_other": bool(r["is_other"]),
    }


def _load_areas(db: Any) -> list[dict[str, Any]]:
    try:
        return db.table("delivery_areas").select("*").order("id").execute().data or []
    except Exception:
        logger.exception("Could not load delivery areas")
        raise HTTPException(status_code=502, detail="Could not load the delivery areas. Did you run the delivery areas SQL?")


@router.get("/api/areas")
def list_areas(db: Any = Depends(get_db)) -> list[dict[str, Any]]:
    rows = _load_areas(db)
    rows.sort(key=lambda r: (bool(r["is_other"]), r["id"]))  # "Other areas" last
    return [_area_out(r) for r in rows]


class AreaIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=AREA_NAME_MAX)
    fee: float = Field(ge=0, le=10000)


@router.post("/api/areas")
def create_area(body: AreaIn, db: Any = Depends(get_db)) -> dict[str, Any]:
    rows = _load_areas(db)
    if any(r["name"].strip().lower() == body.name.lower() for r in rows):
        raise HTTPException(status_code=409, detail="That area is already in the list")
    if sum(1 for r in rows if r["is_active"] and not r["is_other"]) >= MAX_ACTIVE_AREAS:
        raise HTTPException(status_code=409, detail=f"You can have at most {MAX_ACTIVE_AREAS} active areas. Switch one off first.")
    try:
        created = db.table("delivery_areas").insert(
            {"name": body.name, "fee": round(body.fee, 2), "is_active": True, "is_other": False}
        ).execute().data
    except Exception:
        logger.exception("Could not save the delivery area")
        raise HTTPException(status_code=502, detail="Could not save the area")
    return _area_out(created[0])


class AreaPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=AREA_NAME_MAX)
    fee: float | None = Field(default=None, ge=0, le=10000)
    is_active: bool | None = None


@router.patch("/api/areas/{area_id}")
def update_area(area_id: int, body: AreaPatch, db: Any = Depends(get_db)) -> dict[str, Any]:
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to update")
    rows = _load_areas(db)
    area = next((r for r in rows if r["id"] == area_id), None)
    if area is None:
        raise HTTPException(status_code=404, detail="Area not found")
    if area["is_other"] and ("fee" in changes or "name" in changes):
        raise HTTPException(status_code=400, detail="For other areas the owner confirms the fee, so there is nothing to edit here")
    if "name" in changes and any(r["id"] != area_id and r["name"].strip().lower() == changes["name"].lower() for r in rows):
        raise HTTPException(status_code=409, detail="That area is already in the list")
    if changes.get("is_active") and not area["is_other"] and not area["is_active"]:
        if sum(1 for r in rows if r["is_active"] and not r["is_other"]) >= MAX_ACTIVE_AREAS:
            raise HTTPException(status_code=409, detail=f"You can have at most {MAX_ACTIVE_AREAS} active areas. Switch one off first.")
    if "fee" in changes:
        changes["fee"] = round(changes["fee"], 2)
    try:
        updated = db.table("delivery_areas").update(changes).eq("id", area_id).execute().data
    except Exception:
        logger.exception("Could not update delivery area %s", area_id)
        raise HTTPException(status_code=502, detail="Could not save the change")
    return _area_out(updated[0])


@router.delete("/api/areas/{area_id}")
def delete_area(area_id: int, db: Any = Depends(get_db)) -> dict[str, Any]:
    area = next((r for r in _load_areas(db) if r["id"] == area_id), None)
    if area is None:
        raise HTTPException(status_code=404, detail="Area not found")
    if area["is_other"]:
        raise HTTPException(status_code=400, detail="Switch it off instead of deleting it")
    db.table("delivery_areas").delete().eq("id", area_id).execute()  # old orders keep the area name they saved
    return {"id": area_id, "deleted": True}


class FeeIn(BaseModel):
    fee: float = Field(ge=0, le=100000)


@router.patch("/api/orders/{order_id}/fee")
def set_order_fee(order_id: int, body: FeeIn, db: Any = Depends(get_db)) -> dict[str, Any]:
    """The owner confirms the delivery fee of an order whose area is not on the list. The total is rebuilt."""
    try:
        found = db.table("orders").select("order_id, status, fulfillment").eq("order_id", order_id).limit(1).execute().data
    except Exception:
        logger.exception("Could not load order %s", order_id)
        raise HTTPException(status_code=502, detail="Could not load the order")
    if not found:
        raise HTTPException(status_code=404, detail="Order not found")
    if found[0].get("fulfillment") != "delivery":
        raise HTTPException(status_code=400, detail="Pick up orders have no delivery fee")
    if found[0]["status"] in ("completed", "cancelled"):
        raise HTTPException(status_code=409, detail="This order is already closed")
    items = db.table("order_items").select("subtotal").eq("order_id", order_id).execute().data or []
    subtotal = sum((Decimal(str(i["subtotal"])) for i in items), Decimal("0"))
    fee = Decimal(str(round(body.fee, 2)))
    total = subtotal + fee
    try:
        db.table("orders").update({"delivery_fee": float(fee), "total_amount": float(total)}).eq("order_id", order_id).execute()
    except Exception:
        logger.exception("Could not save the fee for order %s", order_id)
        raise HTTPException(status_code=502, detail="Could not save the fee. Did you run the delivery areas SQL?")
    return {"id": order_id, "delivery_fee": float(fee), "total": float(total)}


# ---------- Owner inbox: handoffs and unanswered questions ----------
@router.get("/api/inbox")
def list_inbox(db: Any = Depends(get_db)) -> list[dict[str, Any]]:
    try:
        rows = owner_inbox.list_items(db)
    except Exception:
        logger.exception("Could not load the owner inbox")
        raise HTTPException(status_code=502, detail="Could not load the questions. Did you run the Phase 6 SQL in Supabase?")
    names: dict[str, str] = {}  # a customer who has ordered before is shown by name
    ids = sorted({r["messenger_id"] for r in rows})
    if ids:
        try:
            found = db.table("customers").select("messenger_id, name").in_("messenger_id", ids).execute().data or []
            names = {c["messenger_id"]: c["name"] for c in found}
        except Exception:
            logger.warning("Could not look up customer names for the inbox")
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "message": r.get("message") or "",
            "status": r["status"],
            "when": _when(r.get("created_at")),
            "customer": names.get(r["messenger_id"], ""),
        }
        for r in rows
    ]


@router.patch("/api/inbox/{item_id}")
def resolve_inbox_item(item_id: int, db: Any = Depends(get_db)) -> dict[str, Any]:
    """Done / Dismiss. For a handoff this also lets the bot answer that customer again."""
    existing = db.table(owner_inbox.TABLE).select("id, kind, status").eq("id", item_id).limit(1).execute().data
    if not existing:
        raise HTTPException(status_code=404, detail="Item not found")
    if existing[0]["status"] == "resolved":
        return {"id": item_id, "status": "resolved"}
    owner_inbox.resolve(db, item_id, "owner", back_notice=existing[0]["kind"] == "handoff")
    return {"id": item_id, "status": "resolved"}


# ---------- shop hours, locked days and the order calendar ----------
ORDER_EXTRAS = (", fulfillment, notes, dedication, delivery_area, delivery_fee", ", fulfillment, notes, dedication", ", fulfillment, notes", "")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
SHOP_SQL_HINT = "Could not read the shop hours. Run the shop hours SQL (section 3 of supabase/schema.sql) first."


def _ph_bounds(first: date, after: date) -> tuple[str, str]:
    """[first, after) as UTC timestamps, where both dates are Philippine calendar days."""
    lo = datetime(first.year, first.month, first.day, tzinfo=PH_TIME)
    hi = datetime(after.year, after.month, after.day, tzinfo=PH_TIME)
    return lo.astimezone(timezone.utc).isoformat(), hi.astimezone(timezone.utc).isoformat()


def _ph_day(value: str | None) -> str | None:
    try:
        return datetime.fromisoformat(value).astimezone(PH_TIME).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _hhmm(value: Any, fallback: str) -> str:
    parsed = shop_hours.parse_time(value)
    return parsed.strftime("%H:%M") if parsed else fallback


def _shop_out(db: Any) -> dict[str, Any]:
    try:
        rows = db.table("shop_settings").select("*").eq("id", 1).limit(1).execute().data or []
    except Exception:
        logger.exception("Could not read shop_settings")
        raise HTTPException(status_code=502, detail=SHOP_SQL_HINT)
    row = rows[0] if rows else {}
    now = datetime.now(PH_TIME)
    status = shop_hours.current_status(db, now)
    return {
        "is_open": row.get("is_open", True) is not False,
        "hours_enabled": bool(row.get("hours_enabled")),
        "open_time": _hhmm(row.get("open_time"), "08:00"),
        "close_time": _hhmm(row.get("close_time"), "20:00"),
        "accepting_orders": status.is_open,
        "closed_because": status.reason,
        "now": now.strftime("%Y-%m-%d %H:%M"),
    }


@router.get("/api/shop")
def get_shop(db: Any = Depends(get_db)) -> dict[str, Any]:
    return _shop_out(db)


class ShopPatch(BaseModel):
    is_open: bool | None = None
    hours_enabled: bool | None = None
    open_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # HH:MM, Philippine time
    close_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


@router.patch("/api/shop")
def update_shop(body: ShopPatch, db: Any = Depends(get_db)) -> dict[str, Any]:
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to update")
    current = _shop_out(db)
    merged = {**current, **changes}
    if merged["close_time"] <= merged["open_time"]:  # HH:MM strings compare correctly; the table requires it too
        raise HTTPException(status_code=400, detail="Closing time must be later than opening time")
    try:
        db.table("shop_settings").upsert(
            {"id": 1, **changes, "updated_at": datetime.now(timezone.utc).isoformat()}, on_conflict="id"
        ).execute()
    except Exception:
        logger.exception("Could not save the shop hours")
        raise HTTPException(status_code=502, detail="Could not save the shop hours")
    return _shop_out(db)


@router.get("/api/calendar")
def calendar_month(month: str, db: Any = Depends(get_db)) -> dict[str, Any]:
    """Per day of the month: how many orders were placed (not cancelled) and whether the day is locked."""
    if not MONTH_RE.match(month):
        raise HTTPException(status_code=400, detail="Month must look like 2026-10")
    year, mon = int(month[:4]), int(month[5:])
    first = date(year, mon, 1)
    after = date(year + (mon == 12), mon % 12 + 1, 1)
    lo, hi = _ph_bounds(first, after)
    try:
        order_rows = db.table("orders").select("order_id, order_date, status").gte("order_date", lo).lt("order_date", hi).limit(5000).execute().data or []
        locked_rows = db.table("closed_days").select("day, reason").gte("day", first.isoformat()).lt("day", after.isoformat()).execute().data or []
    except Exception:
        logger.exception("Could not load the calendar")
        raise HTTPException(status_code=502, detail=SHOP_SQL_HINT)
    days: dict[str, dict[str, Any]] = {}
    for r in order_rows:
        key = _ph_day(r.get("order_date"))
        if key and r.get("status") != "cancelled":
            days.setdefault(key, {"orders": 0, "locked": False, "reason": ""})["orders"] += 1
    for r in locked_rows:
        entry = days.setdefault(r["day"], {"orders": 0, "locked": False, "reason": ""})
        entry["locked"], entry["reason"] = True, r.get("reason") or ""
    return {"month": month, "days": days}


@router.get("/api/calendar/{day}")
def calendar_day(day: date, db: Any = Depends(get_db)) -> dict[str, Any]:
    """The orders placed on one day (Philippine time) and whether that day is locked."""
    lo, hi = _ph_bounds(day, day + timedelta(days=1))

    def fetch(columns: str) -> list[dict[str, Any]]:
        return db.table("orders").select(columns).gte("order_date", lo).lt("order_date", hi).order("order_date").execute().data or []

    shaped = None
    for extra in ORDER_EXTRAS:  # newer columns may not exist yet, like in the Orders tab
        try:
            shaped = [_shape_order(r) for r in fetch(ORDER_SELECT + extra)]
            break
        except Exception:
            if extra:
                logger.warning("Order columns missing (%s); retrying without.", extra.strip(", "))
    if shaped is None:
        logger.exception("Could not load the orders of %s", day)
        raise HTTPException(status_code=502, detail="Could not load the orders")
    try:
        locked = db.table("closed_days").select("day, reason").eq("day", day.isoformat()).limit(1).execute().data or []
    except Exception:
        logger.exception("Could not read closed_days")
        raise HTTPException(status_code=502, detail=SHOP_SQL_HINT)
    return {"day": day.isoformat(), "locked": bool(locked), "reason": (locked[0].get("reason") or "") if locked else "", "orders": shaped}


class LockIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    reason: str | None = Field(default=None, max_length=60)  # shown to customers while the day is locked


@router.put("/api/closed-days/{day}")
def lock_day(day: date, body: LockIn | None = None, db: Any = Depends(get_db)) -> dict[str, Any]:
    if day < datetime.now(PH_TIME).date():
        raise HTTPException(status_code=400, detail="That day has already passed")
    reason = (body.reason if body else None) or None
    try:
        db.table("closed_days").upsert({"day": day.isoformat(), "reason": reason}, on_conflict="day").execute()
    except Exception:
        logger.exception("Could not lock %s", day)
        raise HTTPException(status_code=502, detail="Could not lock the day")
    return {"day": day.isoformat(), "locked": True, "reason": reason or ""}


@router.delete("/api/closed-days/{day}")
def unlock_day(day: date, db: Any = Depends(get_db)) -> dict[str, Any]:
    try:
        db.table("closed_days").delete().eq("day", day.isoformat()).execute()
    except Exception:
        logger.exception("Could not unlock %s", day)
        raise HTTPException(status_code=502, detail="Could not unlock the day")
    return {"day": day.isoformat(), "locked": False, "reason": ""}
