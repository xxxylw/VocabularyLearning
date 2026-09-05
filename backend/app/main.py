import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import emailing
from app.routes import health, router
from app.routes_auth import router as auth_router
from app.version import APP_VERSION

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Startup self-check (P0 follow-up): Brevo answers 201 even when the
    # sender is not validated and then silently drops the email, so the
    # app itself must surface the misconfiguration at boot. The check
    # only logs — it must never block or crash application startup.
    try:
        emailing.verify_sender_configuration()
    except Exception:  # noqa: BLE001 — self-check must never block boot
        logger.warning("Brevo sender self-check raised unexpectedly", exc_info=True)
    yield


def static_dir() -> Path | None:
    """Directory holding the built frontend (SPA) served at ``/``.

    Configured via ``VOCAB_STATIC_DIR`` by the packaged Windows launcher so
    the browser talks to a single origin (``/api/*`` + static assets). In
    developer mode the variable is unset and no static files are mounted,
    which keeps the Vite dev server the single source of frontend truth.
    """
    configured = os.environ.get("VOCAB_STATIC_DIR")
    if not configured:
        return None
    path = Path(configured)
    return path if path.is_dir() else None


def create_app() -> FastAPI:
    app = FastAPI(title="VocabularyLearning", version=APP_VERSION, lifespan=_lifespan)
    # /api/health is anonymous (used by the launcher and uptime checks);
    # every other study endpoint on ``router`` is guarded by
    # app.auth.require_user.
    app.include_router(router, prefix="/api")
    app.include_router(auth_router, prefix="/api")
    app.get("/api/health")(health)

    # Mounted last so /api routes always win; html=True serves index.html
    # for the root path.
    directory = static_dir()
    if directory is not None:
        app.mount("/", StaticFiles(directory=directory, html=True), name="frontend")

    return app


app = create_app()
