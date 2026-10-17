"""Tkinter-интерфейс. Тонкий слой: собирает данные и показывает сообщения."""
import queue
import re
import threading
import tkinter as tk
import tkinter.font as tkfont
import weakref
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import cast

from . import config, logging_setup, protocol
from .client import IndexNowClient
from .endpoints import DEFAULT_ENGINE_KEYS, ENGINES, get_engines
from .service import IndexNowService

QUEUE_POLL_INTERVAL_MS = 100
SHUTDOWN_JOIN_TIMEOUT_SECONDS = 0.3
ENABLE_SEND_RETRY_DELAY_MS = 50

DEFAULT_GEOMETRY = "820x900"
MIN_WINDOW_SIZE = (760, 620)
GEOMETRY_PATTERN = re.compile(r"^\d+x\d+([+-]\d+[+-]\d+)?$")

# Виртуальные коды клавиш Windows (event.keycode). В отличие от keysym
# они не зависят от раскладки: при русской раскладке Ctrl+V даёт keysym
# 'Cyrillic_em', и штатные привязки Tk (<<Paste>> и т.п.) не срабатывают.
VK_A = 65
VK_C = 67
VK_V = 86
VK_X = 88


# --------------------------------------------------------------------------
# Placeholder-помощники для Entry
# --------------------------------------------------------------------------
class _Placeholder:
    """Состояние текста-заглушки для одного поля ввода."""

    __slots__ = ("text", "active")

    def __init__(self, text: str) -> None:
        self.text = text
        self.active = False


# Состояние заглушек по виджетам (у ttk.Entry своих полей нет).
_placeholders: "weakref.WeakKeyDictionary[ttk.Entry, _Placeholder]" = (
    weakref.WeakKeyDictionary()
)


def _add_placeholder(entry: ttk.Entry, text: str) -> None:
    """Показывает текст-заглушку, пока поле пустое."""
    state = _Placeholder(text)
    _placeholders[entry] = state

    def show() -> None:
        if not entry.get():
            state.active = True
            entry.insert(0, state.text)
            entry.configure(foreground="gray")

    def on_focus_in(_event) -> None:
        if state.active:
            entry.delete(0, tk.END)
            entry.configure(foreground="black")
            state.active = False

    def on_focus_out(_event) -> None:
        if not entry.get():
            show()

    entry.bind("<FocusIn>", on_focus_in)
    entry.bind("<FocusOut>", on_focus_out)
    show()


def _entry_value(entry: ttk.Entry) -> str:
    state = _placeholders.get(entry)
    return "" if state is not None and state.active else entry.get()


def _entry_set(entry: ttk.Entry, value: str) -> None:
    state = _placeholders.get(entry)
    if state is not None:
        state.active = False
    entry.delete(0, tk.END)
    entry.configure(foreground="black")
    entry.insert(0, value)


# --------------------------------------------------------------------------
# Горячие клавиши (не зависят от раскладки клавиатуры)
# --------------------------------------------------------------------------
def _select_all_widget(widget: tk.Misc) -> None:
    """Выделяет весь текст в Entry или Text."""
    if hasattr(widget, "tag_add"):  # Text / ScrolledText
        text_widget = cast("tk.Text", widget)
        text_widget.tag_add(tk.SEL, "1.0", tk.END)
        text_widget.mark_set(tk.INSERT, "1.0")
        text_widget.see(tk.INSERT)
    else:  # Entry
        entry_widget = cast("tk.Entry", widget)
        entry_widget.select_range(0, tk.END)
        entry_widget.icursor(tk.END)


def _select_all(event: tk.Event) -> str:
    _select_all_widget(event.widget)
    return "break"


def _on_control_key(event: tk.Event):
    """Ctrl+C / Ctrl+V / Ctrl+X / Ctrl+A по event.keycode.

    event.keycode - аппаратный код клавиши, одинаковый в любой раскладке,
    в отличие от keysym (в русской раскладке Ctrl+V даёт 'Cyrillic_em').
    """
    keycode = event.keycode
    if keycode == VK_A:
        return _select_all(event)
    if keycode == VK_V:
        event.widget.event_generate("<<Paste>>")
        return "break"
    if keycode == VK_C:
        event.widget.event_generate("<<Copy>>")
        return "break"
    if keycode == VK_X:
        event.widget.event_generate("<<Cut>>")
        return "break"
    return None


def _read_text_file(path: str) -> str | None:
    """Читает файл как UTF-8 (с BOM) или, при неудаче, как CP1251."""
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            with open(path, encoding=encoding) as file:
                return file.read()
        except UnicodeDecodeError:
            continue
        except OSError:
            return None
    return None


