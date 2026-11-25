"""Ponto de entrada: abre o app ou roda um comando de linha de comando.

    pythonw Soom.pyw                  abre o app (ícone perto do relógio)
    python  Soom.pyw --baixar-modelo  baixa/carrega o modelo e sai
    python  Soom.pyw --testar a.wav   transcreve arquivos .wav e mostra o tempo
    python  Soom.pyw --gerar-icone    cria o ícone usado nos atalhos
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

COMMANDS = ("--testar", "--baixar-modelo", "--gerar-icone")


def cli(args: list[str]) -> int:
    from .config import ICON_PATH, SAMPLE_RATE, load_config, setup_logging

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    setup_logging(console=True)
    if args[0] == "--gerar-icone":
        from .tray import make_icon

        ICON_PATH.parent.mkdir(parents=True, exist_ok=True)
        make_icon("pronto", 256).save(ICON_PATH, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
        print(f"Ícone salvo em {ICON_PATH}")
        return 0

    from .audio import read_wav
    from .speech import Transcriber

    tr = Transcriber(load_config())
    t0 = time.perf_counter()
    tr.load()
    if tr.model is None:
        print(f"ERRO: {tr.error}")
        return 1
    print(f"Modelo pronto em {tr.device} ({time.perf_counter() - t0:.1f} s)")
    if args[0] == "--testar":
        for path in args[1:]:
            audio = read_wav(path)
            t0 = time.perf_counter()
            text = tr.transcribe(audio)
            print(f"\n[{Path(path).name}] {len(audio) / SAMPLE_RATE:.1f} s de áudio -> {time.perf_counter() - t0:.2f} s")
            print(text or "(nada reconhecido)")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] in COMMANDS:
        return cli(argv)
    from .app import run_app

    run_app()
    return 0
