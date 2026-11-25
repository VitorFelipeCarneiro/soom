"""A janelinha para trocar o atalho: você aperta a combinação desejada e clica em Salvar.

A captura é feita pelo gancho de teclado do app, então funciona até com combinações que o
Windows usaria para ele mesmo (Win+alguma coisa) e com os botões laterais do mouse.
"""

from __future__ import annotations

import ctypes
import gc
import queue
import winreg
from typing import TYPE_CHECKING

from .config import APP_NAME, ICON_PATH, log
from .hotkeys import DEFAULT_HOTKEY, MOUSE_LABELS, hotkey_parts, hotkey_to_text, resolve_shortcut, validate_hotkey
from .win32 import VK_ESCAPE, user32

if TYPE_CHECKING:
    from .app import App

THEMES = {
    "light": {
        "bg": "#f3f3f5", "text": "#1b1b1f", "muted": "#5f6368", "key": "#ffffff", "key_border": "#cfcfd6",
        "key_shadow": "#c2c2ca", "accent": "#0a84ff", "accent_hover": "#0070e0", "accent_text": "#ffffff",
        "button": "#e6e6eb", "button_hover": "#d9d9e0", "disabled": "#c9d9ee", "error": "#c42b1c", "ok": "#0f7b0f",
    },
    "dark": {
        "bg": "#202024", "text": "#f2f2f5", "muted": "#a8a8b3", "key": "#36363d", "key_border": "#4a4a53",
        "key_shadow": "#121215", "accent": "#0a84ff", "accent_hover": "#3a9bff", "accent_text": "#ffffff",
        "button": "#36363d", "button_hover": "#44444c", "disabled": "#2d4466", "error": "#ff6b5f", "ok": "#4cd964",
    },
}  # fmt: skip


def system_dark_mode() -> bool:
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


