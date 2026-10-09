"""Shop FAQ topics, edited by the owner in the dashboard and stored in Supabase.

The bot reads them from here, cached for a minute. If the table is empty or cannot be
read, it uses the built-in answers in faq.py, so the bot never goes silent.
"""
import logging
import time
from typing import Any

from app.services.faq import FAQS as DEFAULT_FAQS

logger = logging.getLogger("uvicorn.error")
CACHE_SECONDS = 60
_cache: dict[str, Any] = {"at": 0.0, "faqs": None}


def rows_to_faqs(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Turn database rows into the same shape as faq.py: {topic: {description, en, tl, hil}}."""
    faqs: dict[str, dict[str, str]] = {}
    for r in rows:
        entry = {"description": r["description"], "en": r["answer_en"]}
        if r.get("answer_tl"):
            entry["tl"] = r["answer_tl"]
        if r.get("answer_hil"):
            entry["hil"] = r["answer_hil"]
        faqs[r["topic"]] = entry
    return faqs


def load_faqs(client: Any) -> dict[str, dict[str, str]]:
    now = time.monotonic()
    if _cache["faqs"] is not None and now - _cache["at"] < CACHE_SECONDS:
        return _cache["faqs"]
    try:
        rows = client.table("faqs").select("topic, description, answer_en, answer_tl, answer_hil").order("topic").execute().data or []
        faqs = rows_to_faqs(rows) or DEFAULT_FAQS
    except Exception:
        logger.warning("Could not load FAQs from the database; using faq.py")
        faqs = DEFAULT_FAQS
    _cache["at"], _cache["faqs"] = now, faqs
    return faqs


def clear_cache() -> None:
    """Called after the dashboard saves, so the bot sees the change straight away."""
    _cache["at"], _cache["faqs"] = 0.0, None