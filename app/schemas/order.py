"""Pydantic schemas for order data. Everything the AI produces must pass through these."""
import re
from datetime import datetime
from decimal import Decimal

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
    confirmed: bool = False

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
