"""Пути приложения, ресурсы и хранение настроек.

Настройки и лог лежат в одном каталоге данных
(Windows: %LOCALAPPDATA%\\IndexNow).

Ключ IndexNow не является секретом - он публикуется в открытом файле
на сайте, поэтому хранится в настройках без шифрования.
"""
import json
import os
import sys
from pathlib import Path
from typing import Any

APP_NAME = "IndexNow"
SETTINGS_FILE = "settings.json"
LOG_FILE = "indexnow.log"


def app_dir() -> Path:
    """Каталог данных приложения (создаётся при необходимости)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        path = Path(base) / APP_NAME if base else Path.home() / APP_NAME
    else:
        path = Path.home() / f".{APP_NAME.lower()}"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return Path.home()
    return path


def log_path() -> Path:
    return app_dir() / LOG_FILE


def settings_path() -> Path:
    return app_dir() / SETTINGS_FILE


def load_settings() -> dict[str, Any]:
    """Читает настройки; при любой ошибке возвращает пустой словарь."""
    try:
        with settings_path().open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(data: dict[str, Any]) -> None:
    """Атомарная запись настроек; ошибки не пробрасываются в UI."""
    target = settings_path()
    tmp = target.with_name(target.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def resource_path(name: str) -> Path:
    """Путь к ресурсу и в исходниках, и внутри собранного PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / name
    return Path(__file__).resolve().parent.parent / name
