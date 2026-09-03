"""Launcher core unit tests (platform-independent)."""

import socket
import threading
from pathlib import Path

import pytest

from launcher import core


# ------------------------------------------------------------ ensure_working_db


def _make_db(path: Path, marker: bytes = b"vocab-sqlite-bytes") -> None:
    path.write_bytes(marker)


def test_first_run_copies_builtin_atomically(tmp_path: Path):
    builtin = tmp_path / "builtin.sqlite"
    _make_db(builtin, b"builtin-content")
    target = tmp_path / "user" / "vocabulary.sqlite"

    assert core.ensure_working_db(builtin, target) == "created"
    assert target.read_bytes() == b"builtin-content"
    # no temp residue
    assert list(target.parent.glob("*.sqlite.tmp")) == []


def test_existing_working_db_is_never_overwritten(tmp_path: Path):
    builtin = tmp_path / "builtin.sqlite"
    _make_db(builtin, b"builtin-v2")
    target = tmp_path / "vocabulary.sqlite"
    _make_db(target, b"user-progress")

    assert core.ensure_working_db(builtin, target) == "exists"
    assert target.read_bytes() == b"user-progress"


def test_stale_tmp_from_interrupted_copy_is_cleaned(tmp_path: Path):
    builtin = tmp_path / "builtin.sqlite"
    _make_db(builtin)
    target = tmp_path / "vocabulary.sqlite"
    stale = tmp_path / (target.name + core.WORKING_DB_TMP_SUFFIX)
    _make_db(stale, b"half-written")

    assert core.ensure_working_db(builtin, target) == "created"
    assert not stale.exists()


def test_missing_builtin_raises(tmp_path: Path):
    target = tmp_path / "vocabulary.sqlite"
    with pytest.raises(FileNotFoundError):
        core.ensure_working_db(tmp_path / "nope.sqlite", target)


# -------------------------------------------------------------------- port


def test_pick_free_port_prefers_requested(tmp_path):
    assert core.pick_free_port(preferred=8000) in range(8000, 8200)


def test_pick_free_port_skips_occupied(tmp_path):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    occupied = server.getsockname()[1]

    picked = core.pick_free_port(
        preferred=occupied, candidates=[occupied, occupied + 1, occupied + 2]
    )
    server.close()
    assert picked in (occupied + 1, occupied + 2)


def test_pick_free_port_returns_none_when_all_occupied():
    servers = []
    try:
        for _ in range(3):
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            servers.append(s)
        ports = [s.getsockname()[1] for s in servers]
        assert core.pick_free_port(preferred=ports[0], candidates=ports) is None
    finally:
        for s in servers:
            s.close()


# ------------------------------------------------------------ single instance


def test_instance_lock_blocks_second_holder(tmp_path: Path):
    lock_path = tmp_path / "instance.lock"
    first = core.acquire_instance_lock(lock_path)
    assert first is not None
    try:
        assert core.acquire_instance_lock(lock_path) is None
    finally:
        first.close()


def test_instance_lock_stale_pid_is_taken_over(tmp_path: Path):
    lock_path = tmp_path / "instance.lock"
    # a PID that definitely does not exist
    lock_path.write_text('{"pid": 2147483646}', encoding="utf-8")
    handle = core.acquire_instance_lock(lock_path)
    assert handle is not None
    handle.close()


def test_instance_lock_garbage_is_treated_as_stale(tmp_path: Path):
    lock_path = tmp_path / "instance.lock"
    lock_path.write_text("not json", encoding="utf-8")
    handle = core.acquire_instance_lock(lock_path)
    assert handle is not None
    handle.close()


def test_pid_alive_for_current_process():
    import os

    assert core.pid_alive(os.getpid()) is True
    assert core.pid_alive(-1) is False


# ------------------------------------------------------------- runtime info


def test_runtime_info_roundtrip(tmp_path: Path):
    info_path = tmp_path / "runtime.json"
    info = {"url": "http://127.0.0.1:8000/", "port": 8000, "pid": 123}
    core.write_runtime_info(info_path, info)
    assert core.read_runtime_info(info_path) == info


def test_read_runtime_info_missing_or_broken(tmp_path: Path):
    assert core.read_runtime_info(tmp_path / "missing.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert core.read_runtime_info(broken) is None


# ----------------------------------------------------------------- settings


def test_default_settings_offline_first(tmp_path: Path):
    settings = core.load_user_settings(tmp_path / "settings.json")
    assert settings["online_enrichment"] is False
    assert core.enrichment_source(settings) == "fallback"


def test_settings_enable_online(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text('{"online_enrichment": true}', encoding="utf-8")
    settings = core.load_user_settings(path)
    assert core.enrichment_source(settings) == "oxford"


def test_settings_broken_file_falls_back(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text("]]", encoding="utf-8")
    assert core.load_user_settings(path)["online_enrichment"] is False
