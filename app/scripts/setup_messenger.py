"""One-time Messenger setup: Get Started button, greeting and persistent menu.

Run from the project root:
    python -m scripts.setup_messenger          # set everything
    python -m scripts.setup_messenger delete   # remove everything again

Run it again whenever you change the text below. Menu changes can take a few
minutes to appear; closing and reopening the chat helps.
"""
import sys

import httpx

from app.core.config import get_settings
from app.services.messenger_service import GRAPH_API_VERSION

# Meta limits: greeting 160 characters, menu title 30 characters, 3 top-level menu items.
# \U0001F370 is the cake emoji.
GREETING = "Hi! Welcome to Mrs Brave's Cake \U0001F370 Tap Get Started to see our cakes and place an order."
MENU_ITEMS = [  # (title, payload): the payloads are the same ones the bot's buttons use
    ("See menu", "MENU"),
    ("Order now", "START_ORDER"),
    ("Talk to the owner", "TALK_OWNER"),
]


def build_profile() -> dict:
    return {
        "get_started": {"payload": "GET_STARTED"},
        "greeting": [{"locale": "default", "text": GREETING}],
        "persistent_menu": [
            {
                "locale": "default",
                "composer_input_disabled": False,  # customers can still type
                "call_to_actions": [
                    {"type": "postback", "title": title, "payload": payload} for title, payload in MENU_ITEMS
                ],
            }
        ],
    }


def main() -> None:
    token = get_settings().meta_page_access_token.get_secret_value()
    if not token:
        sys.exit("META_PAGE_ACCESS_TOKEN is not set in .env")
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messenger_profile"
    if len(sys.argv) > 1 and sys.argv[1] == "delete":
        response = httpx.request(
            "DELETE", url, params={"access_token": token},
            json={"fields": ["get_started", "greeting", "persistent_menu"]}, timeout=15,
        )
    else:
        response = httpx.post(url, params={"access_token": token}, json=build_profile(), timeout=15)
    print(response.status_code, response.text)  # the token is never printed


if __name__ == "__main__":
    main()