"""System tray integration (Windows) with graceful non-GUI fallback.

The tray is the application's only persistent UI (PRD 交互规则 2/3): while
running, a tray icon stays resident with at least "Open Study" and "Exit".
Closing the browser tab does NOT stop the service — only tray Exit does.

``pystray`` is imported lazily so headless/Linux environments can still run
the launcher core without the GUI dependency installed.
"""

import logging
import threading

logger = logging.getLogger(__name__)


def make_icon_image():
    """Draw the tray icon (blue rounded square with a white "V")."""
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (2, 2, size - 2, size - 2), radius=14, fill=(37, 99, 235, 255)
    )
    draw.line((20, 18, 32, 46), fill=(255, 255, 255, 255), width=7)
    draw.line((32, 46, 44, 18), fill=(255, 255, 255, 255), width=7)
    return image


def run_tray(url: str, on_open_study, on_exit) -> None:
    """Block until the user exits via the tray menu.

    ``on_open_study`` is called (in the tray thread) when the user picks
    "Open Study"; ``on_exit`` when the user picks "Exit". Returns once the
    tray is stopped.
    """
    try:
        import pystray
    except ImportError:
        logger.warning(
            "pystray unavailable; running without tray icon "
            "(close the process to exit)"
        )
        _block_forever()
        return

    menu = pystray.Menu(
        pystray.MenuItem("Open Study", lambda *_: on_open_study(), default=True),
        pystray.MenuItem("Exit", lambda *_: on_exit()),
    )
    icon = pystray.Icon(
        "VocabularyLearning", make_icon_image(), "VocabularyLearning", menu
    )
    icon.run()
    logger.info("tray stopped")


def _block_forever() -> None:
    event = threading.Event()
    event.wait()