# --------------------------------------------------------------------------
# Главное окно
# --------------------------------------------------------------------------
class IndexNowApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("IndexNow - отправка страниц в Яндекс и Bing")
        self.root.geometry(DEFAULT_GEOMETRY)
        self.root.minsize(*MIN_WINDOW_SIZE)
        self.root.resizable(True, True)

        self._logger = logging_setup.get_logger()
        self._bold_font = tkfont.nametofont("TkDefaultFont").copy()
        self._bold_font.configure(weight="bold")

        self.msg_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._shutdown = threading.Event()
        self._active_thread: threading.Thread | None = None
        self._menu_target: tk.Misc | None = None

        self._client = IndexNowClient()
        self._service = IndexNowService(
            client=self._client, post=self._post_to_ui
        )

        self._build_ui()
        self._context_menu = self._create_context_menu()
        self._apply_settings()
        self._set_window_icon()

        self.root.report_callback_exception = self._report_callback_exception
        self.root.bind("<Control-Return>", lambda _event: self.send_urls())

        self.root.after(QUEUE_POLL_INTERVAL_MS, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------------------------------------------- UI --

    def _build_ui(self) -> None:
        self.host_entry = self._add_labeled_entry(
            "Домен (host):", protocol.PLACEHOLDER_HOST
        )
        self.key_entry = self._add_labeled_entry(
            "Ключ IndexNow:", "ключ от 8 до 128 символов [A-Za-z0-9-]"
        )
        self.key_loc_entry = self._add_labeled_entry(
            "Расположение файла с ключом (keyLocation, необязательно):",
            "https://example.com/key.txt",
        )

        ttk.Label(
            self.root, text="Куда отправлять:", font=self._bold_font
        ).pack(anchor="w", padx=10, pady=(10, 2))
        engines_frame = ttk.Frame(self.root)
        engines_frame.pack(fill="x", padx=10)
        self.engine_vars: dict[str, tk.BooleanVar] = {}
        for engine in ENGINES:
            var = tk.BooleanVar(value=engine.key in DEFAULT_ENGINE_KEYS)
            self.engine_vars[engine.key] = var
            ttk.Checkbutton(
                engines_frame, text=engine.title, variable=var
            ).pack(side="left", padx=(0, 14))

        ttk.Label(
            self.root,
            text="Ссылки для переиндексации (по одной на строку):",
            font=self._bold_font,
        ).pack(anchor="w", padx=10, pady=(10, 2))

        urls_bar = ttk.Frame(self.root)
        urls_bar.pack(fill="x", padx=10, pady=(0, 4))
        ttk.Button(
            urls_bar,
            text="Вставить из буфера",
            command=self.paste_from_clipboard,
        ).pack(side="left")
        ttk.Button(
            urls_bar, text="Загрузить из файла", command=self.import_from_file
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            urls_bar, text="Очистить", command=self.clear_urls
        ).pack(side="left", padx=(6, 0))

        self.urls_text = scrolledtext.ScrolledText(
            self.root, height=12, wrap=tk.WORD
        )
        self.urls_text.pack(fill="both", expand=True, padx=10)
        self._bind_shortcuts(self.urls_text)
        self._bind_context_menu(self.urls_text)

        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", padx=10, pady=10)

        self.send_btn = ttk.Button(
            btn_frame, text="Отправить", command=self.send_urls
        )
        self.send_btn.pack(side="left", padx=(0, 10))

        ttk.Button(
            btn_frame, text="Выход", command=self._on_close
        ).pack(side="right")

        self.progress = ttk.Progressbar(
            self.root, mode="determinate", maximum=100
        )
        self.progress.pack(fill="x", padx=10, pady=(0, 6))

        self.progress_label = ttk.Label(self.root, text="")
        self.progress_label.pack(anchor="w", padx=10)

        ttk.Label(
            self.root, text="Результат:", font=self._bold_font
        ).pack(anchor="w", padx=10, pady=(6, 2))
        self.log_text = scrolledtext.ScrolledText(
            self.root, height=12, wrap=tk.WORD, state="disabled"
        )
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._bind_shortcuts(self.log_text)
        self._bind_context_menu(self.log_text)

    def _add_labeled_entry(
        self, title: str, placeholder: str, width: int = 80
    ) -> ttk.Entry:
        ttk.Label(self.root, text=title, font=self._bold_font).pack(
            anchor="w", padx=10, pady=(10, 2)
        )
        entry = ttk.Entry(self.root, width=width)
        entry.pack(fill="x", padx=10)
        _add_placeholder(entry, placeholder)
        self._bind_shortcuts(entry)
        self._bind_context_menu(entry)
        return entry

    @staticmethod
    def _bind_shortcuts(widget: tk.Widget) -> None:
        """Ctrl+A / Ctrl+C / Ctrl+V / Ctrl+X для любой раскладки.

        Штатные привязки Tk сравнивают keysym с латинской буквой и при
        не-английской раскладке молча не срабатывают. Дублируем их
        обработчиком по event.keycode (см. _on_control_key).
        """
        # Классические привязки: английская раскладка и macOS.
        widget.bind("<Control-a>", _select_all)
        widget.bind("<Command-a>", _select_all)
        # Раскладко-независимый дубль (Windows).
        widget.bind("<Control-KeyPress>", _on_control_key)

    # --------------------------------------------------- контекстное меню --

    def _bind_context_menu(self, widget: tk.Widget) -> None:
        widget.bind("<Button-3>", self._popup_menu)

    def _create_context_menu(self) -> tk.Menu:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(
            label="Вырезать", command=lambda: self._menu_edit("<<Cut>>")
        )
        menu.add_command(
            label="Копировать", command=lambda: self._menu_edit("<<Copy>>")
        )
        menu.add_command(
            label="Вставить", command=lambda: self._menu_edit("<<Paste>>")
        )
        menu.add_separator()
        menu.add_command(
            label="Выделить всё", command=self._menu_select_all
        )
        return menu

    def _popup_menu(self, event: tk.Event) -> str:
        self._menu_target = event.widget
        # Поле результата доступно только для чтения.
        if event.widget is self.log_text:
            editable = "disabled"
        else:
            editable = "normal"
        for label in ("Вырезать", "Вставить"):
            self._context_menu.entryconfig(label, state=editable)
        try:
            self._context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._context_menu.grab_release()
        return "break"

    def _menu_edit(self, sequence: str) -> None:
        target = self._menu_target
        if target is None:
            return
        try:
            target.event_generate(sequence)
        except tk.TclError:
            pass

    def _menu_select_all(self) -> None:
        if self._menu_target is not None:
            _select_all_widget(self._menu_target)

    # ---------------------------------------------------------- Helpers ----

    def _set_window_icon(self) -> None:
        icon = config.resource_path("app.ico")
        if not icon.is_file():
            return
        try:
            self.root.iconbitmap(default=str(icon))
        except tk.TclError:
            pass

    def _report_callback_exception(self, exc_type, exc, tb) -> None:
        """Исключения в Tk-колбэках: в лог-файл и короткий диалог."""
        logging_setup.log_exception(exc_type, exc, tb)
        try:
            messagebox.showerror(
                "Внутренняя ошибка",
                f"{exc_type.__name__}: {exc}\n\n"
                f"Подробности в файле журнала:\n{config.log_path()}",
            )
        except tk.TclError:
            pass

    def paste_from_clipboard(self) -> None:
        """Вставляет текст из буфера - обходит проблему раскладки."""
        try:
            data = self.root.clipboard_get()
        except tk.TclError:
            return
        data = data.strip()
        if not data:
            return
        self.urls_text.insert(tk.INSERT, data + "\n")
        self.urls_text.see(tk.INSERT)

    def import_from_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Загрузить ссылки из файла",
            filetypes=[("Текстовые файлы", "*.txt"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        text = _read_text_file(path)
        if not text:
            messagebox.showerror(
                "Ошибка", f"Не удалось прочитать файл:\n{path}"
            )
            return
        if self.urls_text.get("1.0", tk.END).strip():
            self.urls_text.insert(tk.END, "\n")
        self.urls_text.insert(tk.END, text.strip() + "\n")
        self._logger.info("Загружено ссылок из файла: %s", path)

    def clear_urls(self) -> None:
        self.urls_text.delete("1.0", tk.END)

    def _log(self, message: str) -> None:
        try:
            self.log_text.config(state="normal")
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state="disabled")
        except tk.TclError:
            pass

    def _set_send_enabled(self, enabled: bool) -> None:
        try:
            self.send_btn.config(state="normal" if enabled else "disabled")
        except tk.TclError:
            pass

    def _post_to_ui(self, kind: str, payload: object) -> None:
        """Помещает сообщение в UI-очередь.

        * queue.Full ожидаем в теории - тихо игнорируем;
        * прочие исключения пишем в журнал, но поток не роняем.
        """
        if self._shutdown.is_set():
            return
        try:
            self.msg_queue.put_nowait((kind, payload))
        except queue.Full:
            pass
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("Ошибка очереди UI: %r", exc)

    # --------------------------------------------------------- Настройки ---

    def _apply_settings(self) -> None:
        data = config.load_settings()
        if not data:
            return

        for attribute, field in (
            ("host_entry", "host"),
            ("key_entry", "key"),
            ("key_loc_entry", "key_location"),
        ):
            value = data.get(field)
            if isinstance(value, str) and value:
                _entry_set(getattr(self, attribute), value)

        engine_keys = data.get("engines")
        if isinstance(engine_keys, list):
            selected = {key for key in engine_keys if key in self.engine_vars}
            for key, var in self.engine_vars.items():
                var.set(key in selected)

        geometry = data.get("geometry")
        if isinstance(geometry, str) and GEOMETRY_PATTERN.match(geometry):
            try:
                self.root.geometry(geometry)
            except tk.TclError:
                pass

    def _save_settings(self) -> None:
        try:
            geometry = self.root.winfo_geometry()
        except tk.TclError:
            geometry = DEFAULT_GEOMETRY
        config.save_settings(
            {
                "host": _entry_value(self.host_entry).strip(),
                "key": _entry_value(self.key_entry).strip(),
                "key_location": _entry_value(self.key_loc_entry).strip(),
                "engines": [
                    key for key, var in self.engine_vars.items() if var.get()
                ],
                "geometry": geometry,
            }
        )

    def _on_close(self) -> None:
        """Порядок важен для быстрого завершения процесса:

        1) _save_settings() - сохранить введённое;
        2) _shutdown.set() - фон перестаёт писать в UI-очередь;
        3) session.close() - закрытие сокетов провоцирует немедленный
                             ConnectionError в текущем requests.post(),
                             поток завершается почти мгновенно;
        4) join(0.3)       - короткий вежливый таймаут;
        5) root.destroy()  - уничтожение окна.
        """
        self._save_settings()
        self._shutdown.set()
        self._client.close()

        thread = self._active_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=SHUTDOWN_JOIN_TIMEOUT_SECONDS)

        self._logger.info("Приложение закрыто")
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                try:
                    self._handle_message(kind, payload)
                except tk.TclError:
                    return
        except queue.Empty:
            pass

        if self._shutdown.is_set():
            return
        try:
            self.root.after(QUEUE_POLL_INTERVAL_MS, self._drain_queue)
        except tk.TclError:
            pass

    def _handle_message(self, kind: str, payload: object) -> None:
        if kind == "log":
            self._log(str(payload))
        elif kind == "error":
            self._log(f"[ОШИБКА] {payload}")
            messagebox.showerror("Ошибка", str(payload))
        elif kind == "set_host":
            _entry_set(self.host_entry, str(payload))
        elif kind == "set_key_location":
            _entry_set(self.key_loc_entry, str(payload))
        elif kind == "progress":
            current, total = cast("tuple[int, int]", payload)
            if total:
                self.progress["maximum"] = total
                self.progress["value"] = current
                self.progress_label.config(
                    text=f"Пакетов: {current} / {total}"
                )
        elif kind == "enable_send":
            thread = self._active_thread
            if thread is not None and thread.is_alive():
                # Поток ещё завершается (сообщение из finally успело
                # прийти раньше, чем ОС пометила его как dead).
                # Не выбрасываем сигнал - переотправим его чуть позже.
                self.root.after(
                    ENABLE_SEND_RETRY_DELAY_MS,
                    lambda: self._post_to_ui("enable_send", True),
                )
                return
            # Поток мёртв - разблокируем UI и сбрасываем прогресс.
            self._active_thread = None
            self._set_send_enabled(True)
            self.progress["value"] = 0
            self.progress_label.config(text="")

    # ---------------------------------------------------------- Sending ----

    def _selected_engines(self):
        return get_engines(
            key for key, var in self.engine_vars.items() if var.get()
        )

    def send_urls(self) -> None:
        # Источник истины - живой поток, а не булев флаг.
        thread = self._active_thread
        if thread is not None and thread.is_alive():
            return

        key = _entry_value(self.key_entry).strip()
        if not key:
            messagebox.showerror("Ошибка", "Укажите ключ IndexNow.")
            return
        if not protocol.KEY_PATTERN.match(key):
            messagebox.showerror("Ошибка", protocol.KEY_RULE_TEXT)
            return

        raw_urls = self.urls_text.get("1.0", tk.END).strip()
        if not raw_urls:
            messagebox.showerror("Ошибка", "Введите хотя бы одну ссылку.")
            return

        engines = self._selected_engines()
        if not engines:
            messagebox.showerror(
                "Ошибка", "Выберите хотя бы один приёмник."
            )
            return

        host_raw = _entry_value(self.host_entry).strip()
        key_location_raw = _entry_value(self.key_loc_entry).strip()

        self._save_settings()
        self.progress["value"] = 0
        self.progress_label.config(text="")
        self._set_send_enabled(False)

        # Запуск потока может упасть (например, на исчерпании ресурсов).
        # В этом случае обязательно разблокируем UI.
        try:
            thread = threading.Thread(
                target=self._service.run,
                args=(
                    host_raw,
                    key,
                    key_location_raw,
                    raw_urls,
                    engines,
                    self._shutdown,
                ),
                daemon=True,
            )
            self._active_thread = thread
            thread.start()
        except Exception as exc:  # noqa: BLE001
            self._active_thread = None
            self._set_send_enabled(True)
            messagebox.showerror(
                "Ошибка",
                f"Не удалось запустить фоновую задачу:\n{exc!r}",
            )
