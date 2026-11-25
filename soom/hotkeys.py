"""Atalho global (teclado e/ou um botão do mouse), captura de um atalho novo e a vigilância de
cliques e teclas durante o ditado."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading

from . import win32
from .config import log
from .win32 import (
    HC_ACTION,
    HOOKPROC,
    LLKHF_INJECTED,
    LLMHF_INJECTED,
    MOD_VKS,
    MODIFIER_VKS,
    VK_BACK,
    VK_ESCAPE,
    VK_MASK,
    VK_RETURN,
    WH_KEYBOARD_LL,
    WH_MOUSE_LL,
    WM_APP,
    WM_KEYDOWN,
    WM_LBUTTONDOWN,
    WM_MBUTTONDOWN,
    WM_MBUTTONUP,
    WM_RBUTTONDOWN,
    WM_SYSKEYDOWN,
    WM_TIMER,
    WM_XBUTTONDOWN,
    WM_XBUTTONUP,
    KBDLLHOOKSTRUCT,
    MSLLHOOKSTRUCT,
    kernel32,
    user32,
)

WM_APP_REHOOK = WM_APP + 1  # mensagem interna: reinstalar os ganchos (ex.: ligar o de mouse)
MOUSE_MSGS = (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_MBUTTONUP, WM_XBUTTONDOWN, WM_XBUTTONUP)
MOUSE_BUTTONS = ("lateral1", "lateral2", "meio")
MOUSE_LABELS = {
    "lateral1": "Botão lateral 1 do mouse",
    "lateral2": "Botão lateral 2 do mouse",
    "meio": "Botão do meio do mouse",
}
DEFAULT_HOTKEY = "win+h"

# ---------------------------------------------------------------- nomes das teclas
MOD_ORDER = ("win", "ctrl", "alt", "shift")
MOD_LABELS = {"win": "Win", "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift"}
MOD_ALIASES = {"control": "ctrl", "ctl": "ctrl", "windows": "win", "super": "win", "cmd": "win"}
KEY_TOKENS = {  # tecla -> nome no config.json
    0x20: "space", 0x0D: "enter", 0x09: "tab", 0x08: "backspace", 0x1B: "esc", 0x13: "pause",
    0x91: "scrolllock", 0x2D: "insert", 0x2E: "delete", 0x24: "home", 0x23: "end", 0x21: "pageup",
    0x22: "pagedown", 0x14: "capslock", 0x5D: "apps", 0x2C: "printscreen",
    0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down",
}  # fmt: skip
KEY_NAMES = {name: vk for vk, name in KEY_TOKENS.items()} | {
    "espaco": 0x20, "espaço": 0x20, "pausa": 0x13, "ins": 0x2D, "del": 0x2E, "menu": 0x5D,
}  # fmt: skip
KEY_LABELS = {  # tecla -> nome mostrado ao usuário
    0x20: "Espaço", 0x0D: "Enter", 0x09: "Tab", 0x08: "Backspace", 0x1B: "Esc", 0x13: "Pause",
    0x91: "Scroll Lock", 0x2D: "Insert", 0x2E: "Delete", 0x24: "Home", 0x23: "End", 0x21: "Page Up",
    0x22: "Page Down", 0x14: "Caps Lock", 0x5D: "Menu", 0x2C: "Print Screen",
    0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓",
}  # fmt: skip


def _is_letter_or_digit(vk: int) -> bool:
    return 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39


def key_token(vk: int) -> str:
    if _is_letter_or_digit(vk):
        return chr(vk).lower()
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x6F}"
    if 0x60 <= vk <= 0x69:
        return f"num{vk - 0x60}"
    return KEY_TOKENS.get(vk, f"vk{vk}")


def key_label(vk: int) -> str:
    if _is_letter_or_digit(vk):
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    if 0x60 <= vk <= 0x69:
        return f"Num {vk - 0x60}"
    return KEY_LABELS.get(vk) or win32.key_name(vk).capitalize() or f"Tecla {vk}"


def parse_hotkey(text: str) -> tuple[frozenset[str], int]:
    """'ctrl+alt+space' -> ({'ctrl', 'alt'}, código da tecla)."""
    mods: set[str] = set()
    vk = None
    for raw in text.replace(" ", "").lower().split("+"):
        if not raw:
            continue
        part = MOD_ALIASES.get(raw, raw)
        if part in MOD_VKS:
            mods.add(part)
            continue
        if vk is not None:
            raise ValueError(f"mais de uma tecla principal em '{text}'")
        if part in KEY_NAMES:
            vk = KEY_NAMES[part]
        elif len(part) == 1 and part.isascii() and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x6F + int(part[1:])
        elif part.startswith("num") and part[3:].isdigit() and len(part) == 4:
            vk = 0x60 + int(part[3:])
        elif part.startswith("vk") and part[2:].isdigit() and 0 < int(part[2:]) < 256:
            vk = int(part[2:])
        else:
            raise ValueError(f"tecla desconhecida '{raw}'")
    if vk is None:
        raise ValueError(f"falta a tecla principal em '{text}'")
    return frozenset(mods), vk


def hotkey_to_text(mods, vk: int) -> str:
    """O inverso de parse_hotkey: ({'ctrl', 'alt'}, espaço) -> 'ctrl+alt+space'."""
    return "+".join([m for m in MOD_ORDER if m in mods] + [key_token(vk)])


def hotkey_parts(mods, vk: int) -> list[str]:
    """Nomes para mostrar: ({'win'}, H) -> ['Win', 'H']."""
    return [MOD_LABELS[m] for m in MOD_ORDER if m in mods] + [key_label(vk)]


def describe_hotkey(mods, vk: int) -> str:
    return "+".join(hotkey_parts(mods, vk))


def hotkey_label(text: str) -> str:
    return describe_hotkey(*parse_hotkey(text))


def validate_hotkey(mods, vk: int) -> str | None:
    """Motivo para recusar um atalho (ou None se ele for bom)."""
    typing_key = (
        _is_letter_or_digit(vk) or vk in (0x20, 0x0D, 0x09, 0x08, 0x1B) or 0x25 <= vk <= 0x28
        or 0x60 <= vk <= 0x6F or vk >= 0xBA
    )  # fmt: skip
    if typing_key and not (set(mods) - {"shift"}):
        return "Combine com Ctrl, Alt ou Win: sozinha, essa tecla atrapalharia a digitação."
    if set(mods) == {"win"} and vk == ord("L"):
        return "Win+L é do Windows (bloquear a tela) e não pode ser usado."
    return None


def resolve_shortcut(cfg: dict) -> tuple[frozenset[str], int | None, str | None, str]:
    """Lê o atalho do config: (modificadores, tecla ou None, botão do mouse ou None, descrição)."""
    mouse = cfg.get("botao_mouse") if cfg.get("botao_mouse") in MOUSE_BUTTONS else None
    mods, vk = frozenset(), None
    text = (cfg.get("atalho") or "").strip()
    if text:
        try:
            mods, vk = parse_hotkey(text)
        except ValueError as e:
            log.error("Atalho inválido (%s); usando Win+H", e)
    if vk is None and mouse is None:
        mods, vk = parse_hotkey(DEFAULT_HOTKEY)
    labels = ([describe_hotkey(mods, vk)] if vk is not None else []) + ([MOUSE_LABELS[mouse]] if mouse else [])
    return mods, vk, mouse, " ou ".join(labels)


# ---------------------------------------------------------------- o gancho
class InputHook(threading.Thread):
    """Gancho global de teclado (e de mouse, quando preciso) que funciona em qualquer app.

    - Detecta o atalho (e o "engole", para o Windows não abrir o ditado dele).
    - Durante o ditado: Esc cancela, Enter termina e envia, e qualquer outro clique/tecla avisa o
      app (`on_activity`), que para de escrever no campo.
    - Modo captura (`start_capture`): a próxima combinação apertada é entregue a quem pediu (a
      janela de trocar o atalho) e não chega a nenhum outro programa.
    """

    def __init__(self, mods, vk, mouse_button, on_toggle, on_cancel, on_activity=None, on_enter=None):
        super().__init__(name="atalho", daemon=True)
        self.mods, self.vk = mods, vk
        self.mouse_button = mouse_button if mouse_button in MOUSE_BUTTONS else None
        self.on_toggle, self.on_cancel = on_toggle, on_cancel
        self.on_activity = on_activity or (lambda kind: None)
        self.on_enter = on_enter
        self.esc_active = False
        self.watching = False  # durante o ditado
        self.ignore_rect = None  # cliques aqui (o indicador, para arrastar) não contam como atividade
        self.capture_cb = None  # modo captura de um atalho novo
        self._captured: set[int] = set()
        self._key_down = self._swallow_up = self._esc_swallow_up = self._enter_swallow_up = False
        self._kb_ref = HOOKPROC(self._kb_proc)  # as referências precisam ficar vivas
        self._ms_ref = HOOKPROC(self._mouse_proc)
        self._kb_hook = self._ms_hook = None
        self._thread_id = 0

    # ---- controles (chamados de outras threads)
    def _rehook(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_APP_REHOOK, 0, 0)

    def set_watch(self, on: bool) -> None:
        self.watching = on
        self._rehook()  # o gancho de mouse só fica ligado quando precisa

    def set_hotkey(self, mods, vk, mouse_button) -> None:
        self.mods, self.vk = mods, vk
        self.mouse_button = mouse_button if mouse_button in MOUSE_BUTTONS else None
        self._key_down = self._swallow_up = False
        self._rehook()

    def start_capture(self, callback) -> None:
        """Entrega a `callback` cada combinação apertada: ('teclado', mods, tecla) ou ('mouse', botão)."""
        self._captured.clear()
        self.capture_cb = callback
        self._rehook()

    def stop_capture(self) -> None:
        self.capture_cb = None
        self._rehook()

    # ---- gancho de teclado
    def _mods_now(self) -> frozenset[str]:
        return frozenset(name for name, vks in MOD_VKS.items() if any(win32.key_is_down(v) for v in vks))

    def _mask_win(self) -> None:
        """Depois de engolir uma tecla com o Win apertado, evita que o Menu Iniciar abra."""
        threading.Thread(target=win32.send_keys, args=([(VK_MASK, False), (VK_MASK, True)],), daemon=True).start()

    def _fire(self, from_keyboard: bool) -> None:
        if from_keyboard and "win" in self.mods:
            self._mask_win()
        self.on_toggle()

    def _capture_key(self, vk: int, down: bool) -> int:
        """Modo captura: os modificadores passam (para o Windows saber que estão apertados); a
        tecla principal é entregue e engolida."""
        if vk in MODIFIER_VKS:
            return 0
        if down:
            if vk not in self._captured:
                self._captured.add(vk)
                mods = self._mods_now()
                if "win" in mods:
                    self._mask_win()
                self.capture_cb(("teclado", mods, vk))
        else:
            self._captured.discard(vk)
        return 1

    def _kb_proc(self, n_code, w_param, l_param):
        try:
            if n_code == HC_ACTION:
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not kb.flags & LLKHF_INJECTED:  # ignora o que nós mesmos digitamos
                    down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                    vk = kb.vkCode
                    if self.capture_cb is not None:
                        if self._capture_key(vk, down):
                            return 1
                    elif vk == self.vk:
                        if down:
                            if self._key_down or self._mods_ok():
                                if not self._key_down:
                                    self._key_down = True
                                    self._fire(from_keyboard=True)
                                self._swallow_up = True
                                return 1
                        else:
                            self._key_down = False
                            if self._swallow_up:
                                self._swallow_up = False
                                return 1
                    elif vk == VK_ESCAPE:
                        if down and self.esc_active:
                            self._esc_swallow_up = True
                            self.on_cancel()
                            return 1
                        if not down and self._esc_swallow_up:
                            self._esc_swallow_up = False
                            return 1
                    elif vk == VK_RETURN:
                        if down and self.watching and self.on_enter and not win32.modifiers_down():
                            self._enter_swallow_up = True
                            self.on_enter()
                            return 1
                        if not down and self._enter_swallow_up:
                            self._enter_swallow_up = False
                            return 1
                    if down and self.watching and vk not in MODIFIER_VKS and self.capture_cb is None:
                        self.on_activity("Backspace" if vk == VK_BACK else "teclado")
        except Exception:  # noqa: BLE001, S110 - nunca deixar exceção escapar de um gancho do sistema
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _mods_ok(self) -> bool:
        return all(any(win32.key_is_down(v) for v in vks) == (name in self.mods) for name, vks in MOD_VKS.items())

    # ---- gancho de mouse
    def _mouse_proc(self, n_code, w_param, l_param):
        try:
            if n_code == HC_ACTION and w_param in MOUSE_MSGS:
                ms = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not ms.flags & LLMHF_INJECTED:
                    side = None
                    if w_param in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                        side = {1: "lateral1", 2: "lateral2"}.get((ms.mouseData >> 16) & 0xFFFF)
                    if self.capture_cb is not None:
                        if side:  # botões laterais podem virar o atalho
                            if w_param == WM_XBUTTONDOWN:
                                self.capture_cb(("mouse", side))
                            return 1
                        return user32.CallNextHookEx(None, n_code, w_param, l_param)
                    button = "meio" if w_param in (WM_MBUTTONDOWN, WM_MBUTTONUP) else side
                    if button and button == self.mouse_button:
                        if w_param in (WM_MBUTTONDOWN, WM_XBUTTONDOWN):
                            self._fire(from_keyboard=False)
                        return 1
                    if self.watching and w_param in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_XBUTTONDOWN):
                        rect = self.ignore_rect
                        on_overlay = rect and rect[0] <= ms.pt.x < rect[2] and rect[1] <= ms.pt.y < rect[3]
                        if not on_overlay:
                            self.on_activity("mouse")
        except Exception:  # noqa: BLE001, S110
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    # ---- instalação
    def _install(self) -> None:
        hmod = kernel32.GetModuleHandleW(None)
        old = (self._kb_hook, self._ms_hook)
        self._kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_ref, hmod, 0)
        if not self._kb_hook:
            log.error("Não consegui instalar o gancho de teclado (erro %s)", ctypes.get_last_error())
        need_mouse = self.mouse_button or self.watching or self.capture_cb is not None
        self._ms_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_ref, hmod, 0) if need_mouse else None
        for hook in old:
            if hook:
                user32.UnhookWindowsHookEx(hook)

    def run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        self._install()
        # O Windows remove em silêncio ganchos que demoram a responder: reinstala a cada minuto.
        user32.SetTimer(None, 0, 60_000, None)
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message in (WM_TIMER, WM_APP_REHOOK):
                self._install()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
