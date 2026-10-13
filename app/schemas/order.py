"""Pydantic schemas for order data. Everything the AI produces must pass through these."""
import re
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-]{6,18}[0-9]$")


class OrderItemIn(BaseModel):
    product_name: str
    # Quantity range is enforced in order_service.validate_quantity (single source of truth).
    quantity: int

    @field_validator("product_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("product_name must not be empty")
        return v


class OrderRequest(BaseModel):
    """Structured order data. `confirmed` must be True or no order is created."""

    customer_name: str
    contact_number: str
    messenger_id: str | None = None
    location: str
    items: list[OrderItemIn] = Field(min_length=1)
    fulfillment: Literal["delivery", "pickup"] = "delivery"
    notes: str | None = None
    dedication: str | None = None  # the message for the dedication card
    delivery_area: str | None = None  # city or municipality of a delivery order
    delivery_fee: Decimal | None = Field(default=None, ge=0)  # None on a delivery order = the owner will confirm it
    confirmed: bool = False

    @field_validator("notes")
    @classmethod
    def _clean_notes(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if len(v) > 200:
            raise ValueError("notes must be 200 characters or fewer")
        return v or None

    @field_validator("delivery_area")
    @classmethod
    def _clean_area(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if len(v) > 100:
            raise ValueError("delivery_area must be 100 characters or fewer")
        return v or None

    @field_validator("dedication")
    @classmethod
    def _clean_dedication(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if len(v) > 80:
            raise ValueError("dedication must be 80 characters or fewer")
        return v or None

    @field_validator("customer_name", "location")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("contact_number")
    @classmethod
    def _valid_phone(cls, v: str) -> str:
        v = v.strip()
        if not _PHONE_RE.match(v):
            raise ValueError("contact_number must be 8-20 digits (spaces, dashes, leading + allowed)")
        return v

    @field_validator("messenger_id")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        return (v or "").strip() or None


class OrderItemCreated(BaseModel):
    product_id: int
    product_name: str
    quantity: int
    unit_price: Decimal
    subtotal: Decimal


class OrderCreated(BaseModel):
    order_id: int
    customer_id: int
    status: str
    total_amount: Decimal
    location: str
    order_date: datetime
    items: list[OrderItemCreated]
