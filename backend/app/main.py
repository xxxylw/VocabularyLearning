import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import emailing
from app import db as db_module
from app.routes import health, router
from app.routes_auth import router as auth_router
from app.routes_subscription import router as subscription_router
from app.version import APP_VERSION

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # 2026-09-07 事故修复：启动时完成 schema 迁移 + WAL 切换（connect() 里
    # 的 per-path 一次性 migrate，见 app/db.py），确保进入服务态后读路径
    # 连接不再为迁移抢写锁。失败则让启动直接失败——DB 不可用时服务无法
    # 提供任何功能，与其带病 502 不如让 systemd 状态一目了然。
    db_module.warm_up()

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
    app.include_router(subscription_router, prefix="/api")
    app.get("/api/health")(health)

    # C-01a designer walkthrough (P2 #5) + 订阅走查 P2-b: the SPA routes
    # on the URL *hash*, so a path-form entry (/login, /verify-email?token=…,
    # …) has no matching FastAPI route and falls through to a bare 404 JSON
    # (in packaged mode the static mount answers 404 for it too — it only
    # serves index.html for the root path). 301 every auth/subscription
    # entry to its hash form, preserving the query string, so users typing
    # the path by hand (or old emails carrying the path form) land in the
    # SPA instead of a 404. Registered before the static mount so these
    # routes always win.
    spa_routes = (
        "/login",
        "/register",
        "/check-email",
        "/forgot-password",
        "/reset-password",
        "/verify-email",
        "/subscription",
    )

    def spa_path_redirect(spa_path: str):
        def redirect(request: Request) -> RedirectResponse:
            target = f"/#{spa_path}"
            if request.url.query:
                target = f"{target}?{request.url.query}"
            return RedirectResponse(url=target, status_code=301)

        return redirect

    for spa_path in spa_routes:
        app.get(spa_path)(spa_path_redirect(spa_path))

    # Mounted last so /api routes always win; html=True serves index.html
    # for the root path.
    directory = static_dir()
    if directory is not None:
        app.mount("/", StaticFiles(directory=directory, html=True), name="frontend")

    return app


app = create_app()
