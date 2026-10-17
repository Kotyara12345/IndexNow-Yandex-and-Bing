"""HTTP-клиент IndexNow с автоматическими retry на 429 / 5xx."""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from .logging_setup import get_logger

# Таймаут (подключение, чтение ответа) в секундах.
CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 30


class IndexNowClient:
    """HTTP-клиент IndexNow. Один Session на всё приложение."""

    def __init__(self) -> None:
        self._logger = get_logger()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "IndexNow-GUI/1.0"})

        # Автоматический повтор при 429 / 5xx с экспоненциальной задержкой.
        # Уважает заголовок Retry-After, если его присылает движок.
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
        endpoint: str,
        host: str,
        key: str,
        url_list: list[str],
        key_location: str | None = None,
    ) -> requests.Response:
        """Отправляет один батч URL. Исключения requests не глушит."""
        payload: dict = {"host": host, "key": key, "urlList": url_list}
        if key_location:
            payload["keyLocation"] = key_location

        self._logger.debug(
            "POST %s: host=%s, urlList=%d, keyLocation=%s",
            endpoint,
            host,
            len(url_list),
            key_location or "-",
        )
        return self._session.post(
            endpoint,
            json=payload,
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._session.close()
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("Ошибка закрытия HTTP-сессии: %r", exc)
