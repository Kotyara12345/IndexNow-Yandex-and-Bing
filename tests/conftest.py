"""Общие фикстуры: изоляция каталога данных приложения."""
import pytest

from indexnow_app import config, logging_setup


@pytest.fixture(autouse=True)
def isolated_app_dir(tmp_path, monkeypatch):
    """Не пишем настройки и логи в реальный %LOCALAPPDATA% во время тестов."""
    monkeypatch.setattr(config, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(logging_setup, "_logger", None)
    return tmp_path
