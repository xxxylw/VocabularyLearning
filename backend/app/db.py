from pathlib import Path
import os
import sqlite3


def db_path() -> Path:
    configured_path = os.environ.get("VOCAB_DB_PATH")
    if configured_path:
        return Path(configured_path)
    return Path("./data/vocabulary.sqlite")


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    migrate(connection)
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    connection.executescript(schema_path.read_text(encoding="utf-8"))
