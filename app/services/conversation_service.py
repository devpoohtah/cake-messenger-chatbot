"""Guided order flow for Messenger.

Buttons (payloads) drive the flow without the AI; the AI only interprets typed
text. This module validates everything, writes every reply (prices and totals
always come from the database), and creates an order only after Confirm.
"""
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from app.schemas.order import _PHONE_RE, OrderRequest
from app.services import order_service as orders
from app.services.ai_service import AIResult, AIService, AIUnavailableError, Intent
from app.services import owner_inbox, shop_hours
from app.services.faq_store import load_faqs
from app.services.messages import MESSAGES, t
from app.services.messenger_service import LOW_STOCK_AT

logger = logging.getLogger("uvicorn.error")

IDLE = "idle"
COLLECTING = "collecting_details"
AWAITING = "awaiting_confirmation"

CANCEL_WORDS = {"cancel", "stop", "kansela"}
TALK_OWNER = "TALK_OWNER"  # payload of the "Talk to the owner" button and menu item
# Typed ways of asking for the owner (compared after _norm: lowercase, no punctuation). Short messages only.
OWNER_WORDS = {"owner", "the owner", "human", "tao", "may ari", "mayari", "tagiya", "tag iya"}
OWNER_PHRASES = (
    "talk to the owner", "talk to owner", "speak to the owner", "speak to owner", "talk to a person",
    "talk to a human", "speak to a human", "speak to a person", "real person", "human agent",
    "kausapin ang owner", "makausap ang owner", "makausap ang may ari", "makausap ko ang may ari",
    "kausapin ang may ari", "makipagusap sa owner", "makipagusap sa may ari",
    "kausapon ang owner", "kausapon ko ang owner", "kausapon ang tagiya", "makigistorya sa owner",
    "makigestorya sa owner", "makigsulti sa owner", "makigistorya sa tagiya",
)
NO_WORDS = {"no", "hindi", "indi", "ayaw"}  # cancel only while waiting for confirmation
# Typed confirmation is deliberately strict: "ok", "sige" and "oo" are often just
# acknowledgements (for example after an FAQ answer) and must never place an order.
CONFIRM_WORDS = {"yes", "y", "confirm", "confirmed", "yes po", "confirm po", "huo", "huo po"}
MORE_WORDS = {"yes", "y", "another", "add", "more", "add another", "oo", "huo"}
DONE_WORDS = {"done", "thats all", "that is all", "no", "nope", "none", "enough", "wala na", "tapos na", "ok", "okay", "indi"}
BYE_WORDS = {"bye", "goodbye", "bye bye", "babay", "paalam"}
DELIVERY_FEE = Decimal("50")  # flat fee, used only while the owner has set no delivery areas
DRAFT_EXPIRY_HOURS = 24  # an unfinished order is cleared after this long without a message from the customer
SHOW_DISCLAIMER = True  # add the "automated assistant" note to the greeting and to FAQ answers
QUOTE_MAX_ITEMS = 5  # cakes quoted in one price question
QUOTE_MAX_QTY = 99  # a bigger number is treated as "no quantity given"
NOTE_EVERY_HOURS = 24  # the "automated assistant" note is added at most this often per customer
BEST_SELLER_DAYS = 30  # the best seller is counted over this many days of orders
BEST_SELLER_MIN_SOLD = 10  # with fewer cakes sold than this, the bot does not name a best seller
PICKUP_LOCATION = "Pickup at shop"  # stored in orders.location for pick-up orders
MAX_CAKES_PER_ORDER = 20  # all cakes together; the per-cake cap is MAX_QUANTITY_PER_ITEM in order_service
MAX_NOTES_LENGTH = 200
MAX_DEDICATION_LENGTH = 80  # the message on the dedication card
# Delivery address parts. Together they stay under 200 characters, the limit for the saved address.
MAX_STREET_LENGTH = 100  # house number, street or purok, and barangay
MIN_STREET_LENGTH = 8
MAX_AREA_LENGTH = 50  # a city or municipality typed by the customer (listed areas have at most 20 characters)
MAX_LANDMARK_LENGTH = 50
# "oo" after a price quote starts an order. Safe to be loose: it never places one.
QUOTE_YES_WORDS = {"yes", "y", "yes po", "oo", "oo po", "huo", "huo po", "sige", "ok", "okay", "sure", "gusto", "gusto ko", "order"}
SKIP_WORDS = {"skip", "no", "none", "nope", "wala", "wala po", "wala na", "pass", "next"}
THANKS_WORDS: dict[str, str | None] = {  # exact message -> language it implies (None = ambiguous)
    "thanks": "en", "thank you": "en", "thankyou": "en", "thank u": "en", "ty": "en", "tysm": "en",
    "thanks a lot": "en", "bye": "en", "goodbye": "en", "bye bye": "en", "babay": None, "paalam": "tl",
    "salamat": None, "salamat po": "tl", "maraming salamat": "tl", "maraming salamat po": "tl",
    "daghang salamat": "hil", "daghan salamat": "hil", "salamat gid": "hil",
}
LANGUAGES = {"en", "tl", "hil"}
QUESTION_INTENTS = {
    Intent.GREETING, Intent.PRODUCT_QUESTION, Intent.PRICE_QUESTION, Intent.ORDERING_INFO, Intent.FAQ_QUESTION,
    Intent.ORDER_STATUS, Intent.NOT_SOLD, Intent.SMALL_TALK, Intent.ABOUT_BOT,
}

def _wants_owner(words: str) -> bool:
    """A short typed message that clearly asks for the owner. Longer messages go to the AI instead."""
    if not words or len(words.split()) > 10:
        return False
    return words in OWNER_WORDS or any(p in words for p in OWNER_PHRASES)


CONTACT_FIELDS = ("customer_name", "contact_number")  # the delivery address is collected in parts (street, area, landmark)
DELIVERY_PARTS = ("street", "area", "area_other", "landmark")  # values of draft["asking"] while the address is asked


@dataclass
class Reply:
    """One outgoing message. products -> carousel; options/ask_phone -> quick replies."""

    text: str = ""
    options: list[tuple[str, str]] = field(default_factory=list)
    ask_phone: bool = False
    products: list[dict[str, Any]] | None = None
    carousel_button: str = "Order this"
    low_stock: str = ""  # carousel label with {n}, e.g. "Only {n} left!"; empty = no hint


def _peso(value: Any) -> str:
    return f"₱{Decimal(str(value)):,.2f}"

def _fulfillment_from_text(words: str) -> str | None:
    """Read a short typed answer to 'delivery or pick up?'. Longer messages go to the AI instead."""
    if not words or len(words.split()) > 4:
        return None
    if any(k in words for k in ("pick", "kuha", "kukunin")):
        return "pickup"
    if any(k in words for k in ("deliver", "padala")):
        return "delivery"
    return None

def _norm(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text).strip().lower()

def _match_area(text: str, areas: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, bool]:
    """Find the listed area a customer means. Returns (area, ambiguous)."""
    said = f" {_norm(text)} "
    if len(said.strip()) < 2:
        return None, False
    named = [a for a in areas if f" {_norm(a['name'])} " in said]  # the area's name appears in the text
    if named:
        return max(named, key=lambda a: len(a["name"])), False
    part = [a for a in areas if len(said.strip()) >= 4 and said in f" {_norm(a['name'])} "]  # text is part of a name
    if len(part) == 1:
        return part[0], False
    return None, bool(part)


def _compose_location(draft: dict[str, Any]) -> str:
    text = f"{draft['street']}, {draft['area']}"
    if draft.get("landmark"):
        text += f" (Landmark: {draft['landmark']})"
    return text


def _fee_line(lang: str, draft: dict[str, Any], fee: Decimal | None, tbc: bool) -> str:
    if tbc:
        return t(lang, "fee_line_tbc")
    return t(lang, "fee_line", area=draft.get("area", ""), fee=_peso(fee or 0))


def _plain(replies: list[Reply]) -> list[Reply]:
    """Drop buttons from side-answers given in the middle of an order."""
    return [replace(r, options=[]) for r in replies]

# Words that settle the language when the AI could not (it says null for some short messages).
_HIL_CLUES = {"kamo", "kmo", "subong", "sang", "pila", "diin", "indi", "palihog", "gid", "kag", "kon", "buwas",
              "halin", "tubtob", "akon", "imo", "amon"}
