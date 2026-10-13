"""Is the shop taking orders right now?

The owner controls this in the dashboard (Calendar tab):
  * a master switch (shop_settings.is_open),
  * optional daily opening hours (shop_settings.hours_enabled, open_time, close_time), and
  * locked days (closed_days), which close the shop for the whole day.

All times are Philippine time. `evaluate` is a pure function so it is easy to test;
`current_status` reads the database and never raises: if the lookup fails the shop counts
as open, so a database hiccup can't cost the owner orders.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

logger = logging.getLogger("uvicorn.error")

PH_TIME = timezone(timedelta(hours=8))  # the Philippines has no daylight saving
MAX_LOOKAHEAD_DAYS = 366  # how far to look for the next open day


@dataclass(frozen=True)
class ShopStatus:
    is_open: bool = True
    reason: str | None = None  # when closed: "switch", "day" or "hours"
    note: str | None = None  # the owner's note on a locked day (shown to the customer)
    reopens: datetime | None = None  # next opening time in PH time; None = unknown
    timed: bool = False  # does `reopens` carry a real opening time (hours are on)?
    closes: time | None = None  # while open with opening hours on: today's closing time


OPEN = ShopStatus()


def parse_time(value: Any) -> time | None:
    """'08:00' or '08:00:00' (what Postgres returns) -> time; anything else -> None."""
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0, tzinfo=None)
    try:
        return time.fromisoformat(str(value)).replace(second=0, microsecond=0, tzinfo=None)
    except (TypeError, ValueError):
        return None


def _next_open(day: date, locked: dict[date, str | None], opens: time) -> datetime | None:
    """First day from `day` on that is not locked, at the opening time."""
    for _ in range(MAX_LOOKAHEAD_DAYS):
        if day not in locked:
            return datetime.combine(day, opens, tzinfo=PH_TIME)
        day += timedelta(days=1)
    return None


def evaluate(settings: dict[str, Any] | None, locked: dict[date, str | None], now: datetime) -> ShopStatus:
    """Decide from the owner's settings and locked days. `now` is converted to PH time."""
    settings = settings or {}
    now = now.astimezone(PH_TIME)
    if settings.get("is_open") is False:
        return ShopStatus(is_open=False, reason="switch")

    open_t = close_t = None
    if settings.get("hours_enabled"):
        open_t, close_t = parse_time(settings.get("open_time")), parse_time(settings.get("close_time"))
        if open_t is None or close_t is None or close_t <= open_t:
            open_t = close_t = None  # unusable hours must never lock customers out
    timed = open_t is not None
    day_start = open_t if timed else time(0, 0)
    today = now.date()

    if today in locked:
        return ShopStatus(False, "day", locked[today], _next_open(today + timedelta(days=1), locked, day_start), timed)
    if timed:
        if now.time() < open_t:
            return ShopStatus(False, "hours", None, datetime.combine(today, open_t, tzinfo=PH_TIME), True)
        if now.time() >= close_t:  # closing time itself counts as closed
            return ShopStatus(False, "hours", None, _next_open(today + timedelta(days=1), locked, open_t), True)
    return ShopStatus(closes=close_t) if timed else OPEN


def current_status(client: Any, now: datetime | None = None) -> ShopStatus:
    """Read the owner's settings and locked days and evaluate them. Never raises (fails open)."""
    now = (now or datetime.now(PH_TIME)).astimezone(PH_TIME)
    try:
        rows = client.table("shop_settings").select("*").eq("id", 1).limit(1).execute().data or []
        days = client.table("closed_days").select("day, reason").gte("day", now.date().isoformat()).execute().data or []
        locked = {date.fromisoformat(r["day"]): (r.get("reason") or None) for r in days}
        return evaluate(rows[0] if rows else None, locked, now)
    except Exception:
        logger.warning("Shop hours check failed; treating the shop as open")
        return OPEN


def clock(value: time) -> str:
    """08:00 -> '8:00 AM' (no platform-specific strftime flags)."""
    return f"{value.hour % 12 or 12}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"


def reopening_text(status: ShopStatus) -> str | None:
    """'Oct 14, 8:00 AM' (or just 'Oct 14' when hours are off); None when unknown."""
    if status.reopens is None:
        return None
    day = f"{status.reopens:%b} {status.reopens.day}"
    return f"{day}, {clock(status.reopens.time())}" if status.timed else day
