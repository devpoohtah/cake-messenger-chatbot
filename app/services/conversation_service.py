"""Guided order flow for Messenger.

Buttons (payloads) drive the flow without the AI; the AI only interprets typed
text. This module validates everything, writes every reply (prices and totals
always come from the database), and creates an order only after Confirm.
"""
import logging
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from app.schemas.order import _PHONE_RE, OrderRequest
from app.services import order_service as orders
from app.services.ai_service import AIResult, AIService, Intent
from app.services.faq import FAQS
from app.services.messages import t

logger = logging.getLogger("uvicorn.error")

IDLE = "idle"
COLLECTING = "collecting_details"
AWAITING = "awaiting_confirmation"

CANCEL_WORDS = {"cancel", "stop"}
NO_WORDS = {"no", "hindi"}  # cancel only while waiting for confirmation
CONFIRM_WORDS = {"yes", "y", "confirm", "confirmed", "ok", "okay", "oo", "sige"}
MORE_WORDS = {"yes", "y", "another", "add", "more", "add another", "oo"}
DONE_WORDS = {"done", "thats all", "that is all", "no", "nope", "none", "enough", "wala na", "tapos na", "ok", "okay"}
THANKS_WORDS: dict[str, str | None] = {  # exact message -> language it implies (None = ambiguous)
    "thanks": "en", "thank you": "en", "thankyou": "en", "thank u": "en", "ty": "en", "tysm": "en",
    "thanks a lot": "en", "bye": "en", "goodbye": "en",
    "salamat": None, "salamat po": "tl", "maraming salamat": "tl", "maraming salamat po": "tl",
    "daghang salamat": "hil", "daghan salamat": "hil", "salamat gid": "hil",
}
LANGUAGES = {"en", "tl", "hil"}
QUESTION_INTENTS = {
    Intent.GREETING, Intent.PRODUCT_QUESTION, Intent.PRICE_QUESTION, Intent.ORDERING_INFO, Intent.FAQ_QUESTION,
}
CONTACT_FIELDS = ("customer_name", "contact_number", "location")


@dataclass
class Reply:
    """One outgoing message. products -> carousel; options/ask_phone -> quick replies."""

    text: str = ""
    options: list[tuple[str, str]] = field(default_factory=list)
    ask_phone: bool = False
    products: list[dict[str, Any]] | None = None
    carousel_button: str = "Order this"


def _peso(value: Any) -> str:
    return f"₱{Decimal(str(value)):,.2f}"


def _norm(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text).strip().lower()

def _plain(replies: list[Reply]) -> list[Reply]:
    """Drop buttons from side-answers given in the middle of an order."""
    return [replace(r, options=[]) for r in replies]

def _new_draft(lang: str = "en") -> dict[str, Any]:
    return {"cart": [], "lang": lang}


