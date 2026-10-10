"""Webhook placeholders. No Meta behavior is implemented yet."""
import logging
from app.services.messenger_service import MessengerService, extract_text_messages
from app.services.conversation_service import deliver_replies, handle_incoming_message
import secrets
from typing import Any

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core.config import get_settings

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


@router.get("/webhook")
def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    expected = get_settings().meta_verify_token.get_secret_value()

    if (
        hub_mode == "subscribe"
        and expected  # never accept a match against an unset token
        and hub_verify_token
        and hub_challenge is not None
        and secrets.compare_digest(hub_verify_token, expected)
    ):
        return PlainTextResponse(content=hub_challenge, status_code=200)

    return JSONResponse(status_code=403, content={"detail": "Verification failed."})


@router.post("/webhook")
def receive_event(payload: dict[str, Any] = Body(default_factory=dict)):
    messenger = MessengerService()
    for msg in extract_text_messages(payload):
        # DEV ONLY: remove the text from logs later, it is customer data.
        logger.info("Incoming from %s: text=%r payload=%r", msg.sender_id, msg.text, msg.payload)
        messenger.send_action(msg.sender_id, "mark_seen")
        if msg.payload is None:  # typed text may wait for the AI, so show "typing..."
            messenger.send_action(msg.sender_id, "typing_on")
        replies = handle_incoming_message(msg.sender_id, msg.text, msg.message_id, msg.payload)
        if not replies and msg.payload is None:  # the owner has this customer: the bot stays quiet
            messenger.send_action(msg.sender_id, "typing_off")
        deliver_replies(msg.sender_id, replies, messenger)
    return {"status": "received"}
