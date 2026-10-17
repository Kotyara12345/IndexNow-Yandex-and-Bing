"""Бизнес-логика: подготовка URL, валидация, отправка, статистика.

Не знает о Tkinter. Общается с UI через callback `post(kind, payload)`,
который должен быть потокобезопасным.
"""
from collections.abc import Callable
from typing import NamedTuple

import requests

from . import protocol
from .client import IndexNowClient
from .endpoints import Engine
from .logging_setup import get_logger
from .url_utils import URLUtils

PostFn = Callable[[str, object], None]


class _Plan(NamedTuple):
    """Разобранные и проверенные данные, готовые к отправке."""

    host: str
    groups: dict[str, list[str]]  # scheme -> URL
    key_locations: dict[str, str]  # scheme -> keyLocation (может быть "")


class IndexNowService:
    """Оркестратор отправки. Все методы, кроме __init__, выполняются в фоне."""

    def __init__(self, client: IndexNowClient, post: PostFn) -> None:
        self._client = client
        self._post = post
        self._logger = get_logger()

    # ------------------------------------------------------------ public ----

    def run(
        self,
        host_raw: str,
        key: str,
        key_location_raw: str,
        raw_urls: str,
        engines: list[Engine],
        shutdown_event,
    ) -> None:
        """Гарантирует enable_send в finally, даже при исключении."""
        try:
            self._run_impl(
                host_raw,
                key,
                key_location_raw,
                raw_urls,
                engines,
                shutdown_event,
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Непредвиденная ошибка отправки")
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
        engines: list[Engine],
        shutdown_event,
    ) -> None:
        if not engines:
            self._post("error", "Выберите хотя бы один приёмник.")
            return

        plan = self._build_plan(host_raw, key_location_raw, raw_urls)
        if plan is None:
            return

        self._send_all(plan, key, engines, shutdown_event)

    def _build_plan(
        self, host_raw: str, key_location_raw: str, raw_urls: str
    ) -> _Plan | None:
        """Готовит план отправки. При ошибке пишет в UI и возвращает None."""
        raw_lines = [u.strip() for u in raw_urls.splitlines() if u.strip()]
        if not raw_lines:
            self._post("error", "Введите хотя бы одну ссылку.")
            return None

        unique_urls, duplicates_count = URLUtils.encode_and_dedup(raw_lines)

        # --- Авто-подстановка домена ---
        host_was_auto = False
        if not host_raw or host_raw.lower() == protocol.PLACEHOLDER_HOST:
            detected = URLUtils.detect_host_from_encoded(unique_urls)
            if detected:
                host_raw = detected
                host_was_auto = True
                self._post("set_host", detected)

        if not host_raw:
            self._post("error", "Укажите домен (host).")
            return None

        host, host_scheme = URLUtils.normalize_host(host_raw)
        if not host:
            self._post(
                "error",
                "Не удалось распознать домен. Проверьте формат.",
            )
            return None

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
            return None

        # --- Группировка по схеме ---
        groups: dict[str, list[str]] = {}
        for url in valid_urls:
            parsed = URLUtils.safe_parse(url)
            if parsed is None:
                continue
            groups.setdefault(parsed.scheme, []).append(url)

        if not groups:
            self._post("error", "Не удалось разобрать валидные URL.")
            return None

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
                "\nОбнаружены URL с разными схемами. Отправка будет "
                "выполнена отдельными запросами для http:// и https://. "
                + note,
            )

        key_locations = self._prepare_key_locations(
            groups, host, key_location_raw, kl_user_explicit, multi_scheme
        )
        if key_locations is None:
            return None

        return _Plan(host, groups, key_locations)

    def _prepare_key_locations(
        self,
        groups: dict[str, list[str]],
        host: str,
        key_location_raw: str,
        kl_user_explicit: bool,
        multi_scheme: bool,
    ) -> dict[str, str] | None:
        """Считает и проверяет keyLocation для каждой схемы."""
        key_locations: dict[str, str] = {}

        for scheme in sorted(groups):
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

            error = URLUtils.validate_key_location(
                kl, host, {scheme}, enforce_scheme=enforce_scheme
            )
            if error:
                self._post(
                    "error",
                    f"Ошибка валидации keyLocation ({scheme}://):\n{error}",
                )
                return None

            # Автозаполнение UI-поля только для одиночной схемы
            # и только если пользователь сам не вводил полный URL.
            if not multi_scheme and not kl_user_explicit and kl:
                self._post("set_key_location", kl)
                self._post("log", f"keyLocation дополнен: {kl}")

            self._warn_key_location_scope(kl, groups[scheme])
            key_locations[scheme] = kl

        return key_locations

    def _warn_key_location_scope(
        self, key_location: str, urls: list[str]
    ) -> None:
        """Предупреждает, если keyLocation не покрывает часть URL."""
        if not key_location:
            return
        outside = URLUtils.urls_outside_key_location(key_location, urls)
        if not outside:
            return
        scope = URLUtils.key_location_scope(key_location)
        self._post(
            "log",
            f"Внимание: keyLocation «{key_location}» покрывает только "
            f"URL по пути «{scope}». Вне него {len(outside)} URL - "
            f"движок может отклонить их (HTTP 422).",
        )

    def _send_all(
        self,
        plan: _Plan,
        key: str,
        engines: list[Engine],
        shutdown_event,
    ) -> None:
        per_engine_batches = sum(
            (len(group) + protocol.MAX_URLS_PER_REQUEST - 1)
            // protocol.MAX_URLS_PER_REQUEST
            for group in plan.groups.values()
        )
        total_batches = per_engine_batches * len(engines)
        done_batches = 0
        summary: dict[str, list[int | None]] = {}

        for engine in engines:
            if shutdown_event.is_set():
                return

            self._post(
                "log", f"\n=== Приёмник: {engine.title} ({engine.url}) ==="
            )
            statuses: list[int | None] = []
            summary[engine.key] = statuses

            for scheme in sorted(plan.groups):
                group_urls = plan.groups[scheme]

                if len(plan.groups) > 1:
                    self._post(
                        "log",
                        f"--- {scheme}:// ({len(group_urls)} URL) ---",
                    )

                batches = [
                    group_urls[i : i + protocol.MAX_URLS_PER_REQUEST]
                    for i in range(
                        0, len(group_urls), protocol.MAX_URLS_PER_REQUEST
                    )
                ]
                self._post(
                    "log",
                    f"Отправка {len(group_urls)} URL на {plan.host} "
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

                    status = self._send_one_batch(
                        engine, plan, key, scheme, batch, idx
                    )
                    statuses.append(status)
                    done_batches += 1
                    self._post("progress", (done_batches, total_batches))

        self._log_summary(engines, summary)

    def _send_one_batch(
        self,
        engine: Engine,
        plan: _Plan,
        key: str,
        scheme: str,
        batch: list[str],
        idx: int,
    ) -> int | None:
        """Отправляет один пакет. Возвращает HTTP-код или None при сбое."""
        try:
            response = self._client.send_batch(
                endpoint=engine.url,
                host=plan.host,
                key=key,
                url_list=batch,
                key_location=plan.key_locations.get(scheme) or None,
            )
        except requests.exceptions.Timeout:
            self._post("log", f"Пакет {idx}: таймаут запроса.")
            return None
        except requests.exceptions.ConnectionError:
            self._post("log", f"Пакет {idx}: ошибка соединения.")
            return None
        except requests.exceptions.RequestException as exc:
            self._post("log", f"Пакет {idx}: ошибка HTTP-запроса: {exc}")
            return None

        status = response.status_code
        self._post(
            "log",
            f"HTTP-код ответа: {status}. "
            f"{protocol.status_message(status, response.text)}",
        )
        return status

    def _log_summary(
        self, engines: list[Engine], summary: dict[str, list[int | None]]
    ) -> None:
        lines = ["", "=" * 60, "Итог отправки:"]
        for engine in engines:
            statuses = summary.get(engine.key, [])
            if not statuses:
                lines.append(f"   {engine.title}: запросы не выполнялись")
                continue
            counts: dict[str, int] = {}
            for status in statuses:
                label = str(status) if status is not None else "сбой сети"
                counts[label] = counts.get(label, 0) + 1
            parts = ", ".join(
                f"{label} - {count}" for label, count in counts.items()
            )
            lines.append(
                f"   {engine.title}: запросов {len(statuses)} ({parts})"
            )
        self._post("log", "\n".join(lines))

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
                for url in items[:5]:
                    lines.append(f"      {url}")
        self._post("log", "\n".join(lines))
