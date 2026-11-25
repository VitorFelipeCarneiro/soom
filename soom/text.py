"""Regras de texto: juntar os pedaços transcritos, filtrar "alucinações" do Whisper e comparar
versões da prévia."""

from __future__ import annotations

from .config import log

# Frases que o Whisper inventa em trechos sem fala (vieram de legendas de vídeo no treino).
HALLUCINATIONS = (
    "amara.org",
    "legendas pela comunidade",
    "legenda adriana zanotto",
    "inscreva-se no canal",
    "obrigado por assistir",
    "subtitles by",
)

# Palavras que, no começo de um pedaço, quase sempre continuam a frase anterior.
CONTINUATION_WORDS = frozenset(
    "a ao aos as com como da das de depois do dos e ela elas ele eles em então entao enquanto essa esse "
    "isso já ja logo mais mas na nas no nos nós o os ou para pela pelo pois por porque porém porem pra "
    "quando que se sem sobre também tambem um uma umas uns".split()
)

PUNCTUATION = ".,;:!?…\"'()[]«»“”‘’"


def clean_text(text: str) -> str:
    text = " ".join(text.split())
    if text and len(text) < 90 and any(h in text.lower() for h in HALLUCINATIONS):
        log.info("Descartei uma 'alucinação' típica do Whisper")
        return ""
    return text


def join_pieces(pieces) -> str:
    """Junta os textos dos pedaços de áudio sem a maiúscula falsa no meio da frase."""
    out = ""
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if out:
            first = piece.split(" ", 1)[0].rstrip(",.;:!?…")
            if out[-1] not in ".!?…:" and first[:1].isupper() and first.lower() in CONTINUATION_WORDS:
                piece = piece[0].lower() + piece[1:]
            out += " " + piece
        else:
            out = piece
    return out


def common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def bare_word(word: str) -> str:
    return word.strip(PUNCTUATION)
