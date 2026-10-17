"""Приёмники IndexNow.

По протоколу движки делят присланные URL между собой, поэтому достаточно
одного приёмника; выбор нескольких даёт устойчивость, если один недоступен.
"""
from collections.abc import Iterable
from typing import NamedTuple


class Engine(NamedTuple):
    key: str
    title: str
    url: str


ENGINES: tuple[Engine, ...] = (
    Engine("yandex", "Яндекс", "https://yandex.com/indexnow"),
    Engine("bing", "Bing", "https://www.bing.com/indexnow"),
    Engine("indexnow", "IndexNow (общий)", "https://api.indexnow.org/indexnow"),
)

DEFAULT_ENGINE_KEYS: tuple[str, ...] = ("yandex",)

_BY_KEY = {engine.key: engine for engine in ENGINES}


def get_engines(keys: Iterable[str]) -> list[Engine]:
    """Выбранные движки в порядке объявления.

    Неизвестные ключи отбрасываются.
    """
    selected = set(keys)
    return [engine for engine in ENGINES if engine.key in selected]
