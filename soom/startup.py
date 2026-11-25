"""Iniciar junto com o Windows (entrada "Soom" em HKCU\\...\\Run, só para o seu usuário)."""

from __future__ import annotations

import shlex
import sys
import winreg
from pathlib import Path

from .config import LAUNCHER, log

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "Soom"


def command() -> str:
    """Como abrir o Soom sem janela de console: pythonw.exe do ambiente do app + o lançador."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe") if exe.name.lower() == "python.exe" else exe
    return f'"{pythonw}" "{LAUNCHER}"'


def registered() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, VALUE)[0]
    except OSError:
        return None


def is_enabled() -> bool:
    return registered() is not None


def enable() -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, command())
    log.info("Iniciar com o Windows: ligado")


def disable() -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE)
    except OSError:
        pass
    log.info("Iniciar com o Windows: desligado")


def _points_to_existing_files(cmd: str) -> bool:
    try:
        parts = shlex.split(cmd, posix=False)
    except ValueError:
        return False
    return len(parts) >= 2 and all(Path(p.strip('"')).exists() for p in parts[:2])


def sync(wanted: bool) -> None:
    """Deixa o registro como a opção pede. Se a entrada existente aponta para arquivos que não
    existem mais (pasta movida/apagada), corrige; se aponta para outra instalação válida, respeita."""
    try:
        current = registered()
        if wanted and (current is None or not _points_to_existing_files(current)):
            enable()
        elif not wanted and current is not None:
            disable()
    except OSError:
        log.exception("Não consegui ajustar o 'iniciar com o Windows'")
