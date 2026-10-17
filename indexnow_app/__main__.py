"""Точка входа для `python -m indexnow_app`."""
import sys
import tkinter as tk

from . import __version__, config
from .logging_setup import get_logger, install_global_handlers
from .ui import IndexNowApp


def _enable_dpi_awareness() -> None:
    """Чёткий текст на HiDPI-экранах Windows.

    Без этого Windows растягивает окно как картинку, и шрифты мылятся.
    Вызывать нужно до создания Tk-окна.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            # PROCESS_SYSTEM_DPI_AWARE = 1 (Windows 8.1+)
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            # Windows 7 или уже задано манифестом.
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception as exc:  # noqa: BLE001
        get_logger().debug("DPI-awareness недоступен: %r", exc)


def main() -> None:
    logger = get_logger()
    install_global_handlers()
    _enable_dpi_awareness()

    root = tk.Tk()
    IndexNowApp(root)
    logger.info(
        "Приложение запущено, версия %s, данные: %s",
        __version__,
        config.app_dir(),
    )
    root.mainloop()


if __name__ == "__main__":
    main()
