"""Launcher entrypoint: tray-resident single-instance local app.

Flow (PRD 第七章):
1. single-instance lock (second launch re-opens the study UI and exits);
2. first-run copy of the read-only builtin SQLite into the user data dir;
3. pick a free port (auto-fallback when the default is occupied);
4. start the FastAPI backend thread serving both /api and the built SPA;
5. open the default browser at the study UI;
6. run the tray loop (Open Study / Exit); Exit stops everything.
"""

import logging
import os
import sys
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

from launcher import core, paths
from launcher.notify import show_error
from launcher.server import BackendServer
from launcher.tray import run_tray

logger = logging.getLogger(__name__)

READY_TIMEOUT_SECONDS = 60.0


def _setup_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        logs_dir / "launcher.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # --noconsole builds have no stderr; keep logging alive regardless.
    if sys.stderr is not None:
        root.addHandler(logging.StreamHandler(sys.stderr))


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - browser opening must never kill the app
        logger.exception("failed to open browser at %s", url)


def main() -> int:
    data_dir = paths.app_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        show_error(
            "VocabularyLearning",
            f"无法创建用户数据目录：{data_dir}\n({error})",
        )
        return 1
    _setup_logging(paths.logs_dir(data_dir))
    logger.info("starting VocabularyLearning launcher (data dir: %s)", data_dir)

    lock = core.acquire_instance_lock(paths.instance_lock_path(data_dir))
    if lock is None:
        # Already running: bring the existing instance's UI back up.
        info = core.read_runtime_info(paths.runtime_info_path(data_dir))
        url = (info or {}).get("url")
        if url:
            logger.info("another instance is running at %s", url)
            _open_browser(url)
        else:
            logger.warning(
                "another instance holds the lock but no runtime info found"
            )
            # PRD boundary rule: never fail silently. The lock is held by an
            # instance that never reached readiness - typically a lingering
            # error dialog from an earlier crashed run. Tell the user instead
            # of exiting quietly.
            show_error(
                "VocabularyLearning",
                "检测到另一个实例正在运行但尚未就绪：\n\n"
                "· 如果刚刚启动，请等待几秒再看；\n"
                "· 如果之前弹出过报错窗口，请先关闭它，"
                "然后重新双击本程序。",
            )
        return 0

    try:
        return _run_first_instance(data_dir)
    finally:
        try:
            lock.close()
        except OSError:
            pass


def _run_first_instance(data_dir: Path) -> int:
    builtin = paths.builtin_db_path()
    if not builtin.is_file():
        message = (
            "未找到内置词库：\n"
            f"{builtin}\n\n"
            "请确认应用解压目录完整后重试。"
        )
        logger.error(message.replace("\n", " "))
        show_error("VocabularyLearning", message)
        return 1

    working_db = paths.working_db_path(data_dir)
    try:
        status = core.ensure_working_db(builtin, working_db)
    except OSError as error:
        logger.exception("working db provisioning failed")
        show_error(
            "VocabularyLearning",
            f"初始化用户数据失败：{error}",
        )
        return 1
    logger.info("working db: %s (%s)", working_db, status)

    port = core.pick_free_port(preferred=8000)
    if port is None:
        message = (
            "未找到可用端口（已尝试 8000-8199）。\n"
            "请关闭占用这些端口的程序后重试。"
        )
        logger.error(message.replace("\n", " "))
        show_error("VocabularyLearning", message)
        return 1

    settings = core.load_user_settings(paths.settings_path(data_dir))
    server = BackendServer(
        host="127.0.0.1",
        port=port,
        db_path=working_db,
        static_dir=paths.static_dir(),
        enrichment_source=core.enrichment_source(settings),
    )
    try:
        server.start()
    except Exception:  # noqa: BLE001 - report and bail out cleanly
        logger.exception("backend failed to start")
        show_error(
            "VocabularyLearning",
            "本地服务启动失败，详见日志：\n"
            f"{paths.logs_dir(data_dir) / 'launcher.log'}",
        )
        return 1

    if not server.wait_until_ready(READY_TIMEOUT_SECONDS):
        server.stop()
        show_error(
            "VocabularyLearning",
            "本地服务未能就绪（60 秒超时），详见日志：\n"
            f"{paths.logs_dir(data_dir) / 'launcher.log'}",
        )
        return 1

    core.write_runtime_info(
        paths.runtime_info_path(data_dir),
        {"url": server.url, "port": port, "pid": os.getpid()},
    )
    logger.info("ready at %s", server.url)
    _open_browser(server.url)

    run_tray(
        server.url,
        on_open_study=lambda: _open_browser(server.url),
        on_exit=lambda: None,
    )

    server.stop()
    logger.info("launcher exited cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
