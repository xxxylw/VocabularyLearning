"""Error surfacing: on Windows a native message box, elsewhere stderr.

PRD 边界态 requires explicit user-visible errors (e.g. no free port) —
never a silent failure.
"""

import logging

logger = logging.getLogger(__name__)


def show_error(title: str, message: str) -> None:
    shown = False
    try:
        import ctypes

        MB_ICONERROR = 0x10
        ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
            0, message, title, MB_ICONERROR
        )
        shown = True
    except Exception:  # noqa: BLE001 - fall through to logging
        pass
    if not shown:
        logger.error("%s: %s", title, message)
