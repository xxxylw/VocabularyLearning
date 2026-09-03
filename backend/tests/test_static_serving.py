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
