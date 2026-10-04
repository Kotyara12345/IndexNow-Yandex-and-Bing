"""URL-утилиты: нормализация, кодирование, валидация.

Не зависят от Tkinter и requests - легко тестируются в pytest.
"""
from typing import Optional
from urllib.parse import (
    ParseResult,
    quote,
    unquote,
    urljoin,
    urlparse,
    urlunparse,
)


PATH_SAFE = "/:@!$&'()*+,;=~"
PARAMS_SAFE = "/:@!$&'()*+,;=~"
QUERY_SAFE = "/?:@!$&'()*+,;=~[]"
FRAGMENT_SAFE = "/?:@!$&'()*+,;=~"

# Стандартные порты - нормализуются в «отсутствие порта».
DEFAULT_PORTS = {"http": 80, "https": 443}


class URLUtils:
    """Чистые функции для работы с URL.

    Договорённости:
      * все обращения к urlparse - через safe_parse() (без исключений);
      * все сравнения хостов - через hosts_equal() (без порта,
        без скобок IPv6, в нижнем регистре);
      * нестандартный порт делает URL непригодным для IndexNow.
    """

    # -------------------------------------------------------- базовые утилиты --

    @staticmethod
    def safe_parse(url: str) -> Optional[ParseResult]:
        """urlparse с защитой от ValueError / UnicodeError.

        На некорректных URL (битый IPv6, порт вне диапазона) возвращает None.
        """
        if not isinstance(url, str) or not url:
            return None
        try:
            return urlparse(url)
        except (ValueError, UnicodeError):
            return None

    @staticmethod
    def to_ascii_host(hostname: str) -> str:
        """IDN-домен (.рф, умляуты) -> Punycode, нижний регистр.

        Снимает квадратные скобки IPv6, если они есть.
        """
        if not hostname:
            return ""
        hostname = hostname.strip("[]")
        try:
            ascii_host = hostname.encode("idna").decode("ascii")
        except (UnicodeError, UnicodeDecodeError, ValueError):
            ascii_host = hostname
        return ascii_host.lower()

    @staticmethod
    def hosts_equal(a: str, b: str) -> bool:
        """Сравнение хостов без порта, без скобок IPv6, в нижнем регистре."""
        return URLUtils.to_ascii_host(a) == URLUtils.to_ascii_host(b)

    # ------------------------------------------------------------ кодирование --

    @staticmethod
    def encode_url(url: str) -> str:
        """Нормализует URL, не разрушая структуру netloc.

        * hostname -> Punycode (или IPv6 в квадратных скобках);
        * userinfo и port сохраняются как во входной строке;
        * path / params / query / fragment кодируются через unquote + quote
          (операция идемпотентна: %D1%81 не станет %25D1%2581).

        При любой ошибке разбора возвращает исходную строку.
        """
        parsed = URLUtils.safe_parse(url)
        if parsed is None:
            return url
        if parsed.scheme not in ("http", "https"):
            return url

        hostname = (parsed.hostname or "").strip("[]")
        if not hostname:
            return url

        # IPv6-адрес: parsed.hostname без скобок, при пересборке вернём их.
        if ":" in hostname:
            new_host = f"[{hostname.lower()}]"
        else:
            new_host = URLUtils.to_ascii_host(hostname)

        original_netloc = parsed.netloc

        # userinfo отделяем по последнему '@', чтобы двоеточия пароля
        # не путались с портом.
        if "@" in original_netloc:
            userinfo_part, rest = original_netloc.rsplit("@", 1)
            userinfo_part += "@"
        else:
            userinfo_part, rest = "", original_netloc

        # Порт: для IPv6 - после ']', иначе - после последнего ':'.
        if rest.startswith("["):
            closing = rest.find("]")
            port_part = rest[closing + 1:] if closing >= 0 else ""
        else:
            colon = rest.rfind(":")
            port_part = rest[colon:] if colon >= 0 else ""

        new_netloc = f"{userinfo_part}{new_host}{port_part}"

        path = quote(unquote(parsed.path), safe=PATH_SAFE)
        params = quote(unquote(parsed.params), safe=PARAMS_SAFE)
        query = quote(unquote(parsed.query), safe=QUERY_SAFE)
        fragment = quote(unquote(parsed.fragment), safe=FRAGMENT_SAFE)

        return urlunparse(
            (parsed.scheme, new_netloc, path, params, query, fragment)
        )

    @staticmethod
    def replace_scheme(url: str, new_scheme: str) -> str:
        """Меняет схему URL, оставляя netloc / path / query / fragment."""
        parsed = URLUtils.safe_parse(url)
        if parsed is None or parsed.scheme not in ("http", "https"):
            return url
        return urlunparse(
            (
                new_scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

    @staticmethod
    def canonical_key(url: str) -> str:
        """Ключ дедупликации: схема и netloc в нижнем регистре, пустой путь -> '/'."""
        parsed = URLUtils.safe_parse(url)
        if parsed is None:
            return url.lower()
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path or "/"
        query_str = f"?{parsed.query}" if parsed.query else ""
        fragment_str = f"#{parsed.fragment}" if parsed.fragment else ""
        return f"{scheme}://{netloc}{path}{query_str}{fragment_str}"

    @staticmethod
    def encode_and_dedup(raw_lines: list[str]) -> tuple[list[str], int]:
        """Один проход: encode + canonical + dedup.

        Возвращает (unique_encoded_urls, duplicates_count).
        """
        seen: dict[str, str] = {}
        for raw in raw_lines:
            encoded = URLUtils.encode_url(raw)
            canonical = URLUtils.canonical_key(encoded)
            if canonical not in seen:
                seen[canonical] = encoded

        unique_urls = list(seen.values())
        duplicates_count = len(raw_lines) - len(unique_urls)
        return unique_urls, duplicates_count

    @staticmethod
    def detect_host_from_encoded(encoded_urls: list[str]) -> str:
        """Hostname первой валидной http/https-ссылки. Без порта, Punycode."""
        for url in encoded_urls:
            parsed = URLUtils.safe_parse(url)
            if parsed is None:
                continue
            if parsed.scheme in ("http", "https") and parsed.hostname:
                return URLUtils.to_ascii_host(parsed.hostname)
        return ""

    # ---------------------------------------------------------------- host ----

    @staticmethod
    def normalize_host(host: str) -> tuple[str, Optional[str]]:
        """(hostname_punycode, scheme_or_None).

        Порт и скобки IPv6 отбрасываются.
        """
        host = (host or "").strip()
        if not host:
            return "", None

        if "://" in host:
            parsed = URLUtils.safe_parse(host)
            scheme = parsed.scheme if parsed else None
        else:
            parsed = URLUtils.safe_parse(f"http://{host}")
            scheme = None

        if parsed is None:
            return "", None

        hostname = parsed.hostname or ""
        return URLUtils.to_ascii_host(hostname), scheme

    @staticmethod
    def autocomplete_key_location(
        key_location: str, host: str, scheme: Optional[str]
    ) -> str:
        """Дополняет keyLocation схемой и/или хостом.

        * '/path/file.txt'         -> '{scheme}://{host}/path/file.txt'
        * 'file.txt' / 'key.txt'   -> '{scheme}://{host}/file.txt'
        * 'sub/file.txt'           -> '{scheme}://{host}/sub/file.txt'
        * 'example.com/file.txt'   -> '{scheme}://example.com/file.txt'
        * 'https://...'            -> без изменений
        """
        kl = key_location.strip()
        if not kl:
            return ""
        if "://" in kl:
            return kl

        effective_scheme = scheme or "https"

        is_absolute_path = kl.startswith("/")
        has_slash = "/" in kl
        first_segment = kl.split("/", 1)[0] if has_slash else kl
        looks_like_domain_with_path = has_slash and "." in first_segment

        if is_absolute_path or not looks_like_domain_with_path:
            base = f"{effective_scheme}://{host}/"
            return urljoin(base, kl.lstrip("/"))

        return f"{effective_scheme}://{kl}"

    # ------------------------------------------------------------ validation --

    @staticmethod
    def validate_key_location(
        key_location: str,
        host: str,
        valid_schemes: set[str],
        enforce_scheme: bool = True,
    ) -> Optional[str]:
        """Проверяет keyLocation. Возвращает текст ошибки или None.

        enforce_scheme=True  - проверяем совпадение схемы (автодополненный URL);
        enforce_scheme=False - схему не проверяем (пользователь ввёл явный URL,
                               сам отвечает за совместимость с IndexNow).
        """
        if not key_location:
            return None

        parsed = URLUtils.safe_parse(key_location)
        if (
            parsed is None
            or parsed.scheme not in ("http", "https")
            or not parsed.hostname
        ):
            return (
                "keyLocation должен быть полным URL, "
                "начинающимся с http:// или https://."
            )

        if not URLUtils.hosts_equal(parsed.hostname, host):
            return (
                f"keyLocation должен находиться точно на том же домене "
                f"'{host}'.\nСейчас указан хост: {parsed.hostname}"
            )

        if enforce_scheme and len(valid_schemes) == 1:
            single_scheme = next(iter(valid_schemes))
            if parsed.scheme != single_scheme:
                return (
                    f"Схема keyLocation ('{parsed.scheme}://') не совпадает "
                    f"со схемой отправляемых URL ('{single_scheme}://').\n"
                    "IndexNow требует единый протокол для keyLocation "
                    "и передаваемых адресов."
                )
        return None

    @staticmethod
    def split_urls(
        urls: list[str],
        host: str,
        expected_scheme: Optional[str],
    ) -> tuple[list[str], list[str], list[str], list[str], set[str]]:
        """Разделяет URL на группы.

        Возвращает (valid, invalid, host_mismatch, scheme_mismatch, schemes).

        URL с нестандартным портом попадает в invalid: IndexNow принимает
        host без порта и не сможет проверить такой URL.
        """
        invalid: list[str] = []
        host_mismatch: list[str] = []
        candidates: list[tuple[str, str]] = []

        for url in urls:
            parsed = URLUtils.safe_parse(url)
            if (
                parsed is None
                or parsed.scheme not in ("http", "https")
                or not parsed.hostname
            ):
                invalid.append(url)
                continue

            try:
                port = parsed.port
            except ValueError:
                invalid.append(url)
                continue

            if port is not None and DEFAULT_PORTS.get(parsed.scheme) != port:
                invalid.append(url)
                continue

            if not URLUtils.hosts_equal(parsed.hostname, host):
                host_mismatch.append(url)
                continue

            candidates.append((url, parsed.scheme))

        valid: list[str] = []
        scheme_mismatch: list[str] = []
        schemes: set[str] = set()

        if expected_scheme in ("http", "https"):
            for url, scheme in candidates:
                if scheme == expected_scheme:
                    valid.append(url)
                    schemes.add(scheme)
                else:
                    scheme_mismatch.append(url)
        else:
            for url, scheme in candidates:
                valid.append(url)
                schemes.add(scheme)

        return valid, invalid, host_mismatch, scheme_mismatch, schemes