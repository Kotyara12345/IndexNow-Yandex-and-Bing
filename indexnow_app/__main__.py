"""Точка входа для `python -m indexnow_app`."""
import tkinter as tk

from .ui import IndexNowApp


def main() -> None:
    root = tk.Tk()
    IndexNowApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()