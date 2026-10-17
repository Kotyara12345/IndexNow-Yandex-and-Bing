"""Генератор иконки приложения app.ico (без внешних зависимостей).

Рисует синий скруглённый квадрат со стрелкой вверх - символ отправки
страниц на переиндексацию. Рендер с 4-кратным сглаживанием, упаковка
в ICO с PNG-содержимым (поддерживается Vista+).

Запуск:  python tools/make_icon.py
"""
import struct
import zlib
from pathlib import Path

BACKGROUND = (37, 99, 235)  # синий
FOREGROUND = (255, 255, 255)  # белая стрелка
SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPERSAMPLE = 4

OUTPUT = Path(__file__).resolve().parent.parent / "app.ico"


# ------------------------------------------------------------------ фигуры --
def _in_rounded_rect(u: float, v: float, radius: float) -> bool:
    """Точка внутри квадрата [0,1]x[0,1] со скруглёнными углами."""
    cx = min(max(u, radius), 1.0 - radius)
    cy = min(max(v, radius), 1.0 - radius)
    dx, dy = u - cx, v - cy
    return dx * dx + dy * dy <= radius * radius


def _sign(p1, p2, p3) -> float:
    return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (
        p1[1] - p3[1]
    )


def _in_triangle(point, v1, v2, v3) -> bool:
    d1 = _sign(point, v1, v2)
    d2 = _sign(point, v2, v3)
    d3 = _sign(point, v3, v1)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def _in_arrow(u: float, v: float) -> bool:
    """Стрелка вверх: древко + треугольное остриё."""
    if 0.435 <= u <= 0.565 and 0.36 <= v <= 0.79:
        return True
    return _in_triangle(
        (u, v), (0.5, 0.20), (0.235, 0.47), (0.765, 0.47)
    )


# ------------------------------------------------------------------ рендер --
def _render(size: int) -> list[bytearray]:
    """RGBA-строки указанного размера (сглаженные)."""
    n = size * SUPERSAMPLE
    radius = 0.22

    # Рисуем в увеличенном разрешении.
    big: list[bytearray] = []
    for y in range(n):
        row = bytearray()
        for x in range(n):
            u = (x + 0.5) / n
            v = (y + 0.5) / n
            if not _in_rounded_rect(u, v, radius):
                row += b"\x00\x00\x00\x00"
            elif _in_arrow(u, v):
                row += bytes((*FOREGROUND, 255))
            else:
                row += bytes((*BACKGROUND, 255))
        big.append(row)

    # Уменьшаем усреднением (цвет взвешиваем по альфе - без тёмной каймы).
    k = SUPERSAMPLE * SUPERSAMPLE
    out: list[bytearray] = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            sr = sg = sb = sa = 0
            for dy in range(SUPERSAMPLE):
                line = big[y * SUPERSAMPLE + dy]
                for dx in range(SUPERSAMPLE):
                    i = (x * SUPERSAMPLE + dx) * 4
                    alpha = line[i + 3]
                    sr += line[i] * alpha
                    sg += line[i + 1] * alpha
                    sb += line[i + 2] * alpha
                    sa += alpha
            if sa == 0:
                row += b"\x00\x00\x00\x00"
            else:
                row += bytes((sr // sa, sg // sa, sb // sa, sa // k))
        out.append(row)
    return out


def _png(width: int, height: int, rows: list[bytearray]) -> bytes:
    """Кодирует RGBA-строки в PNG."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def build_ico(path: Path) -> None:
    images = [(size, _png(size, size, _render(size))) for size in SIZES]

    directory = struct.pack("<HHH", 0, 1, len(images))
    entries = b""
    payload = b""
    offset = 6 + 16 * len(images)
    for size, data in images:
        dimension = size if size < 256 else 0
        entries += struct.pack(
            "<BBBBHHII",
            dimension,
            dimension,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        offset += len(data)
        payload += data

    path.write_bytes(directory + entries + payload)


if __name__ == "__main__":
    build_ico(OUTPUT)
    print(f"Готово: {OUTPUT} ({OUTPUT.stat().st_size} байт)")