class ShortcutWindow:
    """Roda na própria thread (o Tkinter exige que a janela fique sempre na mesma thread)."""

    def __init__(self, app: App):
        self.app = app
        self.events: queue.Queue = queue.Queue()  # combinações capturadas pelo gancho
        self.choice: tuple[str, str] | None = None
        self.root = None
        self.raise_requested = False

    # ---- montagem
    def show(self) -> None:
        import tkinter as tk
        import tkinter.font as tkfont

        c = self.c = THEMES["dark" if system_dark_mode() else "light"]
        root = self.root = tk.Tk()
        root.withdraw()
        root.title(f"{APP_NAME} – atalho")
        root.configure(bg=c["bg"])
        root.resizable(False, False)
        try:
            root.iconbitmap(str(ICON_PATH))
        except tk.TclError:
            pass
        self.s = max(1.0, root.winfo_fpixels("1i") / 96)
        families = set(tkfont.families())
        display = "Segoe UI Variable Display" if "Segoe UI Variable Display" in families else "Segoe UI"
        body = "Segoe UI Variable Text" if "Segoe UI Variable Text" in families else "Segoe UI"
        self.f_title = tkfont.Font(family=display, size=15, weight="bold")
        self.f_body = tkfont.Font(family=body, size=10)
        self.f_key = tkfont.Font(family=display, size=12, weight="bold")

        px = lambda v: round(v * self.s)  # noqa: E731
        frame = tk.Frame(root, bg=c["bg"], padx=px(28), pady=px(22))
        frame.pack()
        tk.Label(frame, text="Atalho do Soom", font=self.f_title, bg=c["bg"], fg=c["text"]).pack(anchor="w")
        tip = "Aperte a combinação que vai ligar e desligar o ditado (ex.: Ctrl+Alt+Espaço)\nou um botão lateral do mouse."
        tk.Label(frame, text=tip, font=self.f_body, bg=c["bg"], fg=c["muted"], justify="left").pack(anchor="w", pady=(px(4), px(16)))
        self.canvas = tk.Canvas(frame, width=px(430), height=px(62), bg=c["bg"], highlightthickness=0)
        self.canvas.pack()
        self.status = tk.Label(frame, text="", font=self.f_body, bg=c["bg"], fg=c["muted"])
        self.status.pack(pady=(px(10), px(18)))
        row = tk.Frame(frame, bg=c["bg"])
        row.pack(fill="x")
        self._button(row, "Usar o padrão (Win+H)", self.use_default).pack(side="left")
        self.save_btn = self._button(row, "Salvar", self.save, primary=True)
        self.save_btn.pack(side="right")
        self._button(row, "Cancelar", self.close).pack(side="right", padx=(0, px(8)))
        self._set_save(False)
        self._show_current()

        root.protocol("WM_DELETE_WINDOW", self.close)
        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 3}")
        if c is THEMES["dark"]:
            self._dark_title_bar()
        root.deiconify()
        root.attributes("-topmost", True)
        root.lift()
        root.focus_force()
        self.app.hook.start_capture(self.events.put)
        root.after(30, self._poll)
        try:
            root.mainloop()
        finally:
            self.app.hook.stop_capture()
            # O Tkinter precisa ser desmontado na mesma thread em que foi criado; se sobrar alguma
            # referência para ser apagada em outra thread, o processo inteiro cai.
            self.root = self.canvas = self.status = self.save_btn = None
            self.f_title = self.f_body = self.f_key = None
            del root
            gc.collect()

    def _button(self, parent, text: str, command, primary: bool = False):
        import tkinter as tk

        c = self.c
        bg, hover, fg = (c["accent"], c["accent_hover"], c["accent_text"]) if primary else (c["button"], c["button_hover"], c["text"])
        b = tk.Label(parent, text=text, font=self.f_body, bg=bg, fg=fg, padx=round(16 * self.s), pady=round(7 * self.s), cursor="hand2")
        b.enabled, b.colors = True, (bg, hover)
        b.bind("<Enter>", lambda _: b.enabled and b.configure(bg=hover))
        b.bind("<Leave>", lambda _: b.enabled and b.configure(bg=bg))
        b.bind("<Button-1>", lambda _: b.enabled and command())
        return b

    def _set_save(self, enabled: bool) -> None:
        b = self.save_btn
        b.enabled = enabled
        b.configure(bg=b.colors[0] if enabled else self.c["disabled"], cursor="hand2" if enabled else "arrow")

    def _dark_title_bar(self) -> None:
        try:
            hwnd = user32.GetParent(self.root.winfo_id())
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        except (AttributeError, OSError):
            pass

    # ---- desenho das "teclas"
    def _draw_keys(self, parts: list[str], tone: str = "normal") -> None:
        cv, c, s = self.canvas, self.c, self.s
        cv.delete("all")
        h, gap, plus = round(42 * s), round(9 * s), self.f_key.measure("+")
        widths = [max(round(46 * s), self.f_key.measure(p) + round(30 * s)) for p in parts]
        total = sum(widths) + (len(parts) - 1) * (plus + 2 * gap)
        width = max(round(430 * s), total + round(20 * s))
        cv.configure(width=width)
        border = {"normal": c["key_border"], "new": c["accent"], "error": c["error"]}[tone]
        x, y = (width - total) / 2, round(6 * s)
        for i, (part, w) in enumerate(zip(parts, widths, strict=True)):
            if i:
                cv.create_text(x + gap + plus / 2, y + h / 2, text="+", font=self.f_key, fill=c["muted"])
                x += plus + 2 * gap
            self._round_rect(x, y + round(3 * s), x + w, y + h + round(3 * s), round(9 * s), fill=c["key_shadow"], outline="")
            self._round_rect(x, y, x + w, y + h, round(9 * s), fill=c["key"], outline=border, width=max(1, round(1.5 * s)))
            cv.create_text(x + w / 2, y + h / 2, text=part, font=self.f_key, fill=c["text"])
            x += w

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _show_current(self) -> None:
        mods, vk, mouse, _ = resolve_shortcut(self.app.cfg)
        parts = hotkey_parts(mods, vk) if vk is not None else [MOUSE_LABELS[mouse]]
        self._draw_keys(parts)
        self.status.configure(text="Atalho atual. Aperte o novo atalho agora…", fg=self.c["muted"])

    # ---- eventos
    def _poll(self) -> None:
        if self.root is None:
            return
        if self.raise_requested:
            self.raise_requested = False
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        try:
            while True:
                self.handle(self.events.get_nowait())
        except queue.Empty:
            pass
        if self.root is not None:
            self.root.after(30, self._poll)

    def handle(self, event: tuple) -> None:
        """Uma combinação capturada: ('teclado', mods, tecla) ou ('mouse', botão)."""
        c = self.c
        if event[0] == "mouse":
            self.choice = ("mouse", event[1])
            self._draw_keys([MOUSE_LABELS[event[1]]], "new")
            self.status.configure(text="Clique em Salvar para usar este botão do mouse.", fg=c["ok"])
            self._set_save(True)
            return
        _, mods, vk = event
        if vk == VK_ESCAPE and not mods:
            self.close()
            return
        problem = validate_hotkey(mods, vk)
        if problem:
            self.choice = None
            self._draw_keys(hotkey_parts(mods, vk), "error")
            self.status.configure(text=problem, fg=c["error"])
            self._set_save(False)
            return
        self.choice = ("teclado", hotkey_to_text(mods, vk))
        self._draw_keys(hotkey_parts(mods, vk), "new")
        self.status.configure(text="Clique em Salvar para usar este atalho.", fg=c["ok"])
        self._set_save(True)

    def save(self) -> None:
        if self.choice:
            self.app.set_shortcut(*self.choice)
            self.close()

    def use_default(self) -> None:
        self.app.set_shortcut("teclado", DEFAULT_HOTKEY)
        self.close()

    def close(self) -> None:
        self.app.hook.stop_capture()
        if self.root is not None:
            root, self.root = self.root, None
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                log.exception("Erro ao fechar a janela do atalho")
