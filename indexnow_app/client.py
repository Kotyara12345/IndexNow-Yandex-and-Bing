"""HTTP-клиент IndexNow с автоматическими retry на 429 / 5xx."""
import sys

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


INDEXNOW_ENDPOINT = "https://yandex.com/indexnow"
REQUEST_TIMEOUT_SECONDS = 30


class IndexNowClient:
    """HTTP-клиент IndexNow. Один Session на всё приложение."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": "IndexNow-Yandex-GUI/1.0"}
        )

        # Автоматический повтор при 429 / 5xx с экспоненциальной задержкой.
        # Уважает заголовок Retry-After, если его присылает Яндекс.
        retries = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=2,  # 2s, 4s, 8s
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(["POST"]),
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retries)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

        self._closed = False

    def send_batch(
        self,
        host: str,
        key: str,
        url_list: list[str],
        key_location: str | None = None,
    ) -> requests.Response:
        """Отправляет один батч URL. Исключения requests не глушит."""
        payload: dict = {"host": host, "key": key, "urlList": url_list}
        if key_location:
            payload["keyLocation"] = key_location

        return self._session.post(
            INDEXNOW_ENDPOINT,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._session.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[http-session] close error: {exc!r}", file=sys.stderr)