"""Application settings, loaded from environment variables / .env.

All values default to empty so the app (and the test suite) can start without
credentials. Code that needs a secret must check it at the point of use.
"""
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    supabase_url: str = ""
    supabase_anon_key: SecretStr = SecretStr("")
    supabase_secret_key: SecretStr = SecretStr("")

    secret_key: SecretStr = SecretStr("")

    meta_verify_token: SecretStr = SecretStr("")
    meta_page_access_token: SecretStr = SecretStr("")

    openai_api_key: SecretStr = SecretStr("")

    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.1-flash-lite"
    
    enable_ngrok: bool = False
    ngrok_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
