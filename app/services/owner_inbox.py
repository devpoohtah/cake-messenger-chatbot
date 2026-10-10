"""Owner inbox: customers who asked for the owner, and questions the bot could not answer.

While a handoff is open the bot stays quiet for that customer, so it does not talk over the
owner. The owner replies in the Facebook Page inbox (Meta Business Suite). A handoff ends when
the owner presses Done in the dashboard, when the customer taps a bot button, or after
HANDOFF_HOURS, so a forgotten handoff never leaves a customer ignored.

Every function here is safe to call if the owner_inbox table does not exist yet: callers
catch the error and the bot carries on answering normally.
"""
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("uvicorn.error")

TABLE = "owner_inbox"
HANDOFF_HOURS = 12  # placeholder: how long the bot stays quiet before it answers again
MAX_MESSAGE_LENGTH = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def latest_handoff(client: Any, messenger_id: str) -> dict[str, Any] | None:
    rows = (
        client.table(TABLE).select("*").eq("messenger_id", messenger_id).eq("kind", "handoff")
        .order("id", desc=True).limit(1).execute().data
    )
    return rows[0] if rows else None


def is_expired(row: dict[str, Any], now: datetime | None = None) -> bool:
    """True when the handoff is older than HANDOFF_HOURS. An unreadable date counts as expired."""
    try:
        created = datetime.fromisoformat(str(row["created_at"]))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return True
    age = (now or datetime.now(timezone.utc)) - created
    return age.total_seconds() > HANDOFF_HOURS * 3600


def open_handoff(client: Any, messenger_id: str, message: str) -> bool:
    try:
        client.table(TABLE).insert(
            {"messenger_id": messenger_id, "kind": "handoff", "message": message[:MAX_MESSAGE_LENGTH]}
        ).execute()
        return True
    except Exception:
        logger.warning("Could not save the owner handoff; did you run the Phase 6 SQL?")
        return False


def add_unanswered(client: Any, messenger_id: str, message: str) -> None:
    if not message.strip():
        return
    try:
        client.table(TABLE).insert(
            {"messenger_id": messenger_id, "kind": "unanswered", "message": message[:MAX_MESSAGE_LENGTH]}
        ).execute()
    except Exception:
        logger.warning("Could not save the unanswered question; did you run the Phase 6 SQL?")


def touch(client: Any, row_id: int, message: str) -> None:
    """The customer wrote again while waiting: keep their latest message visible to the owner."""
    if message.strip():
        client.table(TABLE).update({"message": message[:MAX_MESSAGE_LENGTH]}).eq("id", row_id).execute()


def resolve(client: Any, row_id: int, by: str, back_notice: bool = False) -> list[dict[str, Any]]:
    """Close an item. back_notice=True makes the bot tell the customer it is back on their next message."""
    return (
        client.table(TABLE)
        .update({"status": "resolved", "resolved_by": by, "resolved_at": _now(), "back_notice_pending": back_notice})
        .eq("id", row_id).execute().data
    )


def clear_back_notice(client: Any, row_id: int) -> None:
    client.table(TABLE).update({"back_notice_pending": False}).eq("id", row_id).execute()


def list_items(client: Any, limit: int = 100) -> list[dict[str, Any]]:
    return client.table(TABLE).select("*").order("id", desc=True).limit(limit).execute().data or []
