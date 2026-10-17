"""Тесты констант и правил протокола IndexNow."""
import pytest

from indexnow_app import protocol


class TestKeyPattern:
    @pytest.mark.parametrize(
        "key",
        [
            "abcdefgh",
            "ABCDEFGH",
            "12345678",
            "a-b-c-d-e",
            "A" * 128,
        ],
    )
    def test_valid(self, key):
        assert protocol.KEY_PATTERN.match(key)

    @pytest.mark.parametrize(
        "key",
        [
            "short",  # меньше 8 символов
            "a" * 129,  # больше 128 символов
            "key_with_underscore",  # спецификация запрещает '_'
            "ключ-на-кириллице-123",
            "key with space",
            "key.with.dots",
        ],
    )
    def test_invalid(self, key):
        assert protocol.KEY_PATTERN.match(key) is None

    def test_underscore_rejected_explicitly(self):
        # Регрессия: раньше '_' ошибочно допускалось.
        assert protocol.KEY_PATTERN.match("abcd_efgh") is None


class TestStatusMessage:
    @pytest.mark.parametrize(
        "status", [200, 202, 400, 403, 404, 405, 422, 429]
    )
    def test_known_statuses_have_text(self, status):
        assert protocol.status_message(status)

    def test_unknown_status_uses_response_text(self):
        message = protocol.status_message(599, "  много   пробелов  ")
        assert "много пробелов" in message

    def test_unknown_status_without_text(self):
        assert protocol.status_message(599, "")

    def test_long_response_is_truncated(self):
        message = protocol.status_message(599, "x" * 1000)
        assert len(message) < 400


class TestIsSuccess:
    @pytest.mark.parametrize("status", [200, 201, 202, 299])
    def test_success(self, status):
        assert protocol.is_success(status)

    @pytest.mark.parametrize("status", [199, 300, 400, 500])
    def test_not_success(self, status):
        assert not protocol.is_success(status)
