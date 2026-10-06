"""Webhook payload schemas.

TODO(Meta): Replace with models based on Meta's official Messenger Platform
webhook documentation. Until then the payload is accepted as loosely-typed JSON
and NOT interpreted.
"""
from typing import Any

from pydantic import BaseModel, ConfigDict


class WebhookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    object: str | None = None
    entry: list[dict[str, Any]] = []
