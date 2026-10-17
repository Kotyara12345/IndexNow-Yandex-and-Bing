@echo off
REM ---------------------------------------------------------------------------
REM Сборка портативного IndexNow.exe (один файл).
REM Требуется: Python 3.10+ и пакеты из requirements.txt.
REM ---------------------------------------------------------------------------
setlocal

cd /d "%~dp0"

python -m pip install --upgrade -q -r requirements.txt || goto :error

python -m PyInstaller --clean --noconfirm IndexNow.spec || goto :error

echo.
echo Готово: "%~dp0dist\IndexNow.exe"
echo Этот файл можно скопировать на любую машину с Windows x64 - установка не нужна.
pause
exit /b 0

:error
echo.
echo Сборка завершилась с ошибкой.
pause
exit /b 1
