"""Static frontend serving behind VOCAB_STATIC_DIR (P0-5 packaging)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def static_root(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text(
        "<html><body>vocab spa</body></html>", encoding="utf-8"
    )
    (tmp_path / "app.js").write_text("console.log('ok');", encoding="utf-8")
    return tmp_path


def test_serves_index_and_assets_when_configured(static_root: Path, monkeypatch):
    monkeypatch.setenv("VOCAB_STATIC_DIR", str(static_root))
    from app.main import create_app

    with TestClient(create_app()) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "vocab spa" in index.text

        asset = client.get("/app.js")
        assert asset.status_code == 200
        assert "ok" in asset.text

        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True


def test_api_routes_take_precedence_over_static_mount(
    static_root: Path, monkeypatch
):
    (static_root / "api").mkdir()
    (static_root / "api" / "health").write_text("static shadow", encoding="utf-8")
    monkeypatch.setenv("VOCAB_STATIC_DIR", str(static_root))
    from app.main import create_app

    with TestClient(create_app()) as client:
        assert client.get("/api/health").json()["ok"] is True


def test_no_static_mount_without_env(monkeypatch):
    monkeypatch.delenv("VOCAB_STATIC_DIR", raising=False)
    from app.main import create_app

    with TestClient(create_app()) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/health").status_code == 200


def test_missing_static_dir_is_ignored(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VOCAB_STATIC_DIR", str(tmp_path / "does-not-exist"))
    from app.main import create_app

    with TestClient(create_app()) as client:
        assert client.get("/").status_code == 404



# ---------------------------------------------------------------------------
# C-01a designer walkthrough (P2 #5): 旧激活链接 path 形式 301 到 hash 形式
# ---------------------------------------------------------------------------


def test_legacy_verify_email_path_redirects_to_hash_form(monkeypatch):
    """/verify-email?token=… (path form, pre-C-01a emails) used to fall
    through to a bare 404 JSON; it must now 301 to /#/verify-email so
    the SPA's retired-link page explains the switch."""

    monkeypatch.delenv("VOCAB_STATIC_DIR", raising=False)
    from app.main import create_app

    with TestClient(create_app()) as client:
        with_token = client.get(
            "/verify-email?token=abc123", follow_redirects=False
        )
        assert with_token.status_code == 301
        assert with_token.headers["location"] == "/#/verify-email?token=abc123"

        without_token = client.get("/verify-email", follow_redirects=False)
        assert without_token.status_code == 301
        assert without_token.headers["location"] == "/#/verify-email"


def test_legacy_verify_email_redirect_wins_over_static_mount(
    static_root: Path, monkeypatch
):
    """The 301 must keep working in packaged mode (VOCAB_STATIC_DIR
    set) — the route is registered before the static mount."""

    monkeypatch.setenv("VOCAB_STATIC_DIR", str(static_root))
    from app.main import create_app

    with TestClient(create_app()) as client:
        response = client.get(
            "/verify-email?token=xyz", follow_redirects=False
        )
        assert response.status_code == 301
        assert response.headers["location"] == "/#/verify-email?token=xyz"



# ---------------------------------------------------------------------------
# 订阅走查 P2-b: 所有 auth/subscription SPA 路由的 path 形式入口都必须
# 301 到 hash 形式（带/不带 query），静态挂载模式下同样生效。
# ---------------------------------------------------------------------------


SPA_PATHS = (
    "/login",
    "/register",
    "/check-email",
    "/forgot-password",
    "/reset-password",
    "/verify-email",
    "/subscription",
)


@pytest.mark.parametrize("spa_path", SPA_PATHS)
def test_spa_path_redirects_to_hash_form(spa_path: str, monkeypatch):
    """A path-form entry to any auth/subscription SPA route must 301 to
    the hash form, preserving the query string (users typing the URL by
    hand, or old links carrying the path form)."""

    monkeypatch.delenv("VOCAB_STATIC_DIR", raising=False)
    from app.main import create_app

    with TestClient(create_app()) as client:
        without_query = client.get(spa_path, follow_redirects=False)
        assert without_query.status_code == 301
        assert without_query.headers["location"] == f"/#{spa_path}"

        with_query = client.get(
            f"{spa_path}?email=user%40example.com", follow_redirects=False
        )
        assert with_query.status_code == 301
        assert (
            with_query.headers["location"]
            == f"/#{spa_path}?email=user%40example.com"
        )


def test_spa_path_redirect_wins_over_static_mount(static_root: Path, monkeypatch):
    """The 301s must keep working in packaged mode (VOCAB_STATIC_DIR
    set) — the routes are registered before the static mount."""

    monkeypatch.setenv("VOCAB_STATIC_DIR", str(static_root))
    from app.main import create_app

    with TestClient(create_app()) as client:
        for spa_path in SPA_PATHS:
            response = client.get(spa_path, follow_redirects=False)
            assert response.status_code == 301, spa_path
            assert response.headers["location"] == f"/#{spa_path}", spa_path
