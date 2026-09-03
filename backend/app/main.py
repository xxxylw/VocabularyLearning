import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes import router
from app.version import APP_VERSION


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
    app = FastAPI(title="VocabularyLearning", version=APP_VERSION)
    app.include_router(router, prefix="/api")

    # Mounted last so /api routes always win; html=True serves index.html
    # for the root path.
    directory = static_dir()
    if directory is not None:
        app.mount("/", StaticFiles(directory=directory, html=True), name="frontend")

    return app


app = create_app()
