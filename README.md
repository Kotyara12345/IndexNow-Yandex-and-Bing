# IndexNow GUI

Небольшое десктопное приложение (Tkinter) для отправки страниц на
переиндексацию по протоколу [IndexNow](https://www.indexnow.org/documentation).
Поддерживает несколько приёмников: Яндекс, Bing и общий `api.indexnow.org`
(по протоколу движки делят присланные URL между собой).

## Возможности

- Отправка списка URL (по одному на строку), автоматическое определение домена.
- Разделение `http://` и `https://` на отдельные запросы как требует протокол.
- Проверка и автодополнение `keyLocation`, предупреждение о его области действия.
- Пакеты до 10 000 URL, прогресс, итоговая сводка по кодам ответов.
- Горячие клавиши Ctrl+C/V/X/A работают в любой раскладке клавиатуры.
- Меню по правой кнопке, кнопка «Вставить из буфера», импорт ссылок из файла.
- Сохранение домена, ключа, `keyLocation`, приёмников и размера окна.
- Журнал работы в файле (важно для сборки без консоли).

## Запуск из исходников

```powershell
python -m pip install -r requirements.txt
python run.py
```

Нужен Python 3.10+ с Tkinter (входит в установщик для Windows).

## Сборка портативного .exe

```powershell
python -m PyInstaller --clean --noconfirm IndexNow.spec
```

или просто запустите `build.bat`. Результат — `dist\IndexNow.exe`: один файл,
установка и Python на целевой машине не нужны.

## Проверки

```powershell
python -m pip install -r requirements-dev.txt
python -m ruff check .      # линтер
python -m mypy              # проверка типов
python -m pytest            # тесты
```

Иконка приложения генерируется скриптом `python tools/make_icon.py`
(в репозиторий кладётся уже готовый `app.ico`).

## Где хранятся данные

`%LOCALAPPDATA%\IndexNow\`:

- `settings.json` — сохранённые настройки;
- `indexnow.log` — журнал (ротация: 256 КБ, 2 файла).

Ключ IndexNow не является секретом (он публикуется в открытом файле на сайте),
поэтому хранится без шифрования.

## Структура

| Файл | Назначение |
|---|---|
| `run.py` | точка входа |
| `indexnow_app/ui.py` | интерфейс Tkinter |
| `indexnow_app/service.py` | оркестрация отправки, статистика |
| `indexnow_app/client.py` | HTTP-клиент с retry |
| `indexnow_app/url_utils.py` | нормализация и валидация URL |
| `indexnow_app/protocol.py` | константы протокола IndexNow |
| `indexnow_app/endpoints.py` | список приёмников |
| `indexnow_app/config.py` | пути, настройки, ресурсы |
| `indexnow_app/logging_setup.py` | логирование в файл |
| `tests/` | тесты pytest |
