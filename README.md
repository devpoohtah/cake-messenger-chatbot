# Mrs Brave's Cake — AI Messenger Ordering Automation (Backend Foundation)

BAM 255 Business Analysis for IT project. Facebook Page: **Stressware**.

## Purpose
Let customers ask about cakes and place orders through Facebook Messenger. An AI
interprets messages into **structured data**; the backend **validates** it and,
only after the customer **explicitly confirms**, saves the order to Supabase
PostgreSQL. The AI never writes to the database directly.

## Status — read this first
This is a **foundation only**. **Messenger and AI integration are NOT implemented.**

| Done | Not done |
|---|---|
| FastAPI app, `/`, `/health` | Meta webhook verification (`GET /webhook` returns 501) |
| Supabase schema + 5 seeded products | Parsing Messenger events (`POST /webhook` just acknowledges) |
| Order validation, totals, atomic order creation | AI intent interpretation (`ai_service.py` is a stub) |
| Confirmation gate (no confirm → no order) | Sending Messenger replies / order form |
| Unit tests (no DB or credentials needed) | Conversation state, order form UI, admin tooling |

## Architecture
```
Messenger -> (ngrok) -> POST /webhook -> messenger_service -> ai_service (structured intent)
                                                        \-> order_service (validate) -> Supabase RPC -> PostgreSQL
```
```
app/
  main.py                  FastAPI app
  core/config.py           Settings from .env (pydantic-settings)
  api/routes/              health.py, webhook.py (placeholders)
  services/                ai_service, messenger_service (stubs), order_service (real logic)
  schemas/                 order.py (validated order data), webhook.py (placeholder)
  db/supabase.py           Server-side Supabase client (service role key)
supabase/schema.sql        Tables, indexes, RLS, RPC function, seed data
tests/
```

Order statuses: `pending` (customer confirmed, business not yet completed it),
`completed`, `cancelled`. Orders are inserted atomically by the Postgres function
`create_order_with_items`, so an order and its items can never be partially saved.
Unit prices come from the `products` table at order time; change prices with an
`UPDATE` — no code change needed.

## Setup
Requires Python 3.11+.

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### Supabase
1. Create a project at supabase.com.
2. Open **SQL Editor**, paste `supabase/schema.sql`, and run it (safe to re-run).
3. Check **Table Editor**: `products` should contain 5 rows.
4. Copy the project URL and API keys from the project's API settings.

### Environment
```bash
cp .env.example .env     # Windows: copy .env.example .env
```
Fill in `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` now; the Meta and OpenAI
values can stay empty until those steps. `.env` is git-ignored — never commit it.
The service role key bypasses Row Level Security: keep it server-side only, and
never put it (or the Meta Page Access Token) in client code or logs.

## Run
```bash
uvicorn app.main:app --reload --port 8000
```
- http://localhost:8000/ and http://localhost:8000/health
- Interactive docs: http://localhost:8000/docs

## Test
```bash
pytest
```
Tests use no network, database, or credentials.

## Exposing the webhook with ngrok (later)
```bash
ngrok http 8000
```
Meta will be pointed at `https://<ngrok-url>/webhook`. Free ngrok URLs change
on restart, so the callback URL must be updated in Meta each time.

## Remaining work
1. Implement `GET /webhook` verification (`hub.mode`, `hub.verify_token`, `hub.challenge`) per Meta's docs.
2. Implement `POST /webhook` event parsing and request validation per Meta's docs.
3. Implement `messenger_service` (send text, order form).
4. Implement `ai_service` (LLM → `AIResult`).
5. Add conversation state (what the customer is ordering, awaiting confirmation).
6. Wire the flow: intent → collect details → show total → explicit confirm → `create_order`.
7. Decide how the business views/updates orders (e.g. Supabase dashboard for now).

## Known limitations
- Customers without a `messenger_id` are matched by contact number + name, so duplicates are possible.
- Max quantity per item is capped at 50 (`MAX_QUANTITY_PER_ITEM`).
- No authentication on any endpoint yet (nothing sensitive is exposed).