_TL_CLUES = {"kayo", "ngayon", "po", "opo", "magkano", "saan", "hindi", "naman", "nasaan", "namin", "ninyo",
             "natin", "yung", "sige", "iyong", "katagal", "pakisabi"}
_EN_CLUES = {"how", "much", "the", "do", "you", "your", "can", "is", "are", "what", "where", "when", "want",
             "would", "like", "please", "thank", "thanks", "hello", "hi", "open", "today", "tomorrow", "with", "and", "for"}


def _guess_language(text: str) -> str | None:
    """en, tl or hil from clue words, or None when there are none (or equally many of both)."""
    words = re.findall(r"[a-zñ'-]+", text.lower())
    hil = sum(w in _HIL_CLUES or w.startswith(("naga", "maga", "gina")) for w in words)
    tl = sum(w in _TL_CLUES for w in words)
    if hil != tl:
        return "hil" if hil > tl else "tl"
    if hil == 0 and any(w in _EN_CLUES for w in words):
        return "en"
    return None


def ask_context(names: list[str]) -> str:
    """Tells the AI what a short reply like "1 of each" answers."""
    return ("The customer was just asked how many of these cakes they want: " + ", ".join(names) +
            ". If their reply gives quantities (for example '1 of each', '2 and 3', 'tig-isa'), return those cakes in items "
            "with the quantities. If it does not, ignore this.")


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _draft_expired(draft: dict[str, Any], now: datetime | None = None) -> bool:
    """True when the draft was last touched more than DRAFT_EXPIRY_HOURS ago. A draft without a stamp is kept."""
    try:
        touched = datetime.fromisoformat(str(draft["touched"]))
    except (KeyError, TypeError, ValueError):
        return False
    if touched.tzinfo is None:
        touched = touched.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - touched).total_seconds() > DRAFT_EXPIRY_HOURS * 3600


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


def _unavailable_text(lang: str, product: dict[str, Any]) -> str:
    """'Sold out' when the stock is 0, otherwise 'currently unavailable' (hidden by the owner)."""
    key = "product_sold_out" if product.get("sold_out") else "product_unavailable"
    return t(lang, key, name=product["name"])


def _clean_fields(result: AIResult, products: list[dict[str, Any]], lang: str) -> tuple[dict[str, Any], str | None]:
    """Validate what the AI extracted. Anything invalid is dropped, never trusted."""
    fields: dict[str, Any] = {}
    notice = None
    if result.product:
        product = _find_product(result.product, products)
        if product and product.get("is_available"):
            fields["product"] = product["name"]
        elif product:
            notice = _unavailable_text(lang, product)
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
    looks_like_answer = intent in (Intent.UNKNOWN, Intent.PROVIDE_ORDER_DETAILS) or (
        asked == "customer_name" and intent == Intent.THANKS  # a name such as "Ty"
    )
    if asked in ("customer_name", "location") and t_ and looks_like_answer:
        if len(t_) <= (100 if asked == "customer_name" else 200):
            return {asked: t_}
    return {}


