"""All customer-facing text, grouped by language. Add languages here (step 3)."""

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "greeting": "Hi! Welcome to Mrs Brave's Cake 🍰\nWhat would you like to do?",
        "btn_order_now": "Order now",
        "btn_menu": "See menu",
        "btn_order_this": "Order this",
        "menu_intro": "Here are our cakes:",
        "menu_empty": "Sorry, no cakes are available right now.",
        "price_line": "{name} — {price}{desc}\n\nWould you like to order?",
        "product_unavailable": "Sorry, {name} is currently unavailable.",
        "ordering_info": (
            "To order, tap Order now, pick your cakes, and I'll ask for your name, contact "
            "number and delivery address. You'll see the total before anything is placed, "
            "and your order is only placed after you confirm it."
        ),
        "fallback": "Sorry, I didn't catch that. I can show you our cakes and prices, or take an order.",
        "ask_product": "Which cake would you like? Tap Order this on a card:",
        "ask_quantity": "How many {name} would you like? Tap a number or type it.",
        "ask_add_more": "Your order so far:\n{cart}\n\nWould you like to add another cake?",
        "btn_add_more": "Add another",
        "btn_done": "That's all",
        "ask_name": "May I have your name?",
        "ask_contact": "What's your contact number? Tap the button to use the number on your Messenger profile, or type it.",
        "ask_location": "What's your delivery address?",
        "summary": (
            "Here's your order:\n{items}\nTotal: {total}\n\n"
            "Name: {name}\nContact: {contact}\nDelivery address: {location}\n\n"
            "Tap Confirm to place your order."
        ),
        "btn_confirm": "✅ Confirm",
        "btn_cancel": "❌ Cancel",
        "awaiting_hint": "Please tap Confirm to place your order, or Cancel to stop.",
        "cancelled": "No problem, your order was cancelled. Tap Order now whenever you'd like to order again.",
        "nothing_to_confirm": "There's no order waiting for confirmation. Tap Order now to start one.",
        "order_received": "✅ Your order #{order_id} has been received!\n{items}\nTotal: {total}\nStatus: {status}",
        "order_failed_retry": "Sorry, I couldn't save your order just now. Please tap Confirm to try again.",
        "order_rejected": "Sorry, I couldn't place that order. Let's start again.",
        "invalid_order": "Sorry, something in that order isn't valid. Let's start again.",
        "error": "Sorry, something went wrong on our side. Please try again in a moment.",
    },
}


def t(lang: str, key: str, **kwargs: object) -> str:
    """Look up a message in the customer's language, falling back to English."""
    text = MESSAGES.get(lang, {}).get(key) or MESSAGES["en"][key]
    return text.format(**kwargs) if kwargs else text