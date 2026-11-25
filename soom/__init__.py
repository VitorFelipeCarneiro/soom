"""Soom: ditado por voz com IA, 100% local, para qualquer campo de texto do Windows.

Módulos:
    config    caminhos, opções (config.json) e registro (soom.log)
    win32     acesso às funções do Windows (teclado, mouse, janelas, desenho)
    hotkeys   atalho global, captura de um atalho novo e vigilância de cliques/teclas
    audio     gravação do microfone e tratamento do áudio
    text      regras de texto (juntar pedaços, filtrar "alucinações")
    speech    o modelo de voz (Whisper) e a prévia ao vivo
    output    como o texto chega ao campo (digitado ao vivo ou colado)
    overlay   o indicador de vidro com a luz estilo Siri
    shortcut  a janelinha para trocar o atalho
    startup   iniciar junto com o Windows
    tray      o ícone perto do relógio
    app       o maestro que liga tudo
"""

import os

__version__ = "1.0.0"

# Privacidade: o app não manda nada para a internet depois de baixar o modelo.
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
