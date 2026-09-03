"""Filesystem layout resolution for the packaged app.

Two disjoint areas matter (PRD 第七章 交互规则 4/5):

- install dir (read-only): the folder the user unzipped — executable,
  ``builtin/vocabulary.sqlite`` (read-only baseline library) and ``static/``
  (built frontend). Never written to at runtime.
- user data dir: ``%APPDATA%\\VocabularyLearning`` — working SQLite DB,
  settings, instance lock, runtime info and logs. One folder per Windows
  account, so multiple accounts never overwrite each other.

``VOCAB_DATA_DIR`` overrides the user data dir for tests and development.
"""

import os
import sys
from pathlib import Path

APP_DIR_NAME = "VocabularyLearning"


def app_data_dir() -> Path:
    """Writable per-user data directory."""
    configured = os.environ.get("VOCAB_DATA_DIR")
    if configured:
        return Path(configured)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_DIR_NAME
    # Non-Windows development fallback so the launcher stays runnable
    # outside Windows without touching a real home config location.
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def install_dir() -> Path:
    """Directory of the packaged application (never written at runtime)."""
    if getattr(sys, "frozen", False):
        # PyInstaller onedir: sys.executable lives next to _internal/.
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def builtin_db_path() -> Path:
    return install_dir() / "builtin" / "vocabulary.sqlite"


def static_dir() -> Path:
    return install_dir() / "static"


def working_db_path(data_dir: Path) -> Path:
    return data_dir / "vocabulary.sqlite"


def settings_path(data_dir: Path) -> Path:
    return data_dir / "settings.json"


def runtime_info_path(data_dir: Path) -> Path:
    return data_dir / "runtime.json"


def instance_lock_path(data_dir: Path) -> Path:
    return data_dir / "instance.lock"


def logs_dir(data_dir: Path) -> Path:
    return data_dir / "logs"
