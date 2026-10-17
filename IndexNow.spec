# -*- mode: python ; coding: utf-8 -*-
"""Сборка портативного однофайлового IndexNow.exe (PyInstaller).

Запуск:  python -m PyInstaller --clean --noconfirm IndexNow.spec
Результат: dist/IndexNow.exe - один файл, без установки и зависимостей.
"""
import sys
from pathlib import Path

# Корень проекта - чтобы запускать spec из любой рабочей папки.
PROJECT_DIR = Path(SPECPATH).resolve()

a = Analysis(
    [str(PROJECT_DIR / "run.py")],
    pathex=[str(PROJECT_DIR)],
    binaries=[],
    # app.ico кладём внутрь exe: приложение берёт его для иконки окна
    # (см. config.resource_path в runtime).
    datas=[(str(PROJECT_DIR / "app.ico"), ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Модули тестов в сборку не нужны.
    excludes=["pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="IndexNow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI-приложение: без чёрного окна консоли
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_DIR / "app.ico"),
    version=str(PROJECT_DIR / "version_info.txt"),
)
