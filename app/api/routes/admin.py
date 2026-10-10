"""Owner dashboard: one HTML page plus a small JSON API for orders and products.

For now there is NO login: the dashboard is open while ADMIN_PASSWORD (in .env) is empty.
Set ADMIN_PASSWORD to switch on HTTP Basic login (username "admin") for the page and the API.
"""
import base64
import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.services import faq_store, owner_inbox
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
