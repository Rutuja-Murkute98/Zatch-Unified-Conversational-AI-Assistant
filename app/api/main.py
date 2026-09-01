"""
WHAT:
    The FastAPI application itself - wires database connect/disconnect
    into app startup/shutdown, and registers all routes.

FLOW:
    Run with: uv run uvicorn app.api.main:app --reload
    Then visit http://127.0.0.1:8000/docs for interactive testing,
    or http://127.0.0.1:8000/demo for the chat surface.

WHY create_app() AND NOT JUST A MODULE-LEVEL APP:
    One route is conditional - /demo is only mounted when
    DEMO_UI_ENABLED is on - and a router registered at import time
    cannot be un-registered afterwards. A factory means the decision is
    made where the settings are read, and means a test can build an app
    with the flag off instead of asserting against the one arrangement
    that happened to be imported first.

    `app` is still created at module level, so `uvicorn
    app.api.main:app` is unchanged.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agent.llm_client import close_llm_client, log_provider_preflight
from app.api.routes import chat, demo_ui, health
from app.config.logging import configure_logging
from app.config.settings import get_settings
from app.db.connection import close_mongo_connection, connect_to_mongo
from app.db.redis_client import close_redis
from app.security.auth import log_secret_preflight

import structlog

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # FIRST, before anything that logs. Until this runs, structlog uses
    # its default config and our redaction processor never executes -
    # which would put raw tool arguments and user ids in the logs.
    configure_logging()
    # Before serving anything: say whether one provider failing would
    # take the whole service down, and how long the Azure credit has
    # left. Startup is the moment an operator is actually reading.
    log_provider_preflight()
    log_secret_preflight()

    # SAID OUT LOUD, EVERY START, because the default is ON and the
    # consequence of leaving it on is invisible from inside the app: a
    # public deployment serves a chat page to anyone who finds the URL.
    # It cannot answer without a valid Zatch token, so it is a surface
    # rather than a leak - but a surface nobody remembers mounting.
    if getattr(app.state, "demo_ui_enabled", False):
        logger.info(
            "demo_ui_enabled",
            path="/demo",
            consequence="a chat page is served to anyone who can reach this "
                        "service; set DEMO_UI_ENABLED=false for a public deployment",
        )
    else:
        logger.info("demo_ui_disabled")

    await connect_to_mongo()
    yield
    await close_mongo_connection()
    await close_llm_client()
    await close_redis()


def create_app() -> FastAPI:
    settings = get_settings()

    application = FastAPI(title="Zatch Chatbot API", lifespan=lifespan)
    application.include_router(chat.router)
    application.include_router(health.router)

    # A chat surface for demos - /docs shows plumbing, this shows the
    # product. DEFAULT ON, so the demo and local development work with
    # no extra configuration; turn it off for anything public, where it
    # is an unnecessary door that invites people to paste tokens into a
    # page. See docs/deployment.md.
    application.state.demo_ui_enabled = settings.demo_ui_enabled
    if settings.demo_ui_enabled:
        application.include_router(demo_ui.router)

    return application


app = create_app()
