from fastapi.testclient import TestClient

from app.main import create_app
from app.version import APP_VERSION


def test_health_returns_ok():
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "version": APP_VERSION}
