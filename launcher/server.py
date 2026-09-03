"""Run the FastAPI backend (uvicorn) in a background thread.

Single-process design: the tray launcher and the HTTP server share one
process, so tray Exit terminates the whole application and cannot leave
orphan server processes behind (PRD 数据安全: 退出即停服务).
"""

import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


class BackendServer:
    def __init__(
        self,
        host: str,
        port: int,
        db_path: Path,
        static_dir: Path | None,
        enrichment_source: str = "fallback",
    ) -> None:
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self._db_path = db_path
        self._static_dir = static_dir
        self._enrichment_source = enrichment_source
        self._server = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"{self.base_url}/"

    def start(self) -> None:
        import os

        # Must be set before app.main is imported: create_app() resolves
        # VOCAB_STATIC_DIR at import time of the created module instance
        # and app.db.db_path() reads VOCAB_DB_PATH per connection.
        os.environ["VOCAB_DB_PATH"] = str(self._db_path)
        if self._static_dir is not None:
            os.environ["VOCAB_STATIC_DIR"] = str(self._static_dir)
        else:
            os.environ.pop("VOCAB_STATIC_DIR", None)
        os.environ["VOCAB_ENRICHMENT_SOURCE"] = self._enrichment_source

        import uvicorn

        from app.main import create_app

        config = uvicorn.Config(
            create_app(),
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, name="vocab-backend", daemon=True
        )
        self._thread.start()
        logger.info("backend thread started on %s", self.base_url)

    def wait_until_ready(self, timeout_seconds: float = 60.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        health_url = f"{self.base_url}/api/health"
        while time.monotonic() < deadline:
            if self._server is not None and self._server.should_exit:
                logger.error("backend exited before becoming ready")
                return False
            try:
                with urllib.request.urlopen(health_url, timeout=2) as response:
                    if response.status == 200:
                        return True
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.3)
        return False

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                logger.warning(
                    "backend thread did not stop cleanly within 10s; "
                    "process exit will terminate it"
                )
        logger.info("backend stopped")
