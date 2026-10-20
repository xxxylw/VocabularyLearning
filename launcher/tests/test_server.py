"""End-to-end launcher server test: uvicorn thread + health readiness.

Runs the same BackendServer the Windows launcher uses (on 127.0.0.1,
with a temp working DB) and checks it becomes ready and stops cleanly.
"""

import json
import socket
import urllib.request
from pathlib import Path

from launcher.server import BackendServer


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_backend_server_becomes_ready_and_stops(tmp_path: Path, monkeypatch):
    # isolate from the developer environment
    monkeypatch.delenv("VOCAB_DB_PATH", raising=False)
    monkeypatch.delenv("VOCAB_STATIC_DIR", raising=False)
    monkeypatch.setenv("VOCAB_ENRICHMENT_SOURCE", "fallback")

    server = BackendServer(
        host="127.0.0.1",
        port=_free_port(),
        db_path=tmp_path / "vocabulary.sqlite",
        static_dir=None,
        enrichment_source="fallback",
    )
    server.start()
    try:
        assert server.wait_until_ready(timeout_seconds=30)
        with urllib.request.urlopen(
            f"{server.base_url}/api/health", timeout=5
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body["ok"] is True
    finally:
        server.stop()

    assert server._thread is not None and not server._thread.is_alive()
