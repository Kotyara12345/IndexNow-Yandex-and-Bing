"""Бизнес-логика: подготовка URL, валидация, отправка, статистика.

Не знает о Tkinter. Общается с UI через callback `post(kind, payload)`,
который должен быть потокобезопасным.
"""
from typing import Callable

import requests

from .client import IndexNowClient
from .url_utils import URLUtils


MAX_URLS_PER_REQUEST = 10_000

STATUS_MESSAGES = {
    200: "OK. Страницы переданы на переиндексацию.",
    202: "Ключ принят и ожидает проверки. Отправьте ещё несколько адресов.",
    403: "Неверный ключ. Проверьте ключ и его расположение.",
    422: "Ошибка валидации. Проверьте ключ, URL и параметры.",
    429: "Слишком много запросов. Подождите и повторите позже.",
}

DEFAULT_HOST_PLACEHOLDER = "example.com"

PostFn = Callable[[str, object], None]


class IndexNowService:
    """Оркестратор отправки. Все методы, кроме __init__, выполняются в фоне."""

    def __init__(self, client: IndexNowClient, post: PostFn) -> None:
        self._client = client
        self._post = post

    # ------------------------------------------------------------ public ----

    def run(
        self,
        host_raw: str,
        key: str,
        key_location_raw: str,
        raw_urls: str,
        shutdown_event,
    ) -> None:
        """Гарантирует enable_send в finally, даже при исключении."""
        try:
            self._run_impl(
                host_raw, key, key_location_raw, raw_urls, shutdown_event
            )
        except Exception as exc:  # noqa: BLE001
            self._post("error", f"Непредвиденная ошибка: {exc!r}")
        finally:
            self._post("enable_send", True)

    # ----------------------------------------------------------- private ----

    def _run_impl(
        self,
        host_raw: str,
        key: str,
        key_location_raw: str,
        raw_urls: str,
        shutdown_event,
    ) -> None:
        raw_lines = [u.strip() for u in raw_urls.splitlines() if u.strip()]
        if not raw_lines:
            self._post("error", "Введите хотя бы одну ссылку.")
            return

        unique_urls, duplicates_count = URLUtils.encode_and_dedup(raw_lines)

        # --- Авто-подстановка домена ---
        host_was_auto = False
        if not host_raw or host_raw.lower() == DEFAULT_HOST_PLACEHOLDER:
            detected = URLUtils.detect_host_from_encoded(unique_urls)
            if detected:
                host_raw = detected
                host_was_auto = True
                self._post("set_host", detected)

        if not host_raw:
            self._post("error", "Укажите домен (host).")
            return

        host, host_scheme = URLUtils.normalize_host(host_raw)
        if not host:
            self._post(
                "error",
                "Не удалось распознать домен. Проверьте формат.",
            )
            return

        (
            valid_urls,
            invalid_urls,
            host_mismatch_urls,
            scheme_mismatch_urls,
            valid_schemes,
        ) = URLUtils.split_urls(unique_urls, host, host_scheme)

        rejected_total = (
            duplicates_count
            + len(invalid_urls)
            + len(host_mismatch_urls)
            + len(scheme_mismatch_urls)
        )

        self._log_stats(
            host,
            host_was_auto,
            raw_lines,
            valid_urls,
            duplicates_count,
            invalid_urls,
            host_mismatch_urls,
            scheme_mismatch_urls,
            valid_schemes,
            rejected_total,
        )

        if not valid_urls:
            self._post(
                "error",
                f"Нет валидных URL для отправки.\n"
                f"Всего введено: {len(raw_lines)}, "
                f"отклонено: {rejected_total}.",
            )
            return

        # --- Группировка по схеме ---
        groups: dict[str, list[str]] = {}
        for url in valid_urls:
            parsed = URLUtils.safe_parse(url)
            if parsed is None:
                continue
            groups.setdefault(parsed.scheme, []).append(url)

        if not groups:
            self._post("error", "Не удалось разобрать валидные URL.")
            return

        multi_scheme = len(groups) > 1
        kl_user_explicit = "://" in key_location_raw

        if multi_scheme:
            note = (
                "keyLocation будет использован в том виде, "
                "в котором вы его указали."
                if kl_user_explicit
                else "keyLocation для каждого запроса получит "
                "соответствующую схему."
            )
            self._post(
                "log",
                "\nОбнаружены URL с разными схемами. "
                "Отправка будет выполнена двумя запросами - "
                "сначала http://, затем https://. " + note,
            )

        # Общее число пакетов по всем группам - для Progressbar.
        total_batches = sum(
            (len(g) + MAX_URLS_PER_REQUEST - 1) // MAX_URLS_PER_REQUEST
            for g in groups.values()
        )
        done_batches = 0

        for scheme in sorted(groups.keys()):
            if shutdown_event.is_set():
                return

            group_urls = groups[scheme]

            if multi_scheme:
                self._post(
                    "log",
                    f"\n--- Группа {scheme}:// ({len(group_urls)} URL) ---",
                )

            # --- Формирование keyLocation для этой группы ---
            if kl_user_explicit:
                kl = URLUtils.encode_url(key_location_raw)
                enforce_scheme = False
            else:
                kl = URLUtils.autocomplete_key_location(
                    key_location_raw, host, scheme
                )
                kl = URLUtils.replace_scheme(kl, scheme)
                kl = URLUtils.encode_url(kl)
                enforce_scheme = True

            # Автозаполнение UI-поля только для одиночной схемы
            # и только если пользователь сам не вводил полный URL.
            if not multi_scheme and not kl_user_explicit and kl:
                self._post("set_key_location", kl)
                self._post("log", f"keyLocation дополнен: {kl}")

            err = URLUtils.validate_key_location(
                kl, host, {scheme}, enforce_scheme=enforce_scheme
            )
            if err:
                self._post(
                    "error",
                    f"Ошибка валидации keyLocation ({scheme}://):\n{err}",
                )
                return

            batches = [
                group_urls[i : i + MAX_URLS_PER_REQUEST]
                for i in range(0, len(group_urls), MAX_URLS_PER_REQUEST)
            ]

            self._post(
                "log",
                f"Отправка {len(group_urls)} URL на {host} "
                f"({len(batches)} пакет(ов))...",
            )

            for idx, batch in enumerate(batches, 1):
                if shutdown_event.is_set():
                    return

                if len(batches) > 1:
                    self._post(
                        "log",
                        f"Пакет {idx}/{len(batches)}: {len(batch)} URL",
                    )

                self._send_one_batch(host, key, kl, batch, idx)
                done_batches += 1
                self._post("progress", (done_batches, total_batches))

    def _send_one_batch(
        self,
        host: str,
        key: str,
        key_location: str,
        batch: list[str],
        idx: int,
    ) -> None:
        """Отправляет один пакет, обрабатывает сетевые ошибки внутри себя."""
        try:
            response = self._client.send_batch(
                host=host,
                key=key,
                url_list=batch,
                key_location=key_location or None,
            )
        except requests.exceptions.Timeout:
            self._post("log", f"Пакет {idx}: таймаут запроса.")
            return
        except requests.exceptions.ConnectionError:
            self._post("log", f"Пакет {idx}: ошибка соединения.")
            return
        except requests.exceptions.RequestException as exc:
            self._post("log", f"Пакет {idx}: ошибка HTTP-запроса: {exc}")
            return

        status = response.status_code
        message = STATUS_MESSAGES.get(
            status, f"Ответ сервера: {response.text[:500]}"
        )
        self._post("log", f"HTTP-код ответа: {status}. {message}")

    def _log_stats(
        self,
        host: str,
        host_was_auto: bool,
        raw_lines: list[str],
        valid_urls: list[str],
        duplicates_count: int,
        invalid_urls: list[str],
        host_mismatch_urls: list[str],
        scheme_mismatch_urls: list[str],
        valid_schemes: set[str],
        rejected_total: int,
    ) -> None:
        lines = ["=" * 60]
        if host_was_auto:
            lines.append(f"Домен определён автоматически: {host}")
        lines.append("Статистика по введённым данным:")
        lines.append(f"   Всего строк в поле ввода: {len(raw_lines)}")
        lines.append(f"   К отправке:               {len(valid_urls)}")
        lines.append(f"   Отклонено:                {rejected_total}")
        if duplicates_count:
            lines.append(f"      - дубликаты:           {duplicates_count}")
        if invalid_urls:
            lines.append(
                f"      - некорректные URL:    {len(invalid_urls)} "
                f"(вкл. нестандартные порты)"
            )
        if host_mismatch_urls:
            lines.append(
                f"      - чужой домен:         {len(host_mismatch_urls)}"
            )
        if scheme_mismatch_urls:
            lines.append(
                f"      - другая схема:        {len(scheme_mismatch_urls)}"
            )
        if valid_schemes:
            lines.append(
                "   Схемы в выборке:          "
                + ", ".join(f"{s}://" for s in sorted(valid_schemes))
            )
        for label, items in (
            ("Примеры некорректных URL:", invalid_urls),
            ("Примеры URL чужого домена:", host_mismatch_urls),
            ("Примеры URL с другой схемой:", scheme_mismatch_urls),
        ):
            if items:
                lines.append("   " + label)
                for u in items[:5]:
                    lines.append(f"      {u}")
        self._post("log", "\n".join(lines))