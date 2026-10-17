"""Тесты чистых функций URLUtils - без сети и Tkinter."""
import pytest

from indexnow_app.url_utils import URLUtils


class TestEncodeUrl:
    def test_punycode_for_idn(self):
        assert (
            URLUtils.encode_url("https://пример.рф/страница")
            == "https://xn--e1afmkfd.xn--p1ai/%D1%81%D1%82%D1%80%D0%B0%D0%BD%D0%B8%D1%86%D0%B0"
        )

    def test_idempotent(self):
        once = URLUtils.encode_url("https://example.com/пробел тут")
        assert URLUtils.encode_url(once) == once

    def test_lowercases_host_preserves_userinfo_and_port(self):
        assert (
            URLUtils.encode_url("https://User:Pass@EXAMPLE.com:8080/a")
            == "https://User:Pass@example.com:8080/a"
        )

    def test_keeps_query_separators(self):
        assert (
            URLUtils.encode_url("https://example.com/a?b=1&c=2")
            == "https://example.com/a?b=1&c=2"
        )

    def test_non_http_scheme_untouched(self):
        assert (
            URLUtils.encode_url("ftp://Example.com/x")
            == "ftp://Example.com/x"
        )

    def test_empty_and_broken(self):
        assert URLUtils.encode_url("") == ""


class TestHosts:
    @pytest.mark.parametrize(
        "left,right",
        [
            ("example.com", "EXAMPLE.com"),
            ("пример.рф", "xn--e1afmkfd.xn--p1ai"),
            ("[::1]", "::1"),
        ],
    )
    def test_hosts_equal(self, left, right):
        assert URLUtils.hosts_equal(left, right)

    def test_normalize_host_strips_port_and_scheme(self):
        assert URLUtils.normalize_host("https://Example.com:8443/x") == (
            "example.com",
            "https",
        )

    def test_normalize_host_without_scheme(self):
        assert URLUtils.normalize_host("example.com") == ("example.com", None)

    def test_normalize_host_empty(self):
        assert URLUtils.normalize_host("   ") == ("", None)


class TestDedup:
    def test_dedup_counts_duplicates(self):
        urls, duplicates = URLUtils.encode_and_dedup(
            [
                "https://Example.com/a",
                "https://example.com/a",
                "https://example.com/b",
            ]
        )
        assert urls == ["https://example.com/a", "https://example.com/b"]
        assert duplicates == 1

    def test_canonical_key_treats_empty_path_as_root(self):
        assert URLUtils.canonical_key("https://example.com") == (
            "https://example.com/"
        )


class TestSplitUrls:
    HOST = "example.com"

    def test_groups_by_expected_scheme(self):
        valid, invalid, foreign, mismatch, schemes = URLUtils.split_urls(
            [
                "https://example.com/a",
                "http://example.com/b",
                "https://other.com/c",
                "not-a-url",
            ],
            self.HOST,
            "https",
        )
        assert valid == ["https://example.com/a"]
        assert mismatch == ["http://example.com/b"]
        assert foreign == ["https://other.com/c"]
        assert invalid == ["not-a-url"]
        assert schemes == {"https"}

    def test_non_standard_port_is_invalid(self):
        valid, invalid, *_ = URLUtils.split_urls(
            ["https://example.com:8443/a"], self.HOST, None
        )
        assert valid == []
        assert invalid == ["https://example.com:8443/a"]

    def test_default_port_is_kept(self):
        valid, invalid, *_ = URLUtils.split_urls(
            ["https://example.com:443/a"], self.HOST, None
        )
        assert valid == ["https://example.com:443/a"]
        assert invalid == []

    def test_without_expected_scheme_all_schemes_valid(self):
        valid, _, _, _, schemes = URLUtils.split_urls(
            ["https://example.com/a", "http://example.com/b"],
            self.HOST,
            None,
        )
        assert len(valid) == 2
        assert schemes == {"http", "https"}


class TestKeyLocation:
    def test_autocomplete_absolute_path(self):
        assert (
            URLUtils.autocomplete_key_location(
                "/k.txt", "example.com", "https"
            )
            == "https://example.com/k.txt"
        )

    def test_autocomplete_bare_file(self):
        assert (
            URLUtils.autocomplete_key_location(
                "key.txt", "example.com", "http"
            )
            == "http://example.com/key.txt"
        )

    def test_autocomplete_domain_with_path(self):
        assert (
            URLUtils.autocomplete_key_location(
                "sub.example.com/k.txt", "example.com", "https"
            )
            == "https://sub.example.com/k.txt"
        )

    def test_validate_rejects_foreign_host(self):
        error = URLUtils.validate_key_location(
            "https://other.com/k.txt", "example.com", {"https"}
        )
        assert error is not None

    def test_validate_enforces_scheme(self):
        assert (
            URLUtils.validate_key_location(
                "http://example.com/k.txt",
                "example.com",
                {"https"},
                enforce_scheme=True,
            )
            is not None
        )
        assert (
            URLUtils.validate_key_location(
                "http://example.com/k.txt",
                "example.com",
                {"https"},
                enforce_scheme=False,
            )
            is None
        )

    def test_validate_empty_is_ok(self):
        assert (
            URLUtils.validate_key_location("", "example.com", {"https"})
            is None
        )

    @pytest.mark.parametrize(
        "kl,scope",
        [
            ("https://example.com/key.txt", "/"),
            ("https://example.com/catalog/key.txt", "/catalog/"),
            ("https://example.com/", "/"),
        ],
    )
    def test_scope(self, kl, scope):
        assert URLUtils.key_location_scope(kl) == scope

    def test_urls_outside_scope(self):
        outside = URLUtils.urls_outside_key_location(
            "https://example.com/catalog/key.txt",
            [
                "https://example.com/catalog/a",
                "https://example.com/help/b",
            ],
        )
        assert outside == ["https://example.com/help/b"]

    def test_root_key_file_covers_everything(self):
        assert (
            URLUtils.urls_outside_key_location(
                "https://example.com/key.txt",
                ["https://example.com/a", "https://example.com/help/b"],
            )
            == []
        )


class TestDetectHost:
    def test_detects_first_valid(self):
        assert (
            URLUtils.detect_host_from_encoded(
                ["not-a-url", "https://Example.com/a"]
            )
            == "example.com"
        )

    def test_returns_empty_when_nothing_valid(self):
        assert URLUtils.detect_host_from_encoded(["nope"]) == ""
