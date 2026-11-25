"""O ícone perto do relógio: mostra o estado (pela cor) e tem o menu do Soom."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import numpy as np

from .config import APP_NAME, log

if TYPE_CHECKING:
    from .app import App

# Degradê de cada estado (as cores da luz do indicador)
STATE_GRADIENTS = {
    "pronto": ((10, 132, 255), (94, 92, 230), (191, 90, 242)),  # azul -> roxo (Siri)
    "ouvindo": ((255, 55, 95), (255, 45, 140), (255, 149, 0)),  # rosa -> laranja
    "transcrevendo": ((255, 149, 0), (255, 179, 0), (255, 214, 10)),  # âmbar
    "carregando": ((10, 132, 255), (64, 156, 255), (90, 200, 250)),  # azul -> ciano
    "erro": ((120, 20, 30), (170, 30, 40), (200, 40, 50)),  # vermelho escuro
}


def make_icon(state: str = "pronto", size: int = 64):
    """Esfera em degradê com um microfone branco (desenhada grande e reduzida, para ficar suave)."""
    from PIL import Image, ImageDraw

    big = size * 4
    y, x = np.mgrid[0:big, 0:big].astype(np.float32) / (big - 1)
    t = np.clip((x + y) / 2, 0, 1)
    cols = np.array(STATE_GRADIENTS.get(state, STATE_GRADIENTS["pronto"]), np.float32) / 255
    rgb = np.stack([np.interp(t, (0, 0.5, 1), cols[:, i]) for i in range(3)], axis=-1)
    shine = np.exp(-(((x - 0.32) ** 2) + ((y - 0.22) ** 2)) / 0.06)[..., None] * 0.28  # reflexo no alto
    rgb = np.clip(rgb + shine, 0, 1)
    img = Image.fromarray((rgb * 255).astype(np.uint8)).convert("RGBA")
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    img.putalpha(mask)
    d = ImageDraw.Draw(img)
    cx, lw = big / 2, max(4, big // 16)
    w, h, top = big * 0.22, big * 0.36, big * 0.17
    d.rounded_rectangle((cx - w / 2, top, cx + w / 2, top + h), radius=w / 2, fill="white")
    d.arc((cx - w * 0.95, top + h * 0.3, cx + w * 0.95, top + h * 1.28), start=0, end=180, fill="white", width=lw)
    d.line((cx, top + h * 1.28, cx, big * 0.80), fill="white", width=lw)
    d.line((cx - w * 0.6, big * 0.80, cx + w * 0.6, big * 0.80), fill="white", width=lw)
    return img.resize((size, size), Image.LANCZOS)


class Tray:
    def __init__(self, app: App):
        import pystray

        self.images = {state: make_icon(state) for state in STATE_GRADIENTS}
        self._shown = None
        noop = lambda: None  # noqa: E731
        menu = pystray.Menu(
            pystray.MenuItem(lambda _: f"{APP_NAME}: {app.status_text()}", noop, enabled=False),
            pystray.MenuItem(lambda _: f"Atalho: {app.hotkey_text}", noop, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Trocar atalho...", lambda: app.open_shortcut_window()),
            pystray.MenuItem("Iniciar com o Windows", lambda: app.toggle_autostart(), checked=lambda _: app.autostart_enabled()),
            pystray.MenuItem("Voltar o indicador ao lugar padrão", lambda: app.reset_overlay_position()),
            pystray.MenuItem(
                "Avançado",
                pystray.Menu(
                    pystray.MenuItem("Configurações avançadas (config.json)", lambda: app.open_config()),
                    pystray.MenuItem("Reiniciar o Soom", lambda: app.restart()),
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair do Soom", lambda: app.quit()),
        )
        self.icon = pystray.Icon("Soom", self.images["carregando"], APP_NAME, menu)

    def run(self) -> None:
        threading.Thread(target=self.icon.run, name="icone", daemon=True).start()

    def update(self, state: str, text: str) -> None:
        if (state, text) == self._shown:  # nada mudou: não mexe no ícone
            return
        self._shown = (state, text)
        try:
            self.icon.icon = self.images[state]
            self.icon.title = f"{APP_NAME} - {text}"[:127]
            self.icon.update_menu()
        except Exception:  # noqa: BLE001 - o ícone pode ainda não estar pronto
            pass

    def refresh_menu(self) -> None:
        try:
            self.icon.update_menu()
        except Exception:  # noqa: BLE001
            pass

    def notify(self, message: str) -> None:
        try:
            self.icon.notify(message, APP_NAME)
        except Exception:  # noqa: BLE001
            log.info("Aviso: %s", message)

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:  # noqa: BLE001
            pass
