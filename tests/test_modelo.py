"""Testes com o modelo de voz de verdade (precisa do modelo baixado; leva ~1 minuto).
Os áudios são gerados com a voz em português do próprio Windows. Feche o Soom antes:
a placa de vídeo não comporta duas cópias do modelo ao mesmo tempo.

    python tests/test_modelo.py
"""

import subprocess
import tempfile
import time
from pathlib import Path

import _runner  # noqa: F401 - ajusta o caminho de importação
import numpy as np

from soom import output, win32
from soom.audio import read_wav, trim_silence
from soom.config import SAMPLE_RATE, load_config, setup_logging
from soom.speech import LiveSession, Transcriber
from soom.text import bare_word

AUDIOS = Path(tempfile.gettempdir()) / "soom-testes"
FRASES = {
    "curto": "Oi, tudo bem? Amanhã eu não vou conseguir ir na reunião das dez, mas posso participar à tarde.",
    "medio": (
        "Pessoal, só pra lembrar: o relatório precisa ser entregue até sexta-feira. Quem ainda não mandou a sua "
        "parte, por favor, me envie hoje. Qualquer dúvida, é só me chamar."
    ),
}
_transcriber = None


def _audio(name: str) -> np.ndarray:
    """Gera (uma vez) o áudio com a voz pt-BR do Windows."""
    path = AUDIOS / f"{name}.wav"
    if not path.exists():
        AUDIOS.mkdir(exist_ok=True)
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 'Sixteen', 'Mono');"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'pt-BR' } | Select-Object -First 1;"
            "if (-not $v) { exit 3 }; $s.SelectVoice($v.VoiceInfo.Name);"
            f"$s.SetOutputToWaveFile('{path}', $f); $s.Speak($env:FRASE); $s.Dispose()"
        )
        env = {**__import__("os").environ, "FRASE": FRASES[name]}
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script], env=env, check=False)
        assert result.returncode == 0, "não há voz pt-BR instalada no Windows para gerar o áudio de teste"
    return read_wav(path)


def _model() -> Transcriber:
    global _transcriber  # noqa: PLW0603
    if _transcriber is None:
        setup_logging()
        _transcriber = Transcriber(load_config())
        _transcriber.load()
        assert _transcriber.model is not None, _transcriber.error
        print(f"      modelo em {_transcriber.device}")
    return _transcriber


def test_transcreve_portugues_com_pontuacao():
    tr = _model()
    for name, keys in (("curto", ("reunião", "tarde")), ("medio", ("relatório", "sexta-feira", "dúvida"))):
        audio = _audio(name)
        t0 = time.perf_counter()
        text = tr.transcribe(audio)
        print(f"      [{name}] {len(audio) / SAMPLE_RATE:.0f} s -> {time.perf_counter() - t0:.2f} s: {text}")
        assert all(k in text for k in keys) and text.rstrip()[-1] in ".!?"


def test_silencio_nao_vira_texto():
    noise = (np.random.default_rng(1).standard_normal(3 * SAMPLE_RATE) * 0.001).astype(np.float32)
    assert _model().transcribe(noise) == ""


def test_previa_ao_vivo_no_campo():
    """Toca o áudio em 'tempo real' para a prévia, digitando num campo simulado."""
    tr = _model()
    field: list[str] = []

    def fake_type(text, backspaces=0):
        del field[len(field) - backspaces :]
        field.extend(text)

    win32.type_text = fake_type
    win32.foreground_window = lambda: 1
    win32.modifiers_down = lambda: False
    audio = np.concatenate([trim_silence(_audio("medio")), np.zeros(SAMPLE_RATE, np.float32)])

    class FakeRecorder:
        generation = 1
        t0 = time.monotonic()

        def audio16(self, start=0, generation=None):
            n = int((time.monotonic() - self.t0) * SAMPLE_RATE)
            return audio[start : min(n, len(audio))].copy()

    typer = output.LiveTyper()
    session = LiveSession(tr, FakeRecorder(), 1, on_update=typer.update)
    session.start()
    first_text = None
    while time.monotonic() - FakeRecorder.t0 < len(audio) / SAMPLE_RATE:
        if field and first_text is None:
            first_text = time.monotonic() - FakeRecorder.t0
        time.sleep(0.05)
    t0 = time.perf_counter()
    final = session.finalize(audio) + " "
    assert typer.finish(final)
    print(f"      1º texto no campo em {first_text:.1f} s; final em {time.perf_counter() - t0:.2f} s depois de parar")
    words = lambda s: [bare_word(w) for w in s.split()]  # noqa: E731
    assert words("".join(field)) == words(final)


if __name__ == "__main__":
    raise SystemExit(_runner.run(globals()))
