"""Microfone, sons de aviso e o tratamento do áudio (silêncio, pausas, divisão em pedaços)."""

from __future__ import annotations

import tempfile
import wave
from pathlib import Path

import numpy as np

from .config import SAMPLE_RATE, log

SILENCE_RMS = 0.003  # volume abaixo disso o tempo todo = ninguém falou
FRAME = int(SAMPLE_RATE * 0.03)  # janelas de 30 ms para medir o volume


def resample(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or audio.size == 0:
        return audio.astype(np.float32, copy=False)
    k = max(1, round(sr_in / sr_out))
    if k > 1:  # filtro passa-baixa simples antes de reduzir a taxa
        audio = np.convolve(audio, np.ones(k, dtype=np.float32) / k, mode="same")
    n_out = round(len(audio) * sr_out / sr_in)
    x_old = np.linspace(0.0, 1.0, len(audio), endpoint=False)
    x_new = np.linspace(0.0, 1.0, n_out, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def frame_rms(audio: np.ndarray, frame: int = FRAME) -> np.ndarray:
    """Volume (RMS) de cada janela de `frame` amostras."""
    k = len(audio) // frame
    if k == 0:
        return np.array([float(np.sqrt(np.mean(audio**2)))]) if audio.size else np.zeros(0)
    f = audio[: k * frame].reshape(k, frame)
    return np.sqrt((f * f).mean(axis=1))


def voice_peak(audio: np.ndarray) -> float:
    rms = frame_rms(audio)
    return float(rms.max()) if rms.size else 0.0


def has_voice(audio: np.ndarray, reference_peak: float = 0.0) -> bool:
    """Tem fala neste trecho? (pelo menos ~150 ms acima do ruído, relativo ao volume da sua voz)"""
    rms = frame_rms(audio)
    if rms.size == 0:
        return False
    thr = max(SILENCE_RMS * 1.5, 0.05 * reference_peak)
    return int((rms > thr).sum()) >= 5


def trim_silence(audio: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Corta o silêncio do começo e do fim. Devolve vazio se não houver fala."""
    frame = int(sr * 0.03)
    rms = frame_rms(audio, frame)
    if rms.size == 0 or rms.max() < SILENCE_RMS:
        return audio[:0]
    voiced = np.nonzero(rms > max(SILENCE_RMS, rms.max() * 0.04))[0]
    pad = int(0.4 * sr)
    return audio[max(0, voiced[0] * frame - pad) : min(len(audio), (voiced[-1] + 1) * frame + pad)]


def find_quiet_cut(audio: np.ndarray, sr: int, min_s: float, max_s: float) -> int:
    """Posição (em amostras) da pausa mais silenciosa entre min_s e max_s segundos."""
    frame = int(sr * 0.03)
    rms = frame_rms(audio[int(min_s * sr) : int(max_s * sr)], frame)
    w = 10  # ~300 ms
    if len(rms) > w:
        i = int(np.argmin(np.convolve(rms, np.ones(w) / w, mode="valid"))) + w // 2
    else:
        i = int(np.argmin(rms)) if len(rms) else 0
    return int(min_s * sr) + i * frame


def split_long_audio(audio: np.ndarray, sr: int = SAMPLE_RATE, max_s: float = 28.0, min_s: float = 15.0):
    """O Whisper lê no máximo 30 s por vez: divide falas longas nas pausas mais silenciosas."""
    chunks, pos = [], 0
    while len(audio) - pos > int(max_s * sr):
        cut = pos + find_quiet_cut(audio[pos:], sr, min_s, max_s)
        chunks.append(audio[pos:cut])
        pos = cut
    chunks.append(audio[pos:])
    return chunks


def read_wav(path: str | Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("use WAV PCM 16 bits")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        audio = audio.reshape(-1, ch).mean(axis=1)
    return resample(audio, sr, SAMPLE_RATE)


class Recorder:
    """Grava o microfone em pedaços; dá para ler o que já foi gravado sem parar (para a prévia)."""

    SKIP = int(0.12 * SAMPLE_RATE)  # descarta o "bip" de início

    def __init__(self, device=None):
        self.device = device
        self.level = 0.0  # volume atual (para a animação)
        self.generation = 0  # muda a cada gravação (a prévia de uma não lê a outra)
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self._sr = SAMPLE_RATE

    def start(self) -> int:
        import sounddevice as sd

        self._chunks, self.level = [], 0.0
        self.generation += 1
        try:
            self._sr = SAMPLE_RATE
            stream = sd.InputStream(device=self.device, samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=self._callback)
        except Exception:  # noqa: BLE001 - microfone não aceita 16 kHz: grava na taxa dele e converte depois
            self._sr = int(sd.query_devices(self.device, kind="input")["default_samplerate"])
            stream = sd.InputStream(device=self.device, samplerate=self._sr, channels=1, dtype="float32", callback=self._callback)
        stream.start()
        self._stream = stream
        return self.generation

    def _callback(self, indata, frames, time_info, status) -> None:
        mono = indata[:, 0].copy()
        self._chunks.append(mono)
        self.level = float(np.sqrt(np.dot(mono, mono) / mono.size)) if mono.size else 0.0

    def audio16(self, start: int = 0, generation: int | None = None) -> np.ndarray:
        """Áudio gravado até agora (16 kHz) a partir da amostra `start`, sem parar a gravação."""
        if generation is not None and generation != self.generation:
            return np.zeros(0, np.float32)
        chunks = list(self._chunks)  # cópia rasa: o microfone continua acrescentando na original
        if not chunks:
            return np.zeros(0, np.float32)
        if self._sr != SAMPLE_RATE:
            return resample(np.concatenate(chunks), self._sr, SAMPLE_RATE)[self.SKIP + start :]
        start += self.SKIP
        pos = 0
        for i, chunk in enumerate(chunks):
            if pos + len(chunk) > start:
                return np.concatenate(chunks[i:])[start - pos :]
            pos += len(chunk)
        return np.zeros(0, np.float32)

    def stop(self) -> np.ndarray:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                log.exception("Erro ao fechar o microfone")
        audio = self.audio16()
        self._chunks, self.level = [], 0.0
        return audio


def _write_tone(path: Path, notes: list[tuple[float, float]], volume: float = 0.22) -> None:
    sr = 44_100
    parts = []
    for freq, dur in notes:
        t = np.arange(int(sr * dur)) / sr
        env = np.ones_like(t)
        fade = min(len(t) // 4, int(sr * 0.008))
        env[:fade] = np.linspace(0, 1, fade)
        env[-fade:] = np.linspace(1, 0, fade)
        parts.append(np.sin(2 * np.pi * freq * t) * env)
    data = (np.concatenate(parts) * volume * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


class Sounds:
    """Bipes curtos de início, fim, "nada reconhecido" e erro."""

    SPECS = {
        "inicio": [(660, 0.06), (990, 0.08)],
        "fim": [(990, 0.06), (660, 0.08)],
        "nada": [(520, 0.09)],
        "erro": [(240, 0.22)],
    }

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.files: dict[str, Path] = {}
        if enabled:
            folder = Path(tempfile.gettempdir()) / "soom-sons"
            folder.mkdir(exist_ok=True)
            for name, notes in self.SPECS.items():
                path = folder / f"{name}.wav"
                _write_tone(path, notes)
                self.files[name] = path

    def play(self, name: str) -> None:
        if not self.enabled:
            return
        import winsound

        try:
            winsound.PlaySound(str(self.files[name]), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except RuntimeError:
            log.warning("Não consegui tocar o som '%s'", name)
