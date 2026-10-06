"""Supabase client factory (server-side only).

Uses the SERVICE ROLE key, which bypasses Row Level Security. This module must
only ever be imported by backend code - never ship this key to a client.
"""
from functools import lru_cache

from supabase import Client, create_client

from app.core.config import get_settings


@lru_cache
def get_supabase_client() -> Client:
    settings = get_settings()
    url = settings.supabase_url
    key = settings.supabase_secret_key.get_secret_value()
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set (see .env.example)."
        )
    return create_client(url, key)
