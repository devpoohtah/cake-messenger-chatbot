from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health, webhook
from app.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    public_url = None

    if settings.enable_ngrok:
        from pyngrok import ngrok

        ngrok.kill()
        public_url = ngrok.connect(settings.ngrok_port).public_url
        print(f"\n🌐 Public URL: {public_url}")
        print(f"🌐 Meta webhook URL: {public_url}/webhook\n")

    yield

    if public_url:
        from pyngrok import ngrok
        ngrok.disconnect(public_url)


app = FastAPI(title="Mrs Brave's Cake - AI Messenger Ordering", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(webhook.router)