def _find_product(name: str | None, products: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not name:
        return None
    wanted = name.strip().lower()
    return next((p for p in products if p["name"].strip().lower() == wanted), None)


def _find_product_by_id(raw: str, products: list[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        pid = int(raw)
    except ValueError:
        return None
    return next((p for p in products if int(p["product_id"]) == pid), None)


def _clean_fields(result: AIResult, products: list[dict[str, Any]], lang: str) -> tuple[dict[str, Any], str | None]:
    """Validate what the AI extracted. Anything invalid is dropped, never trusted."""
    fields: dict[str, Any] = {}
    notice = None
    if result.product:
        product = _find_product(result.product, products)
        if product and product.get("is_available"):
            fields["product"] = product["name"]
        elif product:
            notice = t(lang, "product_unavailable", name=product["name"])
    if result.quantity is not None:
        try:
            fields["quantity"] = orders.validate_quantity(result.quantity)
        except orders.InvalidQuantityError as exc:
            notice = str(exc)
    if result.customer_name and 1 <= len(result.customer_name.strip()) <= 100:
        fields["customer_name"] = result.customer_name.strip()
    if result.contact_number and _PHONE_RE.match(result.contact_number.strip()):
        fields["contact_number"] = result.contact_number.strip()
    if result.location and 1 <= len(result.location.strip()) <= 200:
        fields["location"] = result.location.strip()
    return fields, notice


def _fallback_for_asked_field(asked: str | None, text: str, intent: Intent) -> dict[str, Any]:
    """If the AI extracted nothing, treat a plain reply as the answer to the question just asked."""
    t_ = text.strip()
    if asked in ("customer_name", "location") and t_ and intent in (Intent.UNKNOWN, Intent.PROVIDE_ORDER_DETAILS):
        if len(t_) <= (100 if asked == "customer_name" else 200):
            return {asked: t_}
    return {}


class ConversationService:
    def __init__(self, ai: AIService, client: Any) -> None:
        self._ai = ai
        self._client = client
        self._text = ""
    # ---------- storage ----------
    def _load(self, messenger_id: str) -> tuple[str, dict[str, Any]]:
        rows = (
            self._client.table("conversations").select("*").eq("messenger_id", messenger_id).limit(1).execute().data
        )
        if not rows:
            return IDLE, _new_draft()
        state, draft = rows[0]["state"], dict(rows[0].get("draft") or {})
        if "cart" not in draft:  # draft saved by an older version of the flow
            return IDLE, _new_draft(draft.get("lang", "en")) | {"last_mid": draft.get("last_mid")}
        return state, draft

    def _save(self, messenger_id: str, state: str, draft: dict[str, Any]) -> None:
        self._client.table("conversations").upsert(
            {"messenger_id": messenger_id, "state": state, "draft": draft}, on_conflict="messenger_id"
        ).execute()

    def _products(self) -> list[dict[str, Any]]:
        return self._client.table("products").select("*").order("product_id").execute().data or []

    # ---------- entry point ----------
    def handle(
        self, sender_id: str, text: str, message_id: str | None = None, payload: str | None = None
    ) -> list[Reply] | None:
        """Return the replies to send, or None if this message was already processed (Meta retry)."""
        state, draft = self._load(sender_id)
        if message_id and draft.get("last_mid") == message_id:
            return None
        replies, state, draft = self._process(sender_id, text, payload, state, draft)
        if message_id:
            draft["last_mid"] = message_id  # survives resets, so a retried Confirm can't order twice
        self._save(sender_id, state, draft)
        return replies

    # ---------- flow ----------
    def _process(self, sender_id: str, text: str, payload: str | None, state: str, draft: dict[str, Any]):
        products = self._products()
        available = [p for p in products if p.get("is_available")]
        self._text = text
        lang = draft.get("lang", "en")
        words = _norm(text)

        if payload:
            outcome = self._handle_payload(sender_id, payload, state, draft, products, available)
            if outcome is not None:
                return outcome

        if state != IDLE and words in CANCEL_WORDS:
            return self._cancelled(lang)
        if state == AWAITING:
            if words in CONFIRM_WORDS:
                return self._place_order(sender_id, draft, products)
            if words in NO_WORDS:
                return self._cancelled(lang)
        if words in THANKS_WORDS:  # fixed reply, no AI call
            draft = {**draft, "lang": THANKS_WORDS[words] or lang}
            return self._thanks(state, draft, products, available)
        if state == COLLECTING and draft.get("asking") == "add_more":
            if words in MORE_WORDS:
                return self._advance({**draft, "cart_done": False, "pending": None, "force_product": True}, products, available)
            if words in DONE_WORDS:
                return self._advance({**draft, "cart_done": True}, products, available)
        if state == COLLECTING and draft.get("asking") == "quantity" and draft.get("pending") and text.strip().isdigit():
            return self._add_to_cart(draft, draft["pending"], int(text.strip()), products, available)
        if state == COLLECTING and draft.get("asking") == "contact_number" and _PHONE_RE.match(text.strip()):
            return self._advance({**draft, "contact_number": text.strip()}, products, available)

        topics = {topic: row["description"] for topic, row in FAQS.items()}
        result = self._ai.interpret_message(text, state, [p["name"] for p in available], topics)

        if result.language in LANGUAGES:
            lang = result.language
            draft = {**draft, "lang": lang}
        if result.intent == Intent.THANKS:
            return self._thanks(state, draft, products, available)
        if state != IDLE and result.intent == Intent.CANCEL_ORDER:
            return self._cancelled(lang)
        if state == AWAITING and result.intent == Intent.CONFIRM_ORDER:
            return self._place_order(sender_id, draft, products)

        fields, notice = _clean_fields(result, products, lang)
        if not fields and state == COLLECTING:
            fields = _fallback_for_asked_field(draft.get("asking"), text, result.intent)
        wants_order = result.intent in (Intent.START_ORDER, Intent.PROVIDE_ORDER_DETAILS)

        if state == IDLE:
            if wants_order:
                return self._advance(self._merge(_new_draft(lang), fields), products, available, notice)
            return self._answer(result, draft, products, available), IDLE, draft

        if state == COLLECTING:
            if fields or wants_order:
                return self._advance(self._merge(draft, fields), products, available, notice)
            nxt = self._advance(draft, products, available)
            return _plain(self._answer(result, draft, products, available)) + nxt[0], COLLECTING, nxt[2]

        # AWAITING confirmation
        if any(k in fields for k in CONTACT_FIELDS):
            return self._advance(self._merge(draft, fields), products, available, notice)
        if result.intent in QUESTION_INTENTS:
            answer = _plain(self._answer(result, draft, products, available))
            return answer + self._summary(draft, products, available)[0], AWAITING, draft
        return [self._confirm_prompt(lang, "awaiting_hint")], AWAITING, draft

    def _thanks(self, state: str, draft: dict[str, Any], products, available):
        lang = draft.get("lang", "en")
        reply = [Reply(text=t(lang, "thanks"))]
        if state == COLLECTING:  # keep the order moving: repeat the question we were on
            nxt = self._advance(draft, products, available)
            return reply + nxt[0], COLLECTING, nxt[2]
        if state == AWAITING:
            return reply + self._summary(draft, products, available)[0], AWAITING, draft
        return reply, state, draft

    def _log_unanswered(self) -> None:
        """Print questions the bot could not answer, so you know which FAQs to write."""
        if self._text.strip():
            logger.warning("Unanswered question: %r", self._text[:300])

    def _merge(self, draft: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
        """Apply validated fields from typed text to the draft."""
        draft = {**draft, "cart": list(draft.get("cart", []))}
        product, quantity = fields.get("product"), fields.get("quantity")
        if product and quantity:
            draft = self._cart_set(draft, product, quantity)
        elif product:
            draft["pending"] = product
        elif quantity and draft.get("pending"):
            draft = self._cart_set(draft, draft["pending"], quantity)
        for key in CONTACT_FIELDS:
            if key in fields:
                draft[key] = fields[key]
        if any(k in fields for k in CONTACT_FIELDS) and draft["cart"] and not draft.get("pending"):
            draft["cart_done"] = True
        return draft

    @staticmethod
    def _cart_set(draft: dict[str, Any], product: str, quantity: int) -> dict[str, Any]:
        cart = [i for i in draft["cart"] if i["product"] != product] + [{"product": product, "quantity": quantity}]
        return {**draft, "cart": cart, "pending": None, "cart_done": False}

    def _add_to_cart(self, draft, product: str, quantity: int, products, available):
        try:
            orders.validate_quantity(quantity)
        except orders.InvalidQuantityError as exc:
            return self._advance(draft, products, available, str(exc))
        return self._advance(self._cart_set(draft, product, quantity), products, available)

    # ---------- button payloads ----------
    def _handle_payload(self, sender_id, payload, state, draft, products, available):
        lang = draft.get("lang", "en")
        kind, _, arg = payload.partition(":")
        base = draft if state != IDLE else _new_draft(lang)

        if kind == "START_ORDER":
            return self._advance({**base, "pending": None}, products, available)
        if kind == "MENU":
            return self._menu_replies(lang, available), state, draft
        if kind == "ORDER":
            product = _find_product_by_id(arg, products)
            if product is None or not product.get("is_available"):
                name = product["name"] if product else ""
                notice = t(lang, "product_unavailable", name=name) if product else None
                return self._advance({**base, "pending": None, "force_product": True}, products, available, notice)
            return self._advance({**base, "pending": product["name"], "cart_done": False}, products, available)
        if kind == "QTY":
            if state != COLLECTING or not draft.get("pending"):
                return [Reply(text=t(lang, "fallback"), options=self._start_options(lang))], state, draft
            try:
                quantity = int(arg)
            except ValueError:
                return self._advance(draft, products, available)
            return self._add_to_cart(draft, draft["pending"], quantity, products, available)
        if kind == "ADD_MORE":
            return self._advance({**base, "cart_done": False, "pending": None, "force_product": True}, products, available)
        if kind == "DONE_ADDING":
            if state != COLLECTING:
                return [Reply(text=t(lang, "fallback"), options=self._start_options(lang))], state, draft
            return self._advance({**draft, "cart_done": True}, products, available)
        if kind == "CONFIRM":
            if state != AWAITING:  # stale button: never create an order from it
                return [Reply(text=t(lang, "nothing_to_confirm"), options=self._start_options(lang))], state, draft
            return self._place_order(sender_id, draft, products)
        if kind == "CANCEL":
            return self._cancelled(lang)
        return None  # unknown payload (e.g. the phone-number button): handle its text instead

    # ---------- replies ----------
    @staticmethod
    def _start_options(lang: str) -> list[tuple[str, str]]:
        return [(t(lang, "btn_order_now"), "START_ORDER"), (t(lang, "btn_menu"), "MENU")]

    @staticmethod
    def _confirm_prompt(lang: str, key: str) -> Reply:
        return Reply(text=t(lang, key), options=[(t(lang, "btn_confirm"), "CONFIRM"), (t(lang, "btn_cancel"), "CANCEL")])

    @staticmethod
    def _menu_replies(lang: str, available) -> list[Reply]:
        if not available:
            return [Reply(text=t(lang, "menu_empty"))]
        return [Reply(text=t(lang, "menu_intro"), products=available, carousel_button=t(lang, "btn_order_this"))]

    def _cancelled(self, lang: str):
        return [Reply(text=t(lang, "cancelled"), options=self._start_options(lang))], IDLE, _new_draft(lang)

    def _answer(self, result: AIResult, draft, products, available) -> list[Reply]:
        lang = draft.get("lang", "en")
        if result.intent == Intent.GREETING:
            return [Reply(text=t(lang, "greeting"), options=self._start_options(lang))]
        if result.intent in (Intent.PRODUCT_QUESTION, Intent.PRICE_QUESTION):
            product = _find_product(result.product, products)
            if product is None:
                return self._menu_replies(lang, available)
            if not product.get("is_available"):
                return [Reply(text=t(lang, "product_unavailable", name=product["name"]))] + self._menu_replies(lang, available)
            desc = f"\n{product['description']}" if product.get("description") else ""
            text = t(lang, "price_line", name=product["name"], price=_peso(product["price"]), desc=desc)
            return [Reply(text=text, options=[(t(lang, "btn_order_this"), f"ORDER:{product['product_id']}"), (t(lang, "btn_menu"), "MENU")])]
        if result.intent == Intent.ORDERING_INFO:
            return [Reply(text=t(lang, "ordering_info"), options=self._start_options(lang))]
        if result.intent == Intent.FAQ_QUESTION:
            row = FAQS.get(result.faq_topic or "")
            answer = row and (row.get(lang) or row["en"])
            if answer:  # the answer is text YOU wrote in faq.py, never AI-generated
                return [Reply(text=answer, options=self._start_options(lang))]
            self._log_unanswered()
            return [Reply(text=t(lang, "faq_unknown"), options=self._start_options(lang))]
        self._log_unanswered()
        return [Reply(text=t(lang, "fallback"), options=self._start_options(lang))]

    def _cart_text(self, cart: list[dict[str, Any]], products) -> str:
        rows = []
        for item in cart:
            p = _find_product(item["product"], products)
            if p:
                subtotal = orders.calculate_subtotal(p["price"], item["quantity"])
                rows.append(f"• {item['quantity']} × {p['name']} — {_peso(subtotal)}")
        return "\n".join(rows)

    def _advance(self, draft: dict[str, Any], products, available, notice: str | None = None):
        """Ask for whatever is missing next, or show the summary when everything is present."""
        lang = draft.get("lang", "en")
        draft = {**draft}
        cart = draft.get("cart") or []
        force_product = draft.pop("force_product", False)

        if draft.get("pending"):
            draft["asking"] = "quantity"
            replies = [Reply(text=t(lang, "ask_quantity", name=draft["pending"]),
                             options=[(str(n), f"QTY:{n}") for n in range(1, 6)])]
        elif not cart or force_product:
            draft["asking"] = "product"
            if available:
                replies = [Reply(text=t(lang, "ask_product"), products=available, carousel_button=t(lang, "btn_order_this"))]
            else:
                replies = [Reply(text=t(lang, "menu_empty"))]
        elif not draft.get("cart_done"):
            draft["asking"] = "add_more"
            replies = [Reply(text=t(lang, "ask_add_more", cart=self._cart_text(cart, products)),
                             options=[(t(lang, "btn_add_more"), "ADD_MORE"), (t(lang, "btn_done"), "DONE_ADDING")])]
        else:
            missing = next((f for f in CONTACT_FIELDS if not draft.get(f)), None)
            if missing is None:
                return self._summary(draft, products, available)
            draft["asking"] = missing
            key = {"customer_name": "ask_name", "contact_number": "ask_contact", "location": "ask_location"}[missing]
            replies = [Reply(text=t(lang, key), ask_phone=(missing == "contact_number"))]

        if notice and replies and replies[0].text:
            replies[0] = replace(replies[0], text=f"{notice} {replies[0].text}")
        return replies, COLLECTING, draft

    # ---------- summary and ordering ----------
    def _build_request(self, draft: dict[str, Any], messenger_id: str | None, confirmed: bool) -> OrderRequest:
        return OrderRequest(
            customer_name=draft["customer_name"],
            contact_number=draft["contact_number"],
            messenger_id=messenger_id,
            location=draft["location"],
            items=[{"product_name": i["product"], "quantity": i["quantity"]} for i in draft["cart"]],
            confirmed=confirmed,
        )

    def _summary(self, draft: dict[str, Any], products, available):
        lang = draft.get("lang", "en")
        try:
            lines = orders.build_order_lines(self._build_request(draft, None, confirmed=False), products)
        except ValueError:
            fresh = _new_draft(lang)
            nxt = self._advance(fresh, products, available)
            return [Reply(text=t(lang, "invalid_order"))] + nxt[0], COLLECTING, nxt[2]
        items = "\n".join(f"• {l.quantity} × {l.product_name} — {_peso(l.subtotal)}" for l in lines)
        text = t(lang, "summary", items=items, total=_peso(orders.calculate_total(lines)),
                 name=draft["customer_name"], contact=draft["contact_number"], location=draft["location"])
        draft = {k: v for k, v in draft.items() if k not in ("asking", "pending", "force_product")}
        reply = Reply(text=text, options=[(t(lang, "btn_confirm"), "CONFIRM"), (t(lang, "btn_cancel"), "CANCEL")])
        return [reply], AWAITING, draft

    def _place_order(self, sender_id: str, draft: dict[str, Any], products):
        lang = draft.get("lang", "en")
        try:
            order = orders.create_order(self._build_request(draft, sender_id, confirmed=True), client=self._client)
        except orders.OrderCreationError:
            return [self._confirm_prompt(lang, "order_failed_retry")], AWAITING, draft
        except ValueError as exc:  # e.g. a cake became unavailable since the customer chose it
            logger.warning("Order rejected at confirmation: %s", type(exc).__name__)
            return [Reply(text=t(lang, "order_rejected"), options=self._start_options(lang))], IDLE, _new_draft(lang)
        items = "\n".join(f"• {i.quantity} × {i.product_name}" for i in order.items)
        text = t(lang, "order_received", order_id=order.order_id, items=items,
                 total=_peso(order.total_amount), status=order.status)
        return [Reply(text=text, options=self._start_options(lang))], IDLE, _new_draft(lang)


_ai_service: AIService | None = None


def handle_incoming_message(
    sender_id: str, text: str, message_id: str | None = None, payload: str | None = None
) -> list[Reply] | None:
    """Called by the webhook. Never raises, so Meta always gets a 200."""
    global _ai_service
    try:
        from app.db.supabase import get_supabase_client

        if _ai_service is None:
            from app.services.ai_service import GeminiAIService

            _ai_service = GeminiAIService()
        return ConversationService(_ai_service, get_supabase_client()).handle(sender_id, text, message_id, payload)
    except Exception:
        logger.exception("Conversation handling failed")
        return [Reply(text=t("en", "error"))]


def deliver_replies(sender_id: str, replies: list[Reply] | None, messenger: Any | None = None) -> None:
    """Send replies through Messenger: carousel, quick replies, or plain text."""
    if not replies:
        return
    if messenger is None:
        from app.services.messenger_service import MessengerService

        messenger = MessengerService()
    for r in replies:
        if r.products:
            if r.text:
                messenger.send_text(sender_id, r.text)
            messenger.send_product_carousel(sender_id, r.products, r.carousel_button)
        elif r.options or r.ask_phone:
            messenger.send_quick_replies(sender_id, r.text, r.options, r.ask_phone)
        elif r.text:
            messenger.send_text(sender_id, r.text)