class ConversationService:
    def __init__(self, ai: AIService, client: Any) -> None:
        self._ai = ai
        self._client = client
        self._text = ""
        self._sender_id = ""
        self._prev_misses = 0  # how many messages in a row the bot did not understand, before this one
        self._missed = False  # did the bot fail to understand this message?
        self._lang_seen = False  # has a language ever been detected for this customer?
        self._lang_cand: str | None = None  # a different language seen once, waiting for a second vote

    def _resolve_lang(self, current: str, detected: str | None) -> str:
        """Reply in the language of this message. When the AI can't tell (a number, "ok", a name), keep the current one."""
        if detected not in LANGUAGES:
            return current
        self._lang_seen, self._lang_cand = True, None
        return detected

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
        rows = self._client.table("products").select("*").order("product_id").execute().data or []
        for p in rows:
            if p.get("stock") is not None and p["stock"] <= 0:  # stock 0 = sold out; blank (None) = unlimited
                p["is_available"], p["sold_out"] = False, True
        return rows
    
    # ---------- entry point ----------
    def handle(
        self, sender_id: str, text: str, message_id: str | None = None, payload: str | None = None,
        attachment: str | None = None,
    ) -> list[Reply] | None:
        """Return the replies to send, or None if this message was already processed (Meta retry)."""
        state, draft = self._load(sender_id)
        if message_id and draft.get("last_mid") == message_id:
            return None
        silent, bot_back = self._handoff_gate(sender_id, text or (f"[{attachment}]" if attachment else ""), payload)
        if silent:  # the owner has this customer: stay quiet, but remember the message id (Meta retries)
            draft["touched"] = _stamp()  # a customer talking to the owner has not abandoned the order
            if message_id:
                draft["last_mid"] = message_id
            self._save(sender_id, state, draft)
            return []
        self._lang_seen = bool(draft.get("lang_seen"))
        self._lang_cand = draft.get("lang_cand")
        self._prev_misses = int(draft.get("misses", 0))  # NEW (Edit 2.3)
        self._noted_at, self._noted_now = draft.get("noted_at"), False  # when the "automated assistant" note was last shown
        expired = state != IDLE and _draft_expired(draft)
        if expired:  # an order left unfinished for a day: start fresh instead of resuming a stale cart
            state, draft = IDLE, _new_draft(draft.get("lang", "en"))
        attachment_only = bool(attachment and not text and payload is None)
        if attachment_only:  # a photo, voice message, file, location pin or sticker
            lang = draft.get("lang", "en")
            replies = [] if attachment == "sticker" else [Reply(text=t(lang, "attachment_received"), options=self._owner_options(lang))]
        else:
            replies, state, draft = self._process(sender_id, text, payload, state, draft)
            if expired:
                replies = [Reply(text=t(draft.get("lang", "en"), "draft_expired"))] + replies
        if bot_back and replies:  # coming back from the owner: say that this is the automated assistant again
            replies = [Reply(text=t(draft.get("lang", "en"), "owner_back"))] + replies
        if getattr(self, "_quote_store", None) is not None:  # the cakes of the latest price quote
            draft["quote"] = self._quote_store
        if not attachment_only:  # a photo in between must not wipe a question the customer is still answering
            draft["proposed"] = getattr(self, "_proposed_store", None)  # cakes waiting for the customer's Yes
            draft["ask_items"] = getattr(self, "_ask_store", None)  # cakes the customer was just asked "how many?" about
        draft["noted_at"] = _stamp() if self._noted_now else self._noted_at
        draft["lang_seen"], draft["lang_cand"] = self._lang_seen, self._lang_cand  # survive resets
        draft["misses"] = self._prev_misses + 1 if self._missed else 0  # NEW (Edit 2.4)
        draft["touched"] = _stamp()
        if message_id:
            draft["last_mid"] = message_id  # survives resets, so a retried Confirm can't order twice
        self._save(sender_id, state, draft)
        return replies

    # ---------- owner handoff ----------
    def _handoff_gate(self, sender_id: str, text: str, payload: str | None) -> tuple[bool, bool]:
        """Returns (stay_silent, bot_is_back). Never raises: if the check fails the bot answers normally."""
        try:
            row = owner_inbox.latest_handoff(self._client, sender_id)
            if row is None:
                return False, False
            if row["status"] == "open":
                if owner_inbox.is_expired(row):
                    owner_inbox.resolve(self._client, row["id"], "timeout")
                    return False, True
                if payload == TALK_OWNER:
                    return False, False  # _start_handoff tells them the owner was already notified
                if payload:  # the customer tapped a bot button, so they want the bot back
                    owner_inbox.resolve(self._client, row["id"], "customer")
                    return False, True
                owner_inbox.touch(self._client, row["id"], text)
                return True, False
            if row.get("back_notice_pending"):  # the owner pressed Done in the dashboard
                owner_inbox.clear_back_notice(self._client, row["id"])
                return False, True
        except Exception:
            logger.warning("Owner inbox check failed; answering normally")
        return False, False

    def _start_handoff(self, sender_id: str, text: str, lang: str, state: str, draft: dict[str, Any]):
        """The customer wants the owner. The order draft is kept, so an order in progress can continue later."""
        try:
            row = owner_inbox.latest_handoff(self._client, sender_id)
        except Exception:
            row = None
        if row and row["status"] == "open":
            return [Reply(text=t(lang, "owner_waiting"), options=self._start_options(lang))], state, draft
        if not owner_inbox.open_handoff(self._client, sender_id, text.strip() or "(tapped Talk to the owner)"):
            return [Reply(text=t(lang, "owner_unavailable"), options=self._start_options(lang))], state, draft
        return [Reply(text=self._handoff_text(lang))], state, draft

    # ---------- flow ----------
    def _process(self, sender_id: str, text: str, payload: str | None, state: str, draft: dict[str, Any]):
        products = self._products()
        available = [p for p in products if p.get("is_available")]
        self._text = text
        self._sender_id = sender_id
        lang = draft.get("lang", "en")
        words = _norm(text)

        if payload:
            outcome = self._handle_payload(sender_id, payload, state, draft, products, available)
            if outcome is not None:
                return outcome

        if not payload and _wants_owner(words):  # typed request for the owner: no AI call needed
            return self._start_handoff(sender_id, text, lang, state, draft)
        if state != IDLE and words in CANCEL_WORDS:
            return self._cancelled(lang)
        if state == AWAITING:
            if words in CONFIRM_WORDS:
                return self._place_order(sender_id, draft, products)
            if words in NO_WORDS:
                return self._cancelled(lang)
        if state == IDLE and draft.get("quoted") and words in QUOTE_YES_WORDS:  # "oo" after a price quote
            product = _find_product(draft["quoted"], products)
            if product and product.get("is_available"):
                return self._advance({**_new_draft(lang), "pending": product["name"]}, products, available)
        asking_name = state == COLLECTING and draft.get("asking") == "customer_name"
        if words in THANKS_WORDS and not asking_name:  # fixed reply, no AI call
            draft = {**draft, "lang": self._resolve_lang(lang, THANKS_WORDS[words])}
            return self._thanks(state, draft, products, available, "bye" if words in BYE_WORDS else "thanks")
        if state == COLLECTING and draft.get("asking") == "add_more":
            if words in MORE_WORDS:
                return self._advance({**draft, "cart_done": False, "pending": None, "force_product": True}, products, available)
            if words in DONE_WORDS:
                return self._advance({**draft, "cart_done": True}, products, available)
        if state == COLLECTING and draft.get("asking") == "quantity" and draft.get("pending") and text.strip().isdigit():
            return self._add_to_cart(draft, draft["pending"], int(text.strip()), products, available)
        if state == COLLECTING and draft.get("asking") == "contact_number" and _PHONE_RE.match(text.strip()):
            return self._advance({**draft, "contact_number": text.strip()}, products, available)
        if state == COLLECTING and draft.get("asking") == "fulfillment":
            choice = _fulfillment_from_text(words)
            if choice:
                return self._advance(self._set_fulfillment(draft, choice), products, available)
        if state == COLLECTING and draft.get("asking") == "notes":
            note = text.strip()
            if words in SKIP_WORDS:
                return self._advance({**draft, "notes": "", "notes_done": True}, products, available)
            if note and not note.endswith("?"):  # a question goes to the AI instead
                if len(note) > MAX_NOTES_LENGTH:
                    return self._advance(draft, products, available, t(lang, "notes_too_long"))
                return self._advance({**draft, "notes": note, "notes_done": True}, products, available)
        if state == COLLECTING and draft.get("asking") == "dedication":
            card = text.strip()
            if words in SKIP_WORDS:
                return self._advance({**draft, "dedication": "", "dedication_done": True}, products, available)
            if card and not card.endswith("?"):  # a question goes to the AI instead
                if len(card) > MAX_DEDICATION_LENGTH:
                    return self._advance(draft, products, available, t(lang, "dedication_too_long", max=MAX_DEDICATION_LENGTH))
                return self._advance({**draft, "dedication": card, "dedication_done": True}, products, available)
        if state == COLLECTING and draft.get("asking") in DELIVERY_PARTS:
            outcome = self._typed_delivery_answer(draft, text, words, lang, products, available)
            if outcome is not None:
                return outcome
        topics = {topic: row["description"] for topic, row in load_faqs(self._client).items()}
        ask = draft.get("ask_items") if not payload else None  # the customer is answering "how many of each?"
        extra = {"context": ask_context(ask["names"])} if ask and ask.get("names") else {}
        try:
            result = self._ai.interpret_message(text, state, [p["name"] for p in available], topics, **extra)
        except AIUnavailableError:  # the AI is down: say so, and don't count it as an unknown question
            return self._thanks(state, draft, products, available, "ai_unavailable")

        detected = result.language if result.language in LANGUAGES else _guess_language(text)
        lang = self._resolve_lang(lang, detected)
        draft = {**draft, "lang": lang}
        if result.intent == Intent.TALK_TO_OWNER:
            return self._start_handoff(sender_id, text, lang, state, draft)
        if result.intent == Intent.THANKS and not asking_name:
            return self._thanks(state, draft, products, available)
        if state != IDLE and result.intent == Intent.CANCEL_ORDER:
            return self._cancelled(lang)
        if state == IDLE and ask and result.items:  # "1 of each" after "how many would you like?"
            answered = self._answer_quantities(lang, ask, result, products)
            if answered is not None:
                return answered, IDLE, draft
        if state == IDLE and result.intent == Intent.START_ORDER and result.items:
            proposal = self._propose_items(lang, result.items, products)
            if proposal is not None:  # several cakes in one sentence: repeat them back and wait for the customer's Yes
                return proposal, IDLE, draft
        # The AI's guess is never enough to place an order: only the Confirm button
        # or a clear typed word (CONFIRM_WORDS, handled above) can do that.

        fields, notice = _clean_fields(result, products, lang)
        if not fields and state == COLLECTING:
            fields = _fallback_for_asked_field(draft.get("asking"), text, result.intent)
        wants_order = result.intent in (Intent.START_ORDER, Intent.PROVIDE_ORDER_DETAILS)

        if state == COLLECTING and not draft.get("cart") and not draft.get("pending") and not (fields or wants_order):
            state, draft = IDLE, _new_draft(lang)  # an order was started but nothing is picked yet: answer like a fresh chat

        if state == IDLE:
            if wants_order:
                return self._advance(self._merge(_new_draft(lang), fields), products, available, notice)
            replies = self._answer(result, draft, products, available)
            quoted = None  # remember a quoted cake so a following "oo" can start the order
            if result.intent in (Intent.PRODUCT_QUESTION, Intent.PRICE_QUESTION):
                p = _find_product(result.product, products)
                if p and p.get("is_available"):
                    quoted = p["name"]
            return replies, IDLE, {**draft, "quoted": quoted}

        if state == COLLECTING:
            if fields or wants_order:
                return self._advance(self._merge(draft, fields), products, available, notice)
            nxt = self._advance(draft, products, available)
            return _plain(self._answer(result, draft, products, available)) + nxt[0], COLLECTING, nxt[2]

        # AWAITING confirmation
        if any(k in fields for k in CONTACT_FIELDS + ("location",)):
            return self._advance(self._merge(draft, fields), products, available, notice)
        if result.intent in QUESTION_INTENTS:
            answer = _plain(self._answer(result, draft, products, available))
            return answer + self._summary(draft, products, available)[0], AWAITING, draft
        return [self._confirm_prompt(lang, "awaiting_hint")], AWAITING, draft

    def _order_status_replies(self, lang: str) -> list[Reply]:
        """Status of the customer's open orders (up to 3). Pending ones get a Cancel button: the owner hasn't started them."""
        try:
            shown = orders.get_active_orders(self._client, self._sender_id)
            if not shown:
                latest = orders.get_latest_order(self._client, self._sender_id)
                shown = [latest] if latest else []
        except Exception:
            logger.warning("Could not look up the customer's orders")
            return [Reply(text=t(lang, "error"))]
        if not shown:
            return [Reply(text=t(lang, "order_status_none"), options=self._start_options(lang))]
        blocks = []
        for order in shown:
            items = "\n".join(f"• {qty} × {name}" for name, qty in order["items"])
            key = "status_" + order["status"]
            status_text = t(lang, key) if key in MESSAGES["en"] else order["status"]
            blocks.append(t(lang, "order_status", order_id=order["order_id"], items=items,
                            total=_peso(order["total_amount"]), status_text=status_text))
        cancel = [(t(lang, "btn_cancel_order", order_id=o["order_id"]), f"CANCEL_ORDER:{o['order_id']}")
                  for o in shown if o["status"] == "pending"]
        return [Reply(text="\n\n".join(blocks), options=cancel + self._start_options(lang))]

    def _order_cancel_flow(self, kind: str, arg: str, lang: str) -> list[Reply]:
        """Cancel an order that is already placed: ask first, then cancel only if it is still pending."""
        try:
            order_id = int(arg)
        except ValueError:
            return [Reply(text=t(lang, "fallback"), options=self._start_options(lang))]
        if kind == "KEEP_ORDER":
            return [Reply(text=t(lang, "order_kept", order_id=order_id), options=self._start_options(lang))]
        if kind == "CANCEL_ORDER":
            return [Reply(text=t(lang, "order_cancel_ask", order_id=order_id),
                          options=[(t(lang, "btn_cancel_yes"), f"CANCEL_ORDER_YES:{order_id}"),
                                   (t(lang, "btn_cancel_keep"), f"KEEP_ORDER:{order_id}")])]
        try:
            outcome = orders.cancel_pending_order(self._client, self._sender_id, order_id)
        except Exception:
            logger.warning("Could not cancel order %s", order_id)
            return [Reply(text=t(lang, "error"))]
        if outcome == "cancelled":
            return [Reply(text=t(lang, "order_cancelled_ok", order_id=order_id), options=self._start_options(lang))]
        if outcome == "late":  # the owner already started on it
            return [Reply(text=t(lang, "order_cancel_late", order_id=order_id), options=self._owner_options(lang))]
        return [Reply(text=t(lang, "order_status_none"), options=self._start_options(lang))]

    def _thanks(self, state: str, draft: dict[str, Any], products, available, key: str = "thanks"):
        """Send one short fixed message (thanks, bye, AI trouble), then keep any order moving."""
        lang = draft.get("lang", "en")
        reply = [Reply(text=t(lang, key))]
        if state == COLLECTING:  # keep the order moving: repeat the question we were on
            nxt = self._advance(draft, products, available)
            return reply + nxt[0], COLLECTING, nxt[2]
        if state == AWAITING:
            return reply + self._summary(draft, products, available)[0], AWAITING, draft
        return reply, state, draft
    
    def _log_not_sold(self) -> None:
        """Print what customers ask for that the shop does not sell, so the owner can see the demand."""
        if self._text.strip():
            logger.warning("Asked for something we don't sell: %r", self._text[:300])

    def _log_unanswered(self) -> None:
        """Print questions the bot could not answer, so you know which FAQs to write."""
        if self._text.strip():
            logger.warning("Unanswered question: %r", self._text[:300])
            owner_inbox.add_unanswered(self._client, self._sender_id, self._text)

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
        if "location" in fields:  # typing an address means delivery
            draft["fulfillment"] = "delivery"
            draft["street"] = fields["location"][:MAX_STREET_LENGTH]
            draft.pop("location", None)  # rebuilt once the area and landmark are known
            if not draft.get("area"):
                listed, _ = self._delivery_areas()
                area, _ = _match_area(draft["street"], listed)  # "... Jaro" already says which area
                if area:
                    draft = self._with_area(draft, area)
        for key in CONTACT_FIELDS:
            if key in fields:
                draft[key] = fields[key]
        if any(k in fields for k in CONTACT_FIELDS + ("location",)) and draft["cart"] and not draft.get("pending"):
            draft["cart_done"] = True
        return draft

    @staticmethod
    def _cart_set(draft: dict[str, Any], product: str, quantity: int) -> dict[str, Any]:
        others = sum(i["quantity"] for i in draft["cart"] if i["product"] != product)
        if others + quantity > MAX_CAKES_PER_ORDER:  # ask the quantity again, with a notice
            return {**draft, "pending": product, "cart_done": False, "limit_hit": True}
        cart = [i for i in draft["cart"] if i["product"] != product] + [{"product": product, "quantity": quantity}]
        return {**draft, "cart": cart, "pending": None, "cart_done": False}

    @staticmethod
    def _set_fulfillment(draft: dict[str, Any], choice: str) -> dict[str, Any]:
        draft = {**draft, "fulfillment": choice}
        if choice == "pickup":
            draft["location"] = PICKUP_LOCATION  # no address needed
        elif draft.get("location") == PICKUP_LOCATION:
            draft.pop("location")  # switching to delivery: the address must be asked
        return draft

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

        if kind == "GET_STARTED":  # the Get Started button on a brand-new chat
            return [self._disclaimed(lang, Reply(text=t(lang, "greeting"), options=self._start_options(lang)))], state, draft
        if kind == "MENU":
            return self._menu_replies(lang, available), state, draft
        if kind == "MY_ORDERS":
            return self._order_status_replies(lang), state, draft
        if kind == "QUOTE_AREA":  # an area button under a price quote: add that area's delivery fee
            return [self._quote_area_reply(lang, arg, draft, products)], state, draft
        if kind == "ITEMS_YES":  # the customer confirmed the cakes we repeated back
            return self._add_proposed(lang, draft.get("proposed"), base, products, available)
        if kind == "ORDER_QUOTE":  # "Order these" under a quote: the tap is the confirmation
            return self._add_proposed(lang, draft.get("quote"), base, products, available)
        if kind == "ITEMS_NO":
            return [Reply(text=t(lang, "items_no"), options=self._start_options(lang))], state, draft
        if kind in ("CANCEL_ORDER", "CANCEL_ORDER_YES", "KEEP_ORDER"):  # cancel an order that is already placed
            return self._order_cancel_flow(kind, arg, lang), state, draft
        if kind == TALK_OWNER:
            return self._start_handoff(sender_id, "", lang, state, draft)  # no typed text: a default note is saved
        if kind == "ORDER":
            product = _find_product_by_id(arg, products)
            if product is None or not product.get("is_available"):
                name = product["name"] if product else ""
                notice = _unavailable_text(lang, product) if product else None
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
        if kind == "FULFILL":
            if state == IDLE or not draft.get("cart") or arg not in ("delivery", "pickup"):
                return [Reply(text=t(lang, "fallback"), options=self._start_options(lang))], state, draft
            return self._advance({**self._set_fulfillment(draft, arg), "cart_done": True}, products, available)
        if kind == "SKIP_NOTES":
            if state != COLLECTING or draft.get("asking") != "notes":
                return [Reply(text=t(lang, "fallback"), options=self._start_options(lang))], state, draft
            return self._advance({**draft, "notes": "", "notes_done": True}, products, available)
        if kind == "AREA":
            if state != COLLECTING or draft.get("asking") != "area":
                return [Reply(text=t(lang, "fallback"), options=self._start_options(lang))], state, draft
            listed, other = self._delivery_areas()
            if arg == "other" and other:
                return self._advance({**draft, "area_other_mode": True}, products, available)
            area = next((a for a in listed if str(a["id"]) == arg), None)
            if area is None:  # the owner changed the list while the customer was choosing
                return self._advance(draft, products, available, t(lang, "area_pick"))
            return self._advance(self._with_area(draft, area), products, available)
        if kind == "NO_LANDMARK":
            if state != COLLECTING or draft.get("asking") != "landmark":
                return [Reply(text=t(lang, "fallback"), options=self._start_options(lang))], state, draft
            return self._advance({**draft, "landmark": "", "landmark_done": True}, products, available)
        if kind == "SKIP_DEDICATION":
            if state != COLLECTING or draft.get("asking") != "dedication":
                return [Reply(text=t(lang, "fallback"), options=self._start_options(lang))], state, draft
            return self._advance({**draft, "dedication": "", "dedication_done": True}, products, available)
        return None  # unknown payload (e.g. the phone-number button): handle its text instead

    # ---------- replies ----------
    @staticmethod
    def _start_options(lang: str) -> list[tuple[str, str]]:
        return [(t(lang, "btn_order_now"), "START_ORDER"), (t(lang, "btn_menu"), "MENU")]

    @staticmethod
    def _owner_options(lang: str) -> list[tuple[str, str]]:
        """Start buttons plus 'Talk to the owner', offered when the bot could not help."""
        return [(t(lang, "btn_order_now"), "START_ORDER"), (t(lang, "btn_menu"), "MENU"), (t(lang, "btn_talk_owner"), TALK_OWNER)]

    @staticmethod
    def _confirm_prompt(lang: str, key: str) -> Reply:
        return Reply(text=t(lang, key), options=[(t(lang, "btn_confirm"), "CONFIRM"), (t(lang, "btn_cancel"), "CANCEL")])

    @staticmethod
    def _menu_replies(lang: str, available) -> list[Reply]:
        if not available:
            return [Reply(text=t(lang, "menu_empty"))]
        return [Reply(text=t(lang, "menu_intro"), products=available, carousel_button=t(lang, "btn_order_this"),
                      low_stock=t(lang, "low_stock", n="{n}"))]

    def _shop_closed_reply(self, lang: str) -> Reply | None:
        """A closed-shop notice, or None while the shop is taking orders (owner settings: see shop_hours)."""
        status = shop_hours.current_status(self._client)
        if status.is_open:
            return None
        shop_hours.log_missed(self._client, self._sender_id, status.reason)  # counted in the dashboard's Insights tab
        return self._closed_notice(lang, status)

    def _hours_answer(self, lang: str) -> Reply | None:
        """Live answer to "are you open?" from the owner's settings. None = the FAQ text answers."""
        status = shop_hours.current_status(self._client)
        if not status.is_open:
            return self._closed_notice(lang, status)  # includes the reason the owner typed and when we reopen
        if status.closes is None:  # no opening hours set in the dashboard
            return None
        return Reply(text=t(lang, "shop_open_until", until=shop_hours.clock(status.closes)), options=self._start_options(lang))

    @staticmethod
    def _closed_notice(lang: str, status: shop_hours.ShopStatus) -> Reply:
        when = shop_hours.reopening_text(status)
        text = t(lang, "shop_closed_back", when=when) if when else t(lang, "shop_closed_open_ended")
        if status.note:
            text += t(lang, "shop_closed_note", note=status.note)
        return Reply(text=text, options=[(t(lang, "btn_menu"), "MENU"), (t(lang, "btn_talk_owner"), TALK_OWNER)])

    def _cancelled(self, lang: str):
        return [Reply(text=t(lang, "cancelled"), options=self._start_options(lang))], IDLE, _new_draft(lang)

    def _answer(self, result: AIResult, draft, products, available) -> list[Reply]:
        lang = draft.get("lang", "en")
        if result.intent == Intent.GREETING:
            return [self._disclaimed(lang, Reply(text=t(lang, "greeting"), options=self._start_options(lang)))]
        if result.intent == Intent.CANCEL_ORDER:  # idle: they may mean an order that is already placed
            return self._order_status_replies(lang)
        if result.intent == Intent.ORDER_STATUS:
            return self._order_status_replies(lang)
        if result.intent == Intent.NOT_SOLD:
            self._log_not_sold()
            return [Reply(text=t(lang, "not_sold"), options=self._start_options(lang))]
        if result.intent == Intent.SMALL_TALK:
            return [Reply(text=t(lang, "small_talk"), options=self._start_options(lang))]
        if result.intent == Intent.ABOUT_BOT:
            return [Reply(text=t(lang, "about_bot"), options=self._start_options(lang))]
        if result.intent == Intent.PRICE_QUESTION and result.items:
            quote = self._quote_reply(lang, result.items, result.place, products)
            if quote is not None:  # None = a plain "how much is X?", answered below as before
                return [quote]
        if result.intent in (Intent.PRODUCT_QUESTION, Intent.PRICE_QUESTION):
            product = _find_product(result.product, products)
            if product is None:
                return self._menu_replies(lang, available)
            if not product.get("is_available"):
                return [Reply(text=_unavailable_text(lang, product))] + self._menu_replies(lang, available)
            desc = f"\n{product['description']}" if product.get("description") else ""
            if product.get("stock") is not None and 0 < product["stock"] <= LOW_STOCK_AT:
                desc += "\n" + t(lang, "low_stock", n=product["stock"])
            text = t(lang, "price_line", name=product["name"], price=_peso(product["price"]), desc=desc)
            return [Reply(text=text, options=[(t(lang, "btn_order_this"), f"ORDER:{product['product_id']}"), (t(lang, "btn_menu"), "MENU")])]
        if result.intent == Intent.ORDERING_INFO:
            return [Reply(text=t(lang, "ordering_info"), options=self._start_options(lang))]
        if result.intent == Intent.FAQ_QUESTION:
            topic = result.faq_topic or ""
            live = None
            if topic == "hours":  # open, closed, why and until when come from the database, not the AI
                live = self._hours_answer(lang)
            elif topic == "delivery":  # areas and fees come from the delivery_areas table
                live = self._delivery_answer(lang, result.place)
            elif topic == "best_seller":  # counted from the real orders of the last 30 days
                live = self._best_seller_answer(lang, products)
            if live is not None:
                return [self._disclaimed(lang, live)]
            row = load_faqs(self._client).get(topic)
            answer = row and (row.get(lang) or row["en"])
            if answer:  # the answer is text YOU wrote in faq.py, never AI-generated
                if topic in ("pickup", "location"):
                    answer += self._live_hours_line(lang)  # today's hours or closed notice, from the database
                options = self._start_options(lang)
                if topic == "cancellation":  # the customer can cancel a pending order from "My orders"
                    options = options + [(t(lang, "btn_my_orders"), "MY_ORDERS")]
                return [self._disclaimed(lang, Reply(text=answer, options=options))]
            self._log_unanswered()
            return [Reply(text=t(lang, "faq_unknown"), options=self._owner_options(lang))]
        self._log_unanswered()
        self._missed = True  # counted, so a second miss in a row gets a different reply
        key = "fallback_again" if self._prev_misses else "fallback"
        options = self._owner_options(lang) if self._prev_misses else self._start_options(lang)  # 2nd miss: offer the owner
        return [Reply(text=t(lang, key), options=options)]

    def _quote_reply(self, lang: str, raw_items, place: str | None, products, area: dict[str, Any] | None = None) -> Reply | None:
        """Price (and delivery) quote for the cakes the customer asked about.

        The AI only said which cakes, how many and where. Every name, price, stock count and fee below comes from
        the database, and the reply repeats what was understood so a misread is easy to spot. Nothing is saved.
        Returns None when this is a plain "how much is X?", which the normal price answer handles.
        """
        chosen, merged, unknown = self._resolve_items(raw_items, products)
        if not chosen:
            return None
        if len(chosen) == 1 and merged[int(chosen[0]["product_id"])] is None and not place and area is None and not unknown:
            return None

        body: list[str] = []
        problems: list[str] = []
        subtotal = Decimal(0)
        complete = True  # every cake has a usable quantity
        for product in chosen:
            qty = merged[int(product["product_id"])]
            if not product.get("is_available"):
                problems.append(_unavailable_text(lang, product))
                continue
            if qty is None:
                complete = False
                body.append(t(lang, "quote_unit_line", name=product["name"], price=_peso(product["price"])))
                continue
            stock = product.get("stock")
            if stock is not None and qty > stock:
                problems.append(t(lang, "quote_over_stock", n=stock, name=product["name"]))
                continue
            line = Decimal(str(product["price"])) * qty
            subtotal += line
            body.append(t(lang, "quote_line", qty=qty, name=product["name"], amount=_peso(line)))
        if unknown:
            problems.append(t(lang, "quote_not_found", names=", ".join(unknown)[:60]))

        options = self._start_options(lang)
        head = [t(lang, "quote_header")] if body else []
        if problems:
            return Reply(text="\n".join(head + body + problems + [t(lang, "quote_no_total")]), options=options)
        if not complete:
            self._ask_store = {"kind": "quote", "names": [p["name"] for p in chosen if p.get("is_available")], "place": place}
            return Reply(text="\n".join(head + body + [t(lang, "quote_need_qty")]), options=options)

        self._quote_store = [[int(p["product_id"]), merged[int(p["product_id"])]] for p in chosen]  # for the area buttons
        lines = head + body + [t(lang, "quote_cakes", amount=_peso(subtotal))]
        listed, other = self._delivery_areas()
        ambiguous = False
        if area is None and place and listed:
            area, ambiguous = _match_area(place, listed)
        buttons: list[tuple[str, str]] = []
        if area is not None:
            fee = Decimal(str(area["fee"]))
            lines += [t(lang, "quote_delivery", area=area["name"], fee=_peso(fee)), t(lang, "quote_total", amount=_peso(subtotal + fee))]
        elif ambiguous:
            lines.append(t(lang, "delivery_area_pick", areas="\n".join(f"• {a['name']}" for a in listed)))
            buttons = self._quote_area_buttons(listed)
        elif place:
            lines.append(t(lang, "delivery_area_other" if other else "delivery_area_no", place=place[:40]))
        elif not listed and not other:  # flat-fee mode
            lines.append(t(lang, "quote_flat", fee=_peso(DELIVERY_FEE), total=_peso(subtotal + DELIVERY_FEE)))
        else:
            lines.append(t(lang, "quote_ask_area"))
            buttons = self._quote_area_buttons(listed)
        lines += ["", t(lang, "quote_footer")]
        return Reply(text="\n".join(lines), options=[(t(lang, "btn_order_these"), "ORDER_QUOTE")] + buttons + options)

    @staticmethod
    def _resolve_items(raw_items, products) -> tuple[list[dict[str, Any]], dict[int, int | None], list[str]]:
        """The cakes a customer named: menu rows in the order named, {product_id: quantity or None}, names not on the menu."""
        merged: dict[int, int | None] = {}
        chosen: list[dict[str, Any]] = []
        unknown: list[str] = []
        for item in list(raw_items)[:QUOTE_MAX_ITEMS]:
            product = _find_product(item.product, products)
            if product is None:
                unknown.append(str(item.product))
                continue
            qty = item.quantity if isinstance(item.quantity, int) and 1 <= item.quantity <= QUOTE_MAX_QTY else None
            pid = int(product["product_id"])
            if pid not in merged:
                chosen.append(product)
                merged[pid] = qty
            elif qty is not None:  # the same cake named twice: add up
                total_qty = (merged[pid] or 0) + qty
                merged[pid] = total_qty if total_qty <= QUOTE_MAX_QTY else None
        return chosen, merged, unknown

    def _propose_items(self, lang: str, raw_items, products) -> list[Reply] | None:
        """Several cakes in one ordering sentence: repeat them back and wait for the customer's Yes.

        Nothing goes into the cart until the customer taps Yes. None = one cake or none, which the normal flow handles.
        """
        chosen, merged, unknown = self._resolve_items(raw_items, products)
        if len(chosen) + len(unknown) < 2:
            return None
        options = self._start_options(lang)
        lines: list[str] = []
        problems: list[str] = []
        names: list[str] = []
        complete = True
        for product in chosen:
            qty = merged[int(product["product_id"])]
            if not product.get("is_available"):
                problems.append(_unavailable_text(lang, product))
                continue
            names.append(product["name"])
            if qty is None:
                complete = False
                lines.append(t(lang, "quote_unit_line", name=product["name"], price=_peso(product["price"])))
                continue
            stock = product.get("stock")
            if stock is not None and qty > stock:
                problems.append(t(lang, "quote_over_stock", n=stock, name=product["name"]))
                continue
            lines.append(t(lang, "quote_line", qty=qty, name=product["name"], amount=_peso(Decimal(str(product["price"])) * qty)))
        if unknown:
            problems.append(t(lang, "quote_not_found", names=", ".join(unknown)[:60]))
        if problems:
            return [Reply(text="\n".join(lines + problems + [t(lang, "items_retry")]), options=options)]
        if not complete:
            self._ask_store = {"kind": "order", "names": names, "place": None}
            return [Reply(text="\n".join(lines + [t(lang, "items_need_qty")]), options=options)]
        self._proposed_store = [[int(p["product_id"]), merged[int(p["product_id"])]] for p in chosen]
        return [Reply(text=t(lang, "items_confirm", lines="\n".join(lines)),
                      options=[(t(lang, "btn_items_yes"), "ITEMS_YES"), (t(lang, "btn_items_no"), "ITEMS_NO")])]

    def _answer_quantities(self, lang: str, ask: dict[str, Any], result: AIResult, products) -> list[Reply] | None:
        """The customer's reply to "how many of each?": finish the quote, or the order proposal, with their quantities."""
        if ask.get("kind") == "quote":
            reply = self._quote_reply(lang, result.items, ask.get("place") or result.place, products)
            return [reply] if reply else None
        if ask.get("kind") == "order":
            return self._propose_items(lang, result.items, products)
        return None

    def _add_proposed(self, lang: str, pairs, base: dict[str, Any], products, available):
        """Put the cakes the customer confirmed into the cart and carry on with the order as usual."""
        draft = {**base}
        added, notice = 0, None
        for pid, qty in pairs or []:
            product = _find_product_by_id(str(pid), products)
            if product is None or not product.get("is_available"):
                notice = _unavailable_text(lang, product) if product else notice
                continue
            try:
                orders.validate_quantity(qty)
            except orders.InvalidQuantityError as exc:
                notice = str(exc)
                continue
            draft = self._cart_set(draft, product["name"], int(qty))
            if draft.get("limit_hit"):
                return self._advance(draft, products, available)  # asks the quantity again, with the limit notice
            added += 1
        if not added:
            return [Reply(text=notice or t(lang, "items_lost"), options=self._start_options(lang))], IDLE, _new_draft(lang)
        return self._advance({**draft, "cart_done": True, "pending": None}, products, available, notice)

    @staticmethod
    def _quote_area_buttons(listed: list[dict[str, Any]]) -> list[tuple[str, str]]:
        """'Jaro ₱50' style quick replies (Messenger titles are 20 characters at most)."""
        return [(f"{a['name']} ₱{Decimal(str(a['fee'])):g}"[:20], f"QUOTE_AREA:{a['id']}") for a in listed[:8]]

    def _quote_area_reply(self, lang: str, raw_id: str, draft: dict[str, Any], products) -> Reply:
        """The customer tapped an area under a quote: finish that quote with the area's delivery fee."""
        listed, _ = self._delivery_areas()
        try:
            area = next((a for a in listed if int(a["id"]) == int(raw_id)), None)
            items = [SimpleNamespace(product=p["name"], quantity=qty)
                     for pid, qty in draft.get("quote") or []
                     for p in products if int(p["product_id"]) == int(pid)]
        except (TypeError, ValueError):
            area, items = None, []
        quote = self._quote_reply(lang, items, None, products, area=area) if area and items else None
        return quote or Reply(text=t(lang, "quote_lost"), options=self._start_options(lang))

    def _best_seller_answer(self, lang: str, products) -> Reply | None:
        """The cake ordered most in the last BEST_SELLER_DAYS days, counted from the orders table.

        None (the FAQ text answers) when there are too few orders to say, or the lookup fails. A cake that is sold out
        or hidden is skipped, so the bot never points at something the customer cannot order.
        """
        try:
            ranked, total = orders.best_sellers(self._client, BEST_SELLER_DAYS)
        except Exception:
            logger.warning("Could not count the best sellers")
            return None
        if total < BEST_SELLER_MIN_SOLD:
            return None
        available = {p["name"]: p for p in products if p.get("is_available")}
        top = [(name, qty) for name, qty in ranked if name in available][:3]
        if not top:
            return None
        name, sold = top[0]
        lines = [t(lang, "best_seller_top", name=name, n=sold, days=BEST_SELLER_DAYS)]
        if len(top) > 1:
            lines.append(t(lang, "best_seller_also", names=", ".join(n for n, _ in top[1:])))
        options = [(t(lang, "btn_order_this"), f"ORDER:{available[name]['product_id']}")] + self._start_options(lang)
        return Reply(text="\n".join(lines), options=options)

    def _handoff_text(self, lang: str) -> str:
        """Tell the customer when to expect the owner: soon while open, at reopening time while closed."""
        status = shop_hours.current_status(self._client)
        if status.is_open:
            return t(lang, "owner_handoff")
        when = shop_hours.reopening_text(status)
        return t(lang, "owner_handoff_closed", when=when) if when else t(lang, "owner_handoff_closed_open")

    def _live_hours_line(self, lang: str) -> str:
        """Appended to the pickup and location answers: today's closing time, or why we are closed."""
        status = shop_hours.current_status(self._client)
        if not status.is_open:
            return "\n\n" + self._closed_notice(lang, status).text
        if status.closes is not None:
            return "\n" + t(lang, "shop_open_line", until=shop_hours.clock(status.closes))
        return ""  # no opening hours set in the dashboard: the FAQ text stands alone

    def _delivery_answer(self, lang: str, place: str | None) -> Reply | None:
        """Delivery areas and fees straight from the database. None in flat-fee mode: the FAQ text answers."""
        listed, other = self._delivery_areas()
        if not listed and not other:
            return None
        areas = "\n".join(f"• {a['name']} — {_peso(a['fee'])}" for a in listed)
        place = (place or "").strip()[:40]
        if not listed:  # only "other areas" is switched on
            text = t(lang, "delivery_area_other", place=place) if place else t(lang, "delivery_only_other")
        elif place:
            area, ambiguous = _match_area(place, listed)
            if area:
                text = t(lang, "delivery_area_fee", area=area["name"], fee=_peso(area["fee"]))
            elif ambiguous:
                text = t(lang, "delivery_area_pick", areas=areas)
            elif other:
                text = t(lang, "delivery_area_other", place=place)
            else:
                text = t(lang, "delivery_area_no", place=place) + "\n" + t(lang, "delivery_list", areas=areas)
        else:
            text = t(lang, "delivery_list", areas=areas) + "\n" + t(lang, "delivery_other_yes" if other else "delivery_other_no")
        return Reply(text=text, options=self._start_options(lang))

    def _disclaimed(self, lang: str, reply: Reply) -> Reply:
        """Add the short 'automated assistant' note, at most once per NOTE_EVERY_HOURS per customer (SHOW_DISCLAIMER = False turns it off)."""
        if not SHOW_DISCLAIMER or not self._note_due():
            return reply
        self._noted_now = True
        return replace(reply, text=f"{reply.text}\n\n{t(lang, 'ai_disclaimer')}")

    def _note_due(self) -> bool:
        if getattr(self, "_noted_now", False):  # already added to a reply in this turn
            return False
        try:
            shown = datetime.fromisoformat(str(self._noted_at))
        except (AttributeError, TypeError, ValueError):
            return True
        if shown.tzinfo is None:
            shown = shown.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - shown).total_seconds() > NOTE_EVERY_HOURS * 3600

    def _cart_text(self, cart: list[dict[str, Any]], products) -> str:
        rows = []
        for item in cart:
            p = _find_product(item["product"], products)
            if p:
                subtotal = orders.calculate_subtotal(p["price"], item["quantity"])
                rows.append(f"• {item['quantity']} × {p['name']} — {_peso(subtotal)}")
        return "\n".join(rows)

    @staticmethod
    def _stock_problems(draft: dict[str, Any], products) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
        """Cart items that are no longer orderable, or ask for more than the stock. (item, product) pairs."""
        by_name = {p["name"].strip().lower(): p for p in products}
        problems = []
        for item in draft.get("cart") or []:
            p = by_name.get(item["product"].strip().lower())
            if p is None or not p.get("is_available") or (p.get("stock") is not None and item["quantity"] > p["stock"]):
                problems.append((item, p))
        return problems

    def _fit_cart_to_stock(self, draft: dict[str, Any], products, lang: str) -> tuple[dict[str, Any], str | None]:
        """Take unorderable items out of the cart and re-ask the quantity of ones that are only partly in stock."""
        problems = self._stock_problems(draft, products)
        if not problems:
            return draft, None
        gone = {id(item) for item, _ in problems}
        notices, pending = [], draft.get("pending")
        for item, p in problems:
            if p is None or not p.get("is_available"):
                notices.append(_unavailable_text(lang, p or {"name": item["product"]}))
            else:
                notices.append(t(lang, "qty_limit_stock", name=p["name"], left=p["stock"]))
                pending = p["name"]
        cart = [i for i in draft["cart"] if id(i) not in gone]
        return {**draft, "cart": cart, "pending": pending, "cart_done": False}, " ".join(notices)

    def _advance(self, draft: dict[str, Any], products, available, notice: str | None = None):
        """Ask for whatever is missing next, or show the summary when everything is present."""
        lang = draft.get("lang", "en")
        closed = self._shop_closed_reply(lang)
        if closed is not None:  # closed day, outside opening hours or switched off: no new orders
            return [closed], IDLE, _new_draft(lang)
        draft, stock_notice = self._fit_cart_to_stock({**draft}, products, lang)
        if stock_notice:
            notice = f"{notice} {stock_notice}" if notice else stock_notice
        cart = draft.get("cart") or []
        if draft.pop("limit_hit", False):
            notice = t(lang, "qty_limit_order", max=MAX_CAKES_PER_ORDER)
        force_product = draft.pop("force_product", False)

        if draft.get("pending"):
            draft["asking"] = "quantity"
            pending_product = next((p for p in products if p["name"] == draft["pending"]), None)
            top = 5
            if pending_product and pending_product.get("stock") is not None:
                top = max(1, min(5, pending_product["stock"]))  # no buttons for more than what is left
            replies = [Reply(text=t(lang, "ask_quantity", name=draft["pending"]),
                             options=[(str(n), f"QTY:{n}") for n in range(1, top + 1)])]
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
        elif not draft.get("fulfillment"):
            draft["asking"] = "fulfillment"
            replies = [Reply(text=t(lang, "ask_fulfillment"),
                             options=[(t(lang, "btn_delivery"), "FULFILL:delivery"), (t(lang, "btn_pickup"), "FULFILL:pickup")])]
        else:
            missing = next((f for f in CONTACT_FIELDS if not draft.get(f)), None)
            if missing is None and draft.get("fulfillment") == "delivery":
                missing = self._missing_delivery_part(draft)
                if missing is None:
                    draft["location"] = _compose_location(draft)  # street, area and landmark in one line
            if missing is None and draft.get("notes_done") and draft.get("dedication_done"):
                return self._summary(draft, products, available)
            if missing is None and draft.get("notes_done"):
                draft["asking"] = "dedication"
                replies = [Reply(text=t(lang, "ask_dedication", max=MAX_DEDICATION_LENGTH),
                                 options=[(t(lang, "btn_skip"), "SKIP_DEDICATION")])]
            elif missing is None:
                draft["asking"] = "notes"
                replies = [Reply(text=t(lang, "ask_notes"), options=[(t(lang, "btn_skip"), "SKIP_NOTES")])]
            elif missing in DELIVERY_PARTS:
                draft["asking"] = missing
                replies = [self._ask_delivery_part(lang, missing)]
            else:
                draft["asking"] = missing
                key = {"customer_name": "ask_name", "contact_number": "ask_contact"}[missing]
                replies = [Reply(text=t(lang, key), ask_phone=(missing == "contact_number"))]

        if notice and replies and replies[0].text:
            replies[0] = replace(replies[0], text=f"{notice} {replies[0].text}")
        return replies, COLLECTING, draft

    # ---------- delivery address, area and fee ----------
    def _delivery_areas(self) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """(active listed areas, the 'other areas' row if it is switched on). Both empty means flat-fee mode."""
        cached = getattr(self, "_areas_cache", None)
        if cached is not None:
            return cached
        try:
            rows = self._client.table("delivery_areas").select("*").eq("is_active", True).order("id").execute().data or []
        except Exception:
            logger.warning("Could not load delivery areas; using the flat delivery fee (run the delivery areas SQL?)")
            rows = []
        listed = [r for r in rows if not r.get("is_other")]
        other = next((r for r in rows if r.get("is_other")), None)
        self._areas_cache = (listed, other)
        return self._areas_cache

    @staticmethod
    def _with_area(draft: dict[str, Any], area: dict[str, Any]) -> dict[str, Any]:
        """A listed area: its fee comes from the database."""
        return {**draft, "area": area["name"], "area_id": area["id"], "area_custom": False,
                "fee": str(area["fee"]), "fee_tbc": False, "area_other_mode": False}

    @staticmethod
    def _with_custom_area(draft: dict[str, Any], text: str) -> dict[str, Any]:
        """An area that is not listed: the owner confirms the fee."""
        return {**draft, "area": text, "area_id": None, "area_custom": True,
                "fee": None, "fee_tbc": True, "area_other_mode": False}

    def _missing_delivery_part(self, draft: dict[str, Any]) -> str | None:
        if not draft.get("street"):
            return "street"
        if not draft.get("area"):
            listed, other = self._delivery_areas()
            if draft.get("area_other_mode") or (other and not listed):
                return "area_other"
            return "area"
        if not draft.get("landmark_done"):
            return "landmark"
        return None

    def _ask_delivery_part(self, lang: str, part: str) -> Reply:
        if part == "street":
            return Reply(text=t(lang, "ask_street"))
        if part == "landmark":
            return Reply(text=t(lang, "ask_landmark"), options=[(t(lang, "btn_no_landmark"), "NO_LANDMARK")])
        if part == "area_other":
            return Reply(text=t(lang, "ask_area_other"))
        listed, other = self._delivery_areas()
        if not listed and not other:  # no areas set up yet: any city is accepted and the flat fee applies
            return Reply(text=t(lang, "ask_area_plain"))
        fees = "\n".join(f"• {a['name']}: {_peso(a['fee'])}" for a in listed)
        text = t(lang, "ask_area", fees=fees)
        options = [(a["name"][:20], f"AREA:{a['id']}") for a in listed[:12]]
        if other:
            text += " " + t(lang, "ask_area_other_hint")
            options.append((t(lang, "btn_other_area"), "AREA:other"))
        return Reply(text=text, options=options)

    def _typed_delivery_answer(self, draft, text, words, lang, products, available):
        """A typed answer to the street, area or landmark question. None = let the usual handling take it."""
        answer = text.strip()
        if not answer or answer.endswith("?") or words in CANCEL_WORDS:
            return None  # a question or a cancel goes through the usual path
        asking = draft["asking"]
        if asking == "street":
            if len(answer) > MAX_STREET_LENGTH:
                return self._advance(draft, products, available, t(lang, "address_too_long", max=MAX_STREET_LENGTH))
            if len(answer) < MIN_STREET_LENGTH:
                return self._advance(draft, products, available, t(lang, "address_too_short"))
            draft = {**draft, "street": answer}
            if not draft.get("area"):
                listed, _ = self._delivery_areas()
                area, _ = _match_area(answer, listed)
                if area:
                    draft = self._with_area(draft, area)
            return self._advance(draft, products, available)
        if asking == "landmark":
            if words in SKIP_WORDS:
                return self._advance({**draft, "landmark": "", "landmark_done": True}, products, available)
            return self._advance({**draft, "landmark": answer[:MAX_LANDMARK_LENGTH], "landmark_done": True}, products, available)
        listed, other = self._delivery_areas()
        if asking == "area_other":
            return self._advance(self._with_custom_area(draft, answer[:MAX_AREA_LENGTH]), products, available)
        # asking == "area"
        if not listed and not other:  # flat-fee mode: whatever city they type is fine
            return self._advance({**draft, "area": answer[:MAX_AREA_LENGTH], "area_id": None, "area_custom": False,
                                  "fee": str(DELIVERY_FEE), "fee_tbc": False}, products, available)
        area, ambiguous = _match_area(answer, listed)
        if area:
            return self._advance(self._with_area(draft, area), products, available)
        if ambiguous:
            return self._advance(draft, products, available, t(lang, "area_pick"))
        if other:  # not on the list: the owner confirms the fee
            return self._advance(self._with_custom_area(draft, answer[:MAX_AREA_LENGTH]), products, available)
        names = ", ".join(a["name"] for a in listed)
        return self._advance(draft, products, available, t(lang, "area_not_found", areas=names))

    @staticmethod
    def _fee_info(draft: dict[str, Any]) -> tuple[Decimal | None, bool]:
        """(fee, to_be_confirmed) of a delivery order. Pick up has no fee."""
        if draft.get("fulfillment") != "delivery":
            return None, False
        if draft.get("fee_tbc") or draft.get("fee") is None:
            return None, True
        return Decimal(str(draft["fee"])), False

    # ---------- summary and ordering ----------
    def _build_request(self, draft: dict[str, Any], messenger_id: str | None, confirmed: bool) -> OrderRequest:
        return OrderRequest(
            customer_name=draft["customer_name"],
            contact_number=draft["contact_number"],
            messenger_id=messenger_id,
            location=draft["location"],
            items=[{"product_name": i["product"], "quantity": i["quantity"]} for i in draft["cart"]],
            fulfillment=draft.get("fulfillment", "delivery"),
            notes=draft.get("notes") or None,
            dedication=draft.get("dedication") or None,
            delivery_area=draft.get("area") if draft.get("fulfillment") == "delivery" else None,
            delivery_fee=self._fee_info(draft)[0],
            confirmed=confirmed,
        )

    @staticmethod
    def _where_text(lang: str, draft: dict[str, Any]) -> str:
        if draft.get("fulfillment") == "pickup":
            return t(lang, "where_pickup")
        return t(lang, "where_delivery", location=draft["location"])

    def _summary(self, draft: dict[str, Any], products, available):
        lang = draft.get("lang", "en")
        if self._stock_problems(draft, products):  # _advance fixes the cart and tells the customer
            return self._advance(draft, products, [p for p in products if p.get("is_available")])
        try:
            lines = orders.build_order_lines(self._build_request(draft, None, confirmed=False), products)
        except ValueError:
            fresh = _new_draft(lang)
            nxt = self._advance(fresh, products, available)
            return [Reply(text=t(lang, "invalid_order"))] + nxt[0], COLLECTING, nxt[2]
        if draft.get("fulfillment") == "delivery" and draft.get("area_id") is not None and not draft.get("area_custom"):
            listed, _ = self._delivery_areas()
            row = next((a for a in listed if a["id"] == draft["area_id"]), None)
            if row is None:  # the owner took the area off the list while the customer was ordering
                gone = ("area", "area_id", "area_custom", "fee", "fee_tbc", "area_other_mode", "location")
                return self._advance({k: v for k, v in draft.items() if k not in gone}, products, available, t(lang, "area_changed"))
            draft = {**draft, "fee": str(row["fee"])}  # the owner may have changed the fee
        fee, tbc = self._fee_info(draft)
        items = "\n".join(f"• {l.quantity} × {l.product_name} — {_peso(l.subtotal)}" for l in lines)
        if draft.get("fulfillment") == "delivery":
            items += "\n" + _fee_line(lang, draft, fee, tbc)
        subtotal = orders.calculate_total(lines)
        total = t(lang, "total_plus_fee", amount=_peso(subtotal)) if tbc else _peso(subtotal + (fee or 0))
        notes = t(lang, "notes_line", notes=draft["notes"]) if draft.get("notes") else ""
        if draft.get("dedication"):
            notes += t(lang, "dedication_line", card=draft["dedication"])
        text = t(lang, "summary", items=items, total=total,
                 name=draft["customer_name"], contact=draft["contact_number"],
                 where=self._where_text(lang, draft), notes=notes)
        draft = {k: v for k, v in draft.items() if k not in ("asking", "pending", "force_product")}
        reply = Reply(text=text, options=[(t(lang, "btn_confirm"), "CONFIRM"), (t(lang, "btn_cancel"), "CANCEL")])
        return [reply], AWAITING, draft

    def _place_order(self, sender_id: str, draft: dict[str, Any], products):
        lang = draft.get("lang", "en")
        closed = self._shop_closed_reply(lang)
        if closed is not None:  # the shop closed while the customer was deciding: nothing is saved
            return [closed], IDLE, _new_draft(lang)
        try:
            order = orders.create_order(self._build_request(draft, sender_id, confirmed=True), client=self._client)
        except orders.OrderCreationError:
            return [self._confirm_prompt(lang, "order_failed_retry")], AWAITING, draft
        except orders.InsufficientStockError:  # someone else took the cakes while the customer was deciding
            fresh = self._products()
            return self._advance(draft, fresh, [p for p in fresh if p.get("is_available")])
        except ValueError as exc:  # e.g. a cake became unavailable since the customer chose it
            logger.warning("Order rejected at confirmation: %s", type(exc).__name__)
            return [Reply(text=t(lang, "order_rejected"), options=self._start_options(lang))], IDLE, _new_draft(lang)
        fee, tbc = self._fee_info(draft)
        items = "\n".join(f"• {i.quantity} × {i.product_name}" for i in order.items)
        if draft.get("fulfillment") == "delivery":
            items += "\n" + _fee_line(lang, draft, fee, tbc)
        total = t(lang, "total_plus_fee", amount=_peso(order.total_amount)) if tbc else _peso(order.total_amount)
        text = t(lang, "order_received", order_id=order.order_id, items=items,
                 total=total, where=self._where_text(lang, draft), status=order.status)
        options = self._start_options(lang) + [(t(lang, "btn_my_orders"), "MY_ORDERS")]  # to check it or cancel while pending
        return [Reply(text=text, options=options)], IDLE, _new_draft(lang)


_ai_service: AIService | None = None


def handle_incoming_message(
    sender_id: str, text: str, message_id: str | None = None, payload: str | None = None,
    attachment: str | None = None,
) -> list[Reply] | None:
    """Called by the webhook. Never raises, so Meta always gets a 200."""
    global _ai_service
    try:
        from app.db.supabase import get_supabase_client

        if _ai_service is None:
            from app.services.ai_service import GeminiAIService

            _ai_service = GeminiAIService()
        return ConversationService(_ai_service, get_supabase_client()).handle(sender_id, text, message_id, payload, attachment)
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
            messenger.send_product_carousel(sender_id, r.products, r.carousel_button, r.low_stock)
        elif r.options or r.ask_phone:
            messenger.send_quick_replies(sender_id, r.text, r.options, r.ask_phone)
        elif r.text:
            messenger.send_text(sender_id, r.text)