"""Caminhos, opções do usuário (config.json) e registro de eventos (soom.log)."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import shutil
import sys
import threading
from pathlib import Path

APP_NAME = "Soom"
APP_DIR = Path(__file__).resolve().parent.parent  # a pasta do programa
LAUNCHER = APP_DIR / "Soom.pyw"
LOCAL = Path(os.environ.get("LOCALAPPDATA") or Path.home())
DATA_DIR = LOCAL / "Soom"  # dados do usuário (e o Python do app, em venv)
OLD_DATA_DIR = LOCAL / "DitadoIA"  # nome antigo do projeto
CONFIG_PATH = DATA_DIR / "config.json"
LOG_PATH = DATA_DIR / "soom.log"
ICON_PATH = DATA_DIR / "soom.ico"
SAMPLE_RATE = 16_000  # o Whisper trabalha com áudio a 16 kHz

PREVIEW_MODES = ("no_campo", "na_caixinha", "desligada")
DEFAULT_CONFIG: dict = {
    "atalho": "win+h",
    "botao_mouse": None,
    "idioma": "pt",
    "modelo": "onnx-community/whisper-large-v3-turbo",
    "quantizacao": None,
    "usar_gpu": True,
    "microfone": None,
    "espaco_no_final": True,
    "restaurar_area_de_transferencia": True,
    "esc_cancela": True,
    "sons": True,
    "mostrar_indicador": True,
    "previa": "no_campo",
    "duracao_maxima_segundos": 600,
    "posicao_indicador": None,
    "iniciar_com_windows": True,
}

log = logging.getLogger("soom")


def setup_logging(console: bool = False) -> None:
    if log.handlers:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(fmt)
    log.addHandler(handler)
    if console:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(fmt)
        log.addHandler(stream)
    sys.excepthook = lambda *exc: log.critical("Erro não tratado", exc_info=exc)
    threading.excepthook = lambda a: log.critical(
        "Erro na thread %s", a.thread.name if a.thread else "?", exc_info=(a.exc_type, a.exc_value, a.exc_traceback)
    )


def load_config() -> dict:
    """Lê o config.json (criando/completando com os valores padrão quando preciso)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Versões antigas (ainda com o nome "Ditado IA") guardavam em outros lugares.
    for legacy in (OLD_DATA_DIR / "config.json", APP_DIR / "config.json"):
        if not CONFIG_PATH.exists() and legacy.exists():
            try:
                shutil.copy2(legacy, CONFIG_PATH)
                log.info("Configurações trazidas de %s", legacy)
            except OSError:
                log.exception("Não consegui trazer o config.json antigo")
    cfg = dict(DEFAULT_CONFIG)
    if not CONFIG_PATH.exists():
        save_config(cfg)
        return cfg
    try:
        user = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        log.error("config.json inválido (%s); usando os valores padrão", e)
        return cfg  # não sobrescreve: o usuário pode corrigir o arquivo
    cfg.update({k: v for k, v in user.items() if k in DEFAULT_CONFIG})
    if "previa" not in user and user.get("previa_ao_vivo") is False:  # opção antiga
        cfg["previa"] = "desligada"
    if set(user) != set(DEFAULT_CONFIG):
        save_config(cfg)  # acrescenta opções novas e tira as que não existem mais
    return cfg


def save_config(cfg: dict) -> None:
    try:
        data = {k: cfg.get(k, v) for k, v in DEFAULT_CONFIG.items()}
        CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.exception("Não consegui salvar o config.json")
