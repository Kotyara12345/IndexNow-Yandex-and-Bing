"""Тесты списка приёмников IndexNow."""
from indexnow_app.endpoints import (
    DEFAULT_ENGINE_KEYS,
    ENGINES,
    get_engines,
)


def test_all_endpoints_are_https_indexnow():
    for engine in ENGINES:
        assert engine.url.startswith("https://")
        assert engine.url.endswith("/indexnow")


def test_engine_keys_are_unique():
    keys = [engine.key for engine in ENGINES]
    assert len(keys) == len(set(keys))


def test_default_engines_exist():
    assert get_engines(DEFAULT_ENGINE_KEYS)
    known = {engine.key for engine in ENGINES}
    assert set(DEFAULT_ENGINE_KEYS) <= known


def test_selection_preserves_declaration_order():
    selected = get_engines(["indexnow", "yandex"])
    assert [engine.key for engine in selected] == ["yandex", "indexnow"]


def test_unknown_keys_ignored():
    assert get_engines(["nope"]) == []


def test_empty_selection():
    assert get_engines([]) == []
