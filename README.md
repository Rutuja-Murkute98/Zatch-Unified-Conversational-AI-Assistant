<div align="center">

# 🛍️ Zatch Unified Conversational AI Assistant

**The in-app AI shopping assistant for Zatch** — a social-commerce platform
blending e-commerce, live selling, short-form video ("Bits") and
price bargaining.

[![Python](https://img.shields.io/badge/Python-3.13%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![MongoDB](https://img.shields.io/badge/MongoDB-Atlas-47A248?logo=mongodb&logoColor=white)](https://www.mongodb.com/atlas)
[![uv](https://img.shields.io/badge/deps-uv-de5fe9)](https://github.com/astral-sh/uv)
[![Docker](https://img.shields.io/badge/deploy-Docker-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Tests](https://img.shields.io/badge/tests-375%20passing-brightgreen)](#-testing)

*"Where's my order?" · "Any blue jackets under ₹2000?" · "Can I bargain on this?"*

</div>

---

## 📖 What this is

The Zatch mobile app sends a user's chat message plus their existing Zatch
JWT. This service verifies that token, resolves the question against
**live MongoDB Atlas data**, and replies in natural language — powered by an
LLM tool-calling loop with **34 real data lookups** across orders, the
catalogue, bargaining, live sessions, Bits, reviews and the user's own
account.

It never invents an answer. Every reply is grounded in a real database read.

## 📑 Table of contents

- [Core guarantees](#-core-guarantees)
- [What it can talk about](#-what-it-can-talk-about)
- [Project layout](#-project-layout)
- [Setup](#-setup)
- [Running it](#-running-it)
- [Testing](#-testing)
- [Deployment](#-deployment)
- [What the mobile app needs](#-what-the-mobile-app-needs)

---

## 🔒 Core guarantees

This project is built around one non-negotiable rule: **it reads, it never
writes.**

| Guarantee | How it's enforced |
|---|---|
| **Read-only, structurally** | Enforced at the **Atlas database user role** — not just in application code. Even a bug in this service cannot write to production data. The assistant reports cancellation *eligibility*; it never cancels anything. |
| **Identity comes only from a verified JWT** | `user_id` is not a parameter in any of the 34 tool schemas the model can call — there is no field for the model to fill in with someone else's ID. It is injected **server-side**, from the decoded token, by `tool_executor.py`. |
| **Three independent data-safety layers** | Read-only DB role → a per-collection **field allowlist** used as the actual MongoDB projection → an independent **response sanitizer** that re-strips anything not allowlisted, even if it somehow slipped past the first two. |
| **Tool results are data, never instructions** | Product names, reviews, and hashtags are written by sellers and shoppers. The system prompt explicitly treats their content as untrusted — never as a command the assistant should follow. |

## 💬 What it can talk about

<table>
<tr><td width="50%" valign="top">

**📦 Orders & delivery**
- Order status & history
- Delivery estimates & tracking
- Invoices
- Cancellation *eligibility* (read-only)

**🛒 Catalogue**
- Category / price / variant search
- Product detail, stock by size & color
- "Anything similar to X" (vector search)
- Trending & personalized picks

</td><td width="50%" valign="top">

**🤝 Bargaining** *(Zatch's signature feature)*
- Eligibility & seller rules
- Suggested offer amounts
- Offer status & counter-offers

**📺 Live selling & Bits**
- What's live now, session recaps
- Trending Bits, hashtag search

**⭐ Social & account**
- Reviews, seller trust signals
- Cart, coupons, saved items, notifications

</td></tr>
</table>

## 🗂️ Project layout

```
app/
├── agent/       LLM tool-calling loop, tool schemas, provider client
├── api/         FastAPI app, /chat + /health routes, request schemas
├── config/      Settings loader, structured logging with redaction
├── db/          Shared async MongoDB connection
├── memory/      Per-session conversation history (in-process / Redis)
├── repos/       Data access — one file per collection, allowlisted
├── security/    JWT verification, field allowlists, response sanitizer
└── tests/       Pytest suite (375 tests; most need no live database)
docs/            Implementation plan, requirements, schema reference
scripts/         Dev-only helpers, not part of the deployed app
```

<details>
<summary><strong>Deliberately not reachable from chat</strong> — click to expand</summary>
<br>

Not everything in `repos/` is wired into a tool, and the gaps are **choices**,
not oversights — each one is explained in the file that owns it:

| Code | Why it is not a tool |
|---|---|
| `seller_repo` (PDF §11) | The demo is buyer-side end to end. Four more schemas would be re-sent on every round for a capability no demo question reaches. The file documents exactly what wiring it up safely requires — chiefly that `seller_id` must never become a model parameter. |
| `categories_repo` | The assistant does need real category names; it gets them from the system prompt, which is cheaper than a tool round-trip. This backs `scripts/check_categories.py`. |

`app/tests/test_tool_registry.py` keeps the tool surface honest: a schema
with no implementation, an implementation with no schema, or any schema that
lets the model name *whose* data it wants — all fail the suite.

</details>

---

## 🚀 Setup

```bash
uv sync
```

Copy `.env.example` → `.env` and fill in the values:

| Variable | Required | Purpose |
|---|:---:|---|
| `MONGODB_URI` | ✅ | Atlas connection string (read-only service account) |
| `LLM_API_KEY` | ✅ | Primary LLM provider (Groq) |
| `JWT_SECRET` | ✅ | Must match the main Zatch backend's signing secret |
| `GEMINI_API_KEY` | – | Fallback provider; app runs on Groq alone without it |
| `DEMO_UI_ENABLED` | – | Serves the demo chat page at `/demo`. Defaults **true**; set `false` for public deployments |
| `BACKUP_LLM_*` | – | A second provider whose terms exclude training on prompts — the only thing that stops Azure being a single point of failure against real data (see `HANDOFF.md`) |
| `REDIS_URL` | – | Shared conversation memory & rate-limit counts. Required for more than one worker |

<details>
<summary><strong>Running more than one worker</strong></summary>
<br>

`REDIS_URL` is optional, and the app starts and answers correctly without it.
Two pieces of state are then held **per process**, and both are silently
wrong the moment there is a second worker:

- **Conversation history** — a follow-up landing on another worker reads as a
  new conversation, so "anything similar?" answers "similar to what?"
- **The `/chat` rate limit** — each worker grants the full allowance, so the
  real limit becomes `workers × CHAT_RATE_LIMIT_REQUESTS`

Both fall back deliberately rather than refusing to start — a cache outage
should degrade the assistant, not take it down. Startup logs
`redis_not_configured` (or `redis_unavailable`) with the consequence spelled
out. `scripts/check_redis.py` proves a configured URL actually works.

</details>

---

## ▶️ Running it

```bash
uv run uvicorn app.api.main:app --reload
```

| Surface | URL |
|---|---|
| Interactive API docs | http://127.0.0.1:8000/docs |
| Demo chat UI | http://127.0.0.1:8000/demo |

### Two endpoints, one conversation

| Endpoint | Shape | Use |
|---|---|---|
| `POST /chat` | JSON in, JSON out | The contract the mobile app was written against. Unchanged. |
| `POST /chat/stream` | JSON in, Server-Sent Events out | The same answer, delivered as it's produced. |

They are **not** two implementations — both call the same
`run_conversation()`, and the only difference is that the streaming one
passes a callback. `/chat/stream` emits `status` events naming each lookup as
it runs, `token` events carrying the answer as the model writes it, and a
final `done` event with the authoritative reply.

> Most of an answer's latency is tool rounds that produce no readable text —
> the `status` events matter more than the tokens, since the wait is filled
> by *saying what's being looked up* rather than a spinner.

Generate a token to try `/chat` while waiting on the real backend secret:

```bash
uv run python scripts/generate_test_token.py
```

---

## 🧪 Testing

The suite runs against real sandbox data rather than fixtures, so most of it
needs a live `MONGODB_URI`.

```bash
uv run pytest -v
```

**274 of 375 tests** need no database at all and run anywhere:

```bash
uv run pytest -m "not needs_db"
```

The `needs_db` marker is applied automatically by `conftest.py` to anything
requesting the `db` fixture — never written by hand, so it can't drift as
tests are added. CI runs that subset plus a container build; it can't run
the rest, since Atlas allow-lists IP addresses and GitHub runners have none.

---

## 🐳 Deployment

```bash
docker build -t zatch-assistant .
```

~270MB image, runs as an unprivileged user, ships with no `.env` and no dev
scripts inside.

See **[docs/deployment.md](docs/deployment.md)** for full configuration —
why `WEB_CONCURRENCY` defaults to `1`, how to read `/health`, and a
ready-to-use **[render.yaml](render.yaml)** blueprint for a safe, demo-data
deployment on Render.

## 📱 What the mobile app needs

A base URL and the Zatch JWT the app already issues at login — nothing else
changes.

```
Authorization: Bearer <jwt>
```

| Endpoint | Shape |
|---|---|
| `POST /chat` | JSON in, JSON out |
| `POST /chat/stream` | Server-Sent Events: `status`, `token`, `done`, `error` |

Both take `{"message": "...", "session_id": "..."}`, and both sit behind the
same auth and rate limit. Adopt streaming whenever the client is ready — the
non-streaming endpoint isn't deprecated and runs the identical loop.

---

<div align="center">

Built for **Zatch** · read-only by design · every answer backed by a real query

</div>
