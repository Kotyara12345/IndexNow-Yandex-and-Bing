"""Тесты сохранения и чтения настроек."""
import json

from indexnow_app import config


def test_settings_roundtrip(isolated_app_dir):
    data = {
        "host": "example.com",
        "key": "abcdefgh",
        "engines": ["yandex", "bing"],
        "geometry": "800x900+10+20",
    }
    config.save_settings(data)
    assert config.load_settings() == data


def test_missing_file_gives_empty_dict(isolated_app_dir):
    assert config.load_settings() == {}


def test_broken_json_gives_empty_dict(isolated_app_dir):
    config.settings_path().write_text("{ not json", encoding="utf-8")
    assert config.load_settings() == {}


def test_non_dict_json_gives_empty_dict(isolated_app_dir):
    config.settings_path().write_text("[1, 2, 3]", encoding="utf-8")
    assert config.load_settings() == {}


def test_save_is_atomic_and_valid(isolated_app_dir):
    config.save_settings({"key": "abcdefgh", "host": "пример.рф"})
    raw = config.settings_path().read_text(encoding="utf-8")
    assert json.loads(raw)["host"] == "пример.рф"
    # Временный файл не должен оставаться.
    assert not list(isolated_app_dir.glob("*.tmp"))


def test_corrupted_write_does_not_raise(isolated_app_dir, monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("nope")

    monkeypatch.setattr(config.os, "replace", boom)
    config.save_settings({"key": "abcdefgh"})  # не должно бросить


def test_resource_path_returns_existing_file():
    assert config.resource_path("app.ico").is_file()
