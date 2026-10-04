"""Tkinter-интерфейс. Тонкий слой: собирает данные и показывает сообщения."""
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from .client import IndexNowClient
from .service import IndexNowService


QUEUE_POLL_INTERVAL_MS = 100
SHUTDOWN_JOIN_TIMEOUT_SECONDS = 0.3
ENABLE_SEND_RETRY_DELAY_MS = 50

# IndexNow-ключ: 8-128 символов, [A-Za-z0-9_-].
# Подчёркивание добавлено на случай, если Яндекс его допускает
# (в разных реализациях встречается).
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


# --------------------------------------------------------------------------
# Placeholder-помощники для Entry
# --------------------------------------------------------------------------
def _add_placeholder(entry: ttk.Entry, text: str) -> None:
    entry._placeholder = text
    entry._is_placeholder = False

    def show():
        if not entry.get():
            entry._is_placeholder = True
            entry.insert(0, text)
            entry.configure(foreground="gray")

    def on_focus_in(_event):
        if entry._is_placeholder:
            entry.delete(0, tk.END)
            entry.configure(foreground="black")
            entry._is_placeholder = False

    def on_focus_out(_event):
        if not entry.get():
            show()

    entry.bind("<FocusIn>", on_focus_in)
    entry.bind("<FocusOut>", on_focus_out)
    show()


def _entry_value(entry: ttk.Entry) -> str:
    return "" if getattr(entry, "_is_placeholder", False) else entry.get()


def _entry_set(entry: ttk.Entry, value: str) -> None:
    entry.delete(0, tk.END)
    entry._is_placeholder = False
    entry.configure(foreground="black")
    entry.insert(0, value)


# --------------------------------------------------------------------------
# Главное окно
# --------------------------------------------------------------------------
class IndexNowApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("IndexNow - Отправка страниц в Яндекс")
        self.root.geometry("800x860")
        self.root.resizable(True, True)

        self.msg_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._shutdown = threading.Event()
        self._active_thread: threading.Thread | None = None

        self._client = IndexNowClient()
        self._service = IndexNowService(
            client=self._client, post=self._post_to_ui
        )

        self._build_ui()

        self.root.after(QUEUE_POLL_INTERVAL_MS, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------------------------------------------- UI --

    def _build_ui(self) -> None:
        ttk.Label(
            self.root,
            text="Домен (host):",
            font=("Arial", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=(10, 2))
        self.host_entry = ttk.Entry(self.root, width=80)
        self.host_entry.pack(fill="x", padx=10)
        _add_placeholder(self.host_entry, "example.com")
        self._bind_select_all(self.host_entry)

        ttk.Label(
            self.root,
            text="Ключ IndexNow:",
            font=("Arial", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=(10, 2))
        self.key_entry = ttk.Entry(self.root, width=80)
        self.key_entry.pack(fill="x", padx=10)
        _add_placeholder(
            self.key_entry, "ключ от 8 до 128 символов [A-Za-z0-9_-]"
        )
        self._bind_select_all(self.key_entry)

        ttk.Label(
            self.root,
            text="Расположение файла с ключом (keyLocation, необязательно):",
            font=("Arial", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=(10, 2))
        self.key_loc_entry = ttk.Entry(self.root, width=80)
        self.key_loc_entry.pack(fill="x", padx=10)
        _add_placeholder(self.key_loc_entry, "https://example.com/key.txt")
        self._bind_select_all(self.key_loc_entry)

        ttk.Label(
            self.root,
            text="Ссылки для переиндексации (по одной на строку):",
            font=("Arial", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=(10, 2))
        self.urls_text = scrolledtext.ScrolledText(
            self.root, height=12, wrap=tk.WORD
        )
        self.urls_text.pack(fill="both", expand=True, padx=10)
        self._bind_select_all(self.urls_text)

        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", padx=10, pady=10)

        self.send_btn = ttk.Button(
            btn_frame, text="Отправить в Яндекс", command=self.send_urls
        )
        self.send_btn.pack(side="left", padx=(0, 10))

        ttk.Button(
            btn_frame, text="Очистить ссылки", command=self.clear_urls
        ).pack(side="left", padx=(0, 10))

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
            self.root,
            text="Результат:",
            font=("Arial", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=(6, 2))
        self.log_text = scrolledtext.ScrolledText(
            self.root, height=12, wrap=tk.WORD, state="disabled"
        )
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._bind_select_all(self.log_text)

    @staticmethod
    def _bind_select_all(widget: tk.Widget) -> None:
        """Ctrl+A (Cmd+A) - выделить всё."""
        if hasattr(widget, "tag_add"):  # Text / ScrolledText

            def select_all_text(event):
                event.widget.tag_add(tk.SEL, "1.0", tk.END)
                event.widget.mark_set(tk.INSERT, "1.0")
                event.widget.see(tk.INSERT)
                return "break"

            handler = select_all_text
        else:  # Entry

            def select_all_entry(event):
                event.widget.select_range(0, tk.END)
                event.widget.icursor(tk.END)
                return "break"

            handler = select_all_entry

        widget.bind("<Control-a>", handler)
        widget.bind("<Command-a>", handler)

    # ---------------------------------------------------------- Helpers ----

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
        * прочие исключения пишем в stderr, но поток не роняем.
        """
        if self._shutdown.is_set():
            return
        try:
            self.msg_queue.put_nowait((kind, payload))
        except queue.Full:
            pass
        except Exception as exc:  # noqa: BLE001
            print(f"[ui-queue] unexpected error: {exc!r}", file=sys.stderr)

    def _on_close(self) -> None:
        """Порядок важен для быстрого завершения процесса:

        1) _shutdown.set()  - фон перестаёт писать в UI-очередь;
        2) session.close()  - закрытие сокетов провоцирует немедленный
                              ConnectionError в текущем requests.post(),
                              поток завершается почти мгновенно;
        3) join(0.3)        - короткий вежливый таймаут;
        4) root.destroy()   - уничтожение окна.
        """
        self._shutdown.set()
        self._client.close()

        thread = self._active_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=SHUTDOWN_JOIN_TIMEOUT_SECONDS)

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
            messagebox.showerror("Ошибка", str(payload))
        elif kind == "set_host":
            _entry_set(self.host_entry, str(payload))
        elif kind == "set_key_location":
            _entry_set(self.key_loc_entry, str(payload))
        elif kind == "progress":
            current, total = payload  # type: ignore[misc]
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

    def send_urls(self) -> None:
        # Источник истины - живой поток, а не булев флаг.
        thread = self._active_thread
        if thread is not None and thread.is_alive():
            return

        key = _entry_value(self.key_entry).strip()
        if not key:
            messagebox.showerror("Ошибка", "Укажите ключ IndexNow.")
            return
        if not KEY_PATTERN.match(key):
            messagebox.showerror(
                "Ошибка",
                "Ключ должен содержать от 8 до 128 символов "
                "и состоять только из латинских букв, цифр, дефисов "
                "и подчёркиваний ([A-Za-z0-9_-]).",
            )
            return

        raw_urls = self.urls_text.get("1.0", tk.END).strip()
        if not raw_urls:
            messagebox.showerror("Ошибка", "Введите хотя бы одну ссылку.")
            return

        host_raw = _entry_value(self.host_entry).strip()
        key_location_raw = _entry_value(self.key_loc_entry).strip()

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