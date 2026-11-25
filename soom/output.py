"""Como o texto chega ao campo: digitado ao vivo (com correções) ou colado no final."""

from __future__ import annotations

import math
import threading
import time
import unicodedata

from . import win32
from .config import log
from .text import bare_word, common_prefix_len
from .win32 import VK_CONTROL, VK_V


def insert_text(text: str, restore_clipboard: bool) -> None:
    """Cola o texto onde está o cursor (área de transferência + Ctrl+V)."""
    import pyperclip

    win32.wait_for_modifier_release()
    previous = None
    if restore_clipboard:
        try:
            previous = pyperclip.paste()
        except pyperclip.PyperclipException:
            previous = None
    pyperclip.copy(text)
    time.sleep(0.05)
    win32.send_keys([(VK_CONTROL, False), (VK_V, False), (VK_V, True), (VK_CONTROL, True)])
    if restore_clipboard and previous:
        time.sleep(0.7)
        try:
            if pyperclip.paste() == text:  # só restaura se ninguém copiou outra coisa nesse meio tempo
                pyperclip.copy(previous)
        except pyperclip.PyperclipException:
            log.warning("Não consegui restaurar a área de transferência")


class LiveTyper:
    """Escreve a prévia direto no campo onde está o cursor, letra a letra, e vai corrigindo
    conforme a IA revisa.

    Cada revisão da IA vira uma meta (`goal`); uma thread digita em direção a ela em pequenas
    rajadas, para o texto fluir em vez de pular em blocos. Só mexe no campo enquanto a mesma janela
    estiver em primeiro plano; quem decide parar quando o usuário clica ou digita é o App (`detach`).
    """

    KEEP_EXACT = 3  # as últimas palavras sempre seguem a IA à risca (pontuação inclusive)
    TICK = 0.02  # intervalo entre as rajadas de letras
    SPREAD = 0.25  # tempo para escrever o texto novo de uma revisão
    FINAL_SPREAD = 0.2  # ao terminar, completa mais rápido ainda

    def __init__(self):
        self.typed = ""  # o que já está no campo
        self.goal = ""  # o que deve ficar no campo
        self.target = 0  # janela onde estamos escrevendo
        self.detached = False
        self.reported = False
        self._closed = False
        self._deadline = 0.0
        self._cv = threading.Condition()
        threading.Thread(target=self._run, name="digitacao", daemon=True).start()

    @staticmethod
    def _clean(text: str) -> str:
        return unicodedata.normalize("NFC", text.replace("\r", " ").replace("\n", " "))

    @classmethod
    def _merge(cls, base: str, text: str) -> str:
        """Nova meta: o texto novo, mas sem reescrever o resto da frase só porque a IA trocou a
        pontuação de uma palavra antiga (ex.: "todos." -> "todos!")."""
        if not base or not text:
            return text
        old, new = base.split(" "), text.split(" ")
        limit = len(old) - cls.KEEP_EXACT
        k = 0
        while k < len(old) and k < len(new):
            if old[k] != new[k] and not (k < limit and bare_word(old[k]) and bare_word(old[k]) == bare_word(new[k])):
                break
            k += 1
        return " ".join(old[:k] + new[k:])

    def _set_goal(self, text: str, spread: float) -> None:
        self.goal = self._merge(self.goal, text)
        self._deadline = time.monotonic() + spread
        self._cv.notify_all()

    def _step(self) -> None:
        """Um passo da digitação (chamado com o lock)."""
        if self.typed and win32.foreground_window() != self.target:
            self.detached = True
            return
        if win32.modifiers_down():  # com Ctrl/Win apertado, as teclas virariam atalhos: espera soltar
            return
        p = common_prefix_len(self.typed, self.goal)
        if p < len(self.typed):  # a IA mudou algo já escrito: apaga até o ponto da mudança
            win32.type_text("", backspaces=len(self.typed) - p)
            self.typed = self.typed[:p]
            return
        rest = self.goal[p:]
        time_left = max(self.TICK, self._deadline - time.monotonic())
        n = max(1, math.ceil(len(rest) * self.TICK / time_left))
        win32.type_text(rest[:n])
        self.typed += rest[:n]

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._closed and (self.detached or self.typed == self.goal):
                    self._cv.wait()
                if self._closed:
                    return
                try:
                    self._step()
                except Exception:  # noqa: BLE001
                    log.exception("Erro ao digitar no campo")
                    self.detached = True
                self._cv.notify_all()
            time.sleep(self.TICK)

    def update(self, text: str) -> bool:
        """Nova prévia da IA. Devolve False se não dá mais para escrever no campo."""
        text = self._clean(text)
        with self._cv:
            if self.detached or self._closed:
                return False
            if not self.typed:
                self.target = win32.foreground_window()  # o alvo é onde o cursor está quando o texto começa
            elif win32.foreground_window() != self.target:
                self.detached = True
                return False
            self._set_goal(text, self.SPREAD)
            return True

    def finish(self, text: str) -> bool:
        """Deixa o campo com o texto final (espera terminar de digitar). False = não foi possível."""
        text = self._clean(text)
        win32.wait_for_modifier_release(5.0)
        with self._cv:
            try:
                if self.detached:
                    return False
                if not self.typed:
                    self.target = win32.foreground_window()
                elif win32.foreground_window() != self.target:
                    self.detached = True
                    return False
                self._set_goal(text, self.FINAL_SPREAD)
                end = time.monotonic() + 15
                while not self.detached and self.typed != self.goal and time.monotonic() < end:
                    self._cv.wait(0.05)
                return not self.detached and self.typed == self.goal
            finally:
                self._closed = True
                self._cv.notify_all()

    def wait_idle(self, timeout: float = 5.0) -> None:
        with self._cv:
            end = time.monotonic() + timeout
            while not self.detached and self.typed != self.goal and time.monotonic() < end:
                self._cv.wait(0.05)

    def erase(self) -> None:
        """Apaga o que a prévia escreveu (ditado cancelado) e para de escrever."""
        with self._cv:
            if self.typed and not self.detached and win32.foreground_window() == self.target:
                win32.wait_for_modifier_release()
                win32.type_text("", backspaces=len(self.typed))
                self.typed = ""
            self.goal = self.typed
            self.detached = self._closed = True
            self._cv.notify_all()

    def detach(self) -> None:
        with self._cv:
            self.detached = True
            self._cv.notify_all()

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()
