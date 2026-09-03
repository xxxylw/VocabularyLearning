"""Pure launcher logic, independent of Windows UI libraries.

Everything in this module is unit-testable on any platform: first-run
database provisioning, port selection, single-instance locking, runtime
info bookkeeping and user settings.
"""

import errno
import json
import os
import socket
import tempfile
from pathlib import Path
from typing import IO, Any

WORKING_DB_TMP_SUFFIX = ".sqlite.tmp"

DEFAULT_SETTINGS: dict[str, Any] = {
    # Offline-first (PRD 第七章 交互规则 6): core flows run fully local;
    # Oxford/Wiktionary lookups are an opt-in online enhancement.
    "online_enrichment": False,
}


# ---------------------------------------------------------------- database


def ensure_working_db(builtin_path: Path, target_path: Path) -> str:
    """Provision the working DB from the read-only builtin library.

    Returns ``"created"`` on first run, ``"exists"`` when the working DB is
    already present (never overwritten — PRD 数据安全: user data must survive
    upgrades and re-unzips).

    The copy lands in a temp file in the *target* directory and is moved into
    place with ``os.replace`` (atomic on the same volume), so an interrupted
    first run leaves at most a stale ``*.sqlite.tmp`` file and no half-built
    working DB. Stale temp files are cleaned on the next start.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    stale_tmp = target_path.with_name(target_path.name + WORKING_DB_TMP_SUFFIX)
    if target_path.exists():
        if stale_tmp.exists():
            _silent_unlink(stale_tmp)
        return "exists"

    if not builtin_path.is_file():
        raise FileNotFoundError(f"builtin library missing: {builtin_path}")

    _silent_unlink(stale_tmp)
    fd, tmp_name = tempfile.mkstemp(
        dir=target_path.parent, suffix=WORKING_DB_TMP_SUFFIX
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with open(builtin_path, "rb") as src, open(tmp_path, "wb") as dst:
            _copy_stream(src, dst)
        os.replace(tmp_path, target_path)
    except BaseException:
        _silent_unlink(tmp_path)
        raise
    return "created"


def _copy_stream(src: Any, dst: Any, chunk_size: int = 1024 * 1024) -> None:
    while True:
        chunk = src.read(chunk_size)
        if not chunk:
            break
        dst.write(chunk)


def _silent_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


# -------------------------------------------------------------------- port

DEFAULT_PORT_RANGE = range(8000, 8200)


def pick_free_port(
    preferred: int | None = 8000,
    candidates: "range | list[int]" = DEFAULT_PORT_RANGE,
    host: str = "127.0.0.1",
) -> int | None:
    """Find a free TCP port, preferring ``preferred`` then ``candidates``.

    Returns ``None`` when nothing in the candidate list can be bound —
    the caller must surface an explicit error (PRD 边界态: never fail
    silently).
    """
    ordered: list[int] = []
    if preferred is not None:
        ordered.append(preferred)
    ordered.extend(p for p in candidates if p != preferred)

    for port in ordered:
        if port < 0 or port > 65535:
            continue
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, port))
        except OSError:
            continue
        finally:
            sock.close()
        return port
    return None


# ------------------------------------------------------------ single instance


def pid_alive(pid: int) -> bool:
    """Best-effort check whether a process with ``pid`` is running."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        else:
            os.kill(pid, 0)
            return True
    except OSError as error:
        if error.errno == errno.ESRCH:
            return False
        # EPERM etc.: the process exists but is owned by someone else.
        return True


def acquire_instance_lock(lock_path: Path) -> IO[str] | None:
    """Create an exclusive instance lock.

    Returns an open file handle (keep it referenced for the process
    lifetime) on success, or ``None`` when another live instance holds the
    lock. A lock whose recorded PID is dead is considered stale and taken
    over — this covers "killed via task manager / power loss" restarts.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            handle = open(lock_path, "x", encoding="utf-8")
        except FileExistsError:
            if attempt == 0 and _lock_is_stale(lock_path):
                _silent_unlink(lock_path)
                continue
            return None
        handle.write(json.dumps({"pid": os.getpid()}))
        handle.flush()
        return handle
    return None


def _lock_is_stale(lock_path: Path) -> bool:
    try:
        info = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(info["pid"])
    except (OSError, ValueError, KeyError):
        return True
    return not pid_alive(pid)


# ------------------------------------------------------------- runtime info


def write_runtime_info(path: Path, info: dict[str, Any]) -> None:
    """Atomically persist the current instance's URL/PID for second launches."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(info, handle)
        os.replace(tmp_path, path)
    except BaseException:
        _silent_unlink(tmp_path)
        raise


def read_runtime_info(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ----------------------------------------------------------------- settings


def load_user_settings(path: Path) -> dict[str, Any]:
    """Load ``settings.json``, merged over defaults; never raises."""
    settings = dict(DEFAULT_SETTINGS)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if isinstance(loaded, dict):
        for key in DEFAULT_SETTINGS:
            if key in loaded:
                settings[key] = loaded[key]
    return settings


def enrichment_source(settings: dict[str, Any]) -> str:
    return "oxford" if settings.get("online_enrichment") else "fallback"
