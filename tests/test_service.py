"""Тесты оркестратора отправки: движки, схемы, keyLocation, сводка."""
import threading

import requests

from indexnow_app.endpoints import get_engines
from indexnow_app.service import IndexNowService


class FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class FakeClient:
    """Заглушка IndexNowClient: пишет вызовы и отдаёт заданный код."""

    def __init__(self, status_code: int = 200, error: str | None = None):
        self.status_code = status_code
        self.error = error
        self.calls: list[dict] = []

    def send_batch(self, endpoint, host, key, url_list, key_location=None):
        if self.error == "connection":
            raise requests.exceptions.ConnectionError("boom")
        if self.error == "timeout":
            raise requests.exceptions.Timeout("slow")
        self.calls.append(
            {
                "endpoint": endpoint,
                "host": host,
                "key": key,
                "url_list": list(url_list),
                "key_location": key_location,
            }
        )
        return FakeResponse(self.status_code)


def run_service(client, *, host="", key="abcdefgh", key_location="",
                urls="https://example.com/a", engines=("yandex",)):
    messages: list[tuple[str, object]] = []

    def post(kind: str, payload: object) -> None:
        messages.append((kind, payload))

    service = IndexNowService(client=client, post=post)
    service.run(
        host,
        key,
        key_location,
        urls,
        get_engines(engines),
        threading.Event(),
    )
    return messages, service


def logs(messages) -> str:
    return "\n".join(
        str(payload) for kind, payload in messages if kind == "log"
    )


def test_sends_to_every_selected_engine():
    client = FakeClient()
    messages, _ = run_service(
        client, engines=("yandex", "bing", "indexnow")
    )
    assert len(client.calls) == 3
    endpoints = {call["endpoint"] for call in client.calls}
    assert "https://yandex.com/indexnow" in endpoints
    assert "https://www.bing.com/indexnow" in endpoints
    assert "https://api.indexnow.org/indexnow" in endpoints


def test_host_detected_automatically():
    client = FakeClient()
    messages, _ = run_service(client, urls="https://example.com/a")
    assert client.calls[0]["host"] == "example.com"
    assert any(kind == "set_host" for kind, _ in messages)


def test_progress_and_summary():
    client = FakeClient(status_code=202)
    messages, _ = run_service(client, engines=("yandex", "bing"))
    progress = [payload for kind, payload in messages if kind == "progress"]
    assert progress[-1] == (2, 2)
    text = logs(messages)
    assert "Итог отправки:" in text
    assert "Яндекс" in text and "Bing" in text
    assert "202" in text


def test_enable_send_always_posted():
    client = FakeClient()
    messages, _ = run_service(client)
    assert messages[-1] == ("enable_send", True)


def test_enable_send_posted_on_error():
    messages, _ = run_service(FakeClient(), key="", urls="")
    assert messages[-1] == ("enable_send", True)


def test_schemes_split_into_separate_requests():
    client = FakeClient()
    run_service(
        client,
        urls="https://example.com/a\nhttp://example.com/b",
    )
    assert len(client.calls) == 2
    assert {call["url_list"][0] for call in client.calls} == {
        "https://example.com/a",
        "http://example.com/b",
    }


def test_key_location_is_passed():
    client = FakeClient()
    run_service(client, key_location="https://example.com/key.txt")
    assert client.calls[0]["key_location"] == "https://example.com/key.txt"


def test_key_location_scope_warning():
    client = FakeClient()
    messages, _ = run_service(
        client,
        key_location="https://example.com/catalog/key.txt",
        urls="https://example.com/catalog/a\nhttps://example.com/help/b",
    )
    text = logs(messages)
    assert "покрывает только" in text
    assert "/catalog/" in text
    # Оба URL всё равно отправляются - это предупреждение, не ошибка.
    assert len(client.calls) == 1
    assert len(client.calls[0]["url_list"]) == 2


def test_no_warning_for_root_key_file():
    client = FakeClient()
    messages, _ = run_service(
        client, key_location="https://example.com/key.txt"
    )
    assert "покрывает только" not in logs(messages)


def test_missing_host_is_an_error():
    client = FakeClient()
    messages, _ = run_service(client, urls="not-a-url")
    assert client.calls == []
    assert any(kind == "error" for kind, _ in messages)


def test_invalid_key_location_reports_error():
    client = FakeClient()
    messages, _ = run_service(
        client, key_location="https://other.com/key.txt"
    )
    assert client.calls == []
    assert any(
        kind == "error" and "keyLocation" in str(payload)
        for kind, payload in messages
    )


def test_network_error_is_logged_and_summarised():
    client = FakeClient(error="connection")
    messages, _ = run_service(client)
    text = logs(messages)
    assert "ошибка соединения" in text
    assert "сбой сети" in text


def test_timeout_is_logged():
    client = FakeClient(error="timeout")
    messages, _ = run_service(client)
    assert "таймаут" in logs(messages).lower()


def test_duplicates_are_removed_before_sending():
    client = FakeClient()
    run_service(
        client,
        urls="https://example.com/a\nhttps://example.com/a",
    )
    assert client.calls[0]["url_list"] == ["https://example.com/a"]
    assert "дубликаты" in logs([("log", "дубликаты")])


def test_batching_respects_limit(monkeypatch):
    monkeypatch.setattr("indexnow_app.protocol.MAX_URLS_PER_REQUEST", 2)
    client = FakeClient()
    run_service(
        client,
        urls="\n".join(f"https://example.com/{i}" for i in range(5)),
    )
    assert len(client.calls) == 3  # 2 + 2 + 1
