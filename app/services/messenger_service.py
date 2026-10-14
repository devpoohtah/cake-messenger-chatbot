"""Messenger service: parse incoming events and send messages via the Send API."""
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import get_settings

GRAPH_API_VERSION = "v25.0"
logger = logging.getLogger("uvicorn.error")

MAX_QUICK_REPLIES = 13  # Meta limit
MAX_CAROUSEL_ELEMENTS = 10  # Meta limit
LOW_STOCK_AT = 5  # a cake card says "Only N left" at or below this stock (blank stock = unlimited)
QUICK_REPLY_TITLE_MAX = 20  # kept short on purpose: Meta truncates long titles


@dataclass(frozen=True)
class IncomingMessage:
    sender_id: str
    page_id: str
    text: str
    message_id: str | None
    payload: str | None = None  # set when the customer tapped a quick reply or postback button
    attachment: str | None = None  # a photo, voice message, file, location or sticker instead of text: image, audio, video, file, location, sticker


def extract_text_messages(payload: dict) -> list[IncomingMessage]:
    """Pull customer messages out of a Messenger webhook payload.

    Handles typed text, quick-reply taps (message.quick_reply.payload) and
    postback button taps (postback.payload). Echoes and other events are skipped.
    """
    found: list[IncomingMessage] = []
    if payload.get("object") != "page":
        return found
    for entry in payload.get("entry", []):
        for event in entry.get("messaging", []):
            sender_id = (event.get("sender") or {}).get("id")
            message = event.get("message") or {}
            postback = event.get("postback") or {}
            if not sender_id or message.get("is_echo"):
                continue

            if postback:
                text = postback.get("title") or ""
                button_payload = postback.get("payload")
                message_id = postback.get("mid")
            else:
                text = message.get("text") or ""
                button_payload = (message.get("quick_reply") or {}).get("payload")
                message_id = message.get("mid")

            attachment = None
            if not postback:
                attachments = message.get("attachments") or []
                if attachments and not text and button_payload is None:
                    first = attachments[0] or {}
                    attachment = "sticker" if (first.get("payload") or {}).get("sticker_id") else str(first.get("type") or "file")
            if not text and button_payload is None and attachment is None:
                continue
            found.append(
                IncomingMessage(
                    sender_id=str(sender_id),
                    page_id=str((event.get("recipient") or {}).get("id", "")),
                    text=text,
                    message_id=message_id,
                    payload=None if button_payload is None else str(button_payload),
                    attachment=attachment,
                )
            )
    return found


# ---------- message builders (pure functions, easy to test) ----------

def build_quick_replies_message(
    text: str, options: list[tuple[str, str]], ask_phone: bool = False
) -> dict[str, Any]:
    """options: list of (button title, payload). ask_phone adds Messenger's phone-number button."""
    replies: list[dict[str, Any]] = [
        {"content_type": "text", "title": title[:QUICK_REPLY_TITLE_MAX], "payload": payload}
        for title, payload in options
    ]
    if ask_phone:
        replies.append({"content_type": "user_phone_number"})
    return {"text": text, "quick_replies": replies[:MAX_QUICK_REPLIES]}


def build_product_carousel_message(
    products: list[dict[str, Any]], button_title: str = "Order this", low_stock_label: str = ""
) -> dict[str, Any]:
    elements = []
    for p in products[:MAX_CAROUSEL_ELEMENTS]:
        price = f"₱{Decimal(str(p['price'])):,.2f}"
        stock = p.get("stock")
        hint = low_stock_label.replace("{n}", str(stock)) if low_stock_label and stock is not None and 0 < stock <= LOW_STOCK_AT else ""
        head = f"{price} · {hint}" if hint else price  # the hint comes first so the 80-character limit never cuts it
        subtitle = f"{head} — {p['description']}" if p.get("description") else head
        elements.append(
            {
                "title": p["name"][:80],  # Meta limit: 80 characters
                "subtitle": subtitle[:80],
                **({"image_url": p["image_url"]} if p.get("image_url") else {}),  # public https link
                "buttons": [
                    {
                        "type": "postback",
                        "title": button_title,
                        "payload": f"ORDER:{p['product_id']}",
                    }
                ],
            }
        )
    return {
        "attachment": {
            "type": "template",
            "payload": {"template_type": "generic", "elements": elements},
        }
    }


class MessengerService:
    """Sends messages with the Page Access Token from settings (never logged)."""

    def _post_message(self, recipient_id: str, message: dict[str, Any]) -> bool:
        token = get_settings().meta_page_access_token.get_secret_value()
        if not token:
            logger.error("META_PAGE_ACCESS_TOKEN is not set; cannot send message.")
            return False
        try:
            response = httpx.post(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages",
                params={"access_token": token},
                json={
                    "messaging_type": "RESPONSE",
                    "recipient": {"id": recipient_id},
                    "message": message,
                },
                timeout=10,
            )
        except httpx.HTTPError as exc:
            # Log only the exception type: the full message can include the URL with the token.
            logger.error("Send API request failed: %s", type(exc).__name__)
            return False
        if response.status_code != 200:
            logger.error("Send API error %s: %s", response.status_code, response.text)
            return False
        return True

    def send_action(self, recipient_id: str, action: str) -> bool:
        """Send 'mark_seen', 'typing_on' or 'typing_off'. Never raises: these are only polish."""
        token = get_settings().meta_page_access_token.get_secret_value()
        if not token:
            return False
        try:
            response = httpx.post(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages",
                params={"access_token": token},
                json={"recipient": {"id": recipient_id}, "sender_action": action},
                timeout=3,
            )
        except httpx.HTTPError as exc:
            logger.warning("Sender action %s failed: %s", action, type(exc).__name__)
            return False
        if response.status_code != 200:
            logger.warning("Sender action %s error %s", action, response.status_code)
            return False
        return True

    def send_text(self, recipient_id: str, text: str) -> bool:
        return self._post_message(recipient_id, {"text": text})

    def send_quick_replies(
        self, recipient_id: str, text: str, options: list[tuple[str, str]], ask_phone: bool = False
    ) -> bool:
        return self._post_message(recipient_id, build_quick_replies_message(text, options, ask_phone))

    def send_product_carousel(
        self, recipient_id: str, products: list[dict[str, Any]], button_title: str = "Order this", low_stock_label: str = ""
    ) -> bool:
        return self._post_message(recipient_id, build_product_carousel_message(products, button_title, low_stock_label))
