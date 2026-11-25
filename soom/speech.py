"""O modelo de voz (Whisper large-v3-turbo via ONNX Runtime) e a prévia ao vivo."""

from __future__ import annotations

import math
import threading
import time

import numpy as np

from .audio import Recorder, find_quiet_cut, has_voice, split_long_audio, trim_silence, voice_peak
from .config import SAMPLE_RATE, log
from .text import clean_text, join_pieces

MIN_SPEECH = int(0.3 * SAMPLE_RATE)


class Transcriber:
    """Carrega o modelo (GPU via DirectML; se não der, CPU) e transforma áudio em texto."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.model = None
        self.device = ""
        self.error: str | None = None
        self.ready = threading.Event()
        self._lock = threading.Lock()  # prévia e texto final dividem o mesmo modelo/GPU

    def load(self) -> None:
        try:
            self._load()
        except Exception as e:  # noqa: BLE001
            log.exception("Falha ao carregar o modelo")
            self.error = str(e)
        finally:
            self.ready.set()

    def _load(self) -> None:
        import onnx_asr
        import onnxruntime as ort

        available = ort.get_available_providers()
        log.info("ONNX Runtime %s; aceleradores: %s", ort.__version__, ", ".join(available))
        attempts = []
        if self.cfg["usar_gpu"] and "DmlExecutionProvider" in available:
            attempts.append(("GPU (DirectML)", ["DmlExecutionProvider", "CPUExecutionProvider"]))
        attempts.append(("CPU", ["CPUExecutionProvider"]))
        last_error = None
        for label, providers in attempts:
            try:
                t0 = time.perf_counter()
                options = ort.SessionOptions()
                options.log_severity_level = 3
                if providers[0] == "DmlExecutionProvider":  # exigências do DirectML
                    options.enable_mem_pattern = False
                    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                model = onnx_asr.load_model(
                    self.cfg["modelo"], quantization=self.cfg["quantizacao"] or None, providers=providers, sess_options=options
                )
                t1 = time.perf_counter()
                warm = (0.01 * np.sin(np.linspace(0, 2000 * math.pi, SAMPLE_RATE))).astype(np.float32)
                model.recognize(warm, sample_rate=SAMPLE_RATE, language=self.cfg["idioma"] or None)
                self.model, self.device = model, label
                log.info("Modelo pronto em %s (carga %.1fs, aquecimento %.1fs)", label, t1 - t0, time.perf_counter() - t1)
                return
            except Exception as e:  # noqa: BLE001
                last_error = e
                log.exception("Não funcionou em %s", label)
        raise RuntimeError(f"Não foi possível carregar o modelo: {last_error}")

    def transcribe(self, audio: np.ndarray, *, quiet: bool = False) -> str:
        audio = trim_silence(audio)
        if audio.size < MIN_SPEECH:
            if not quiet:
                log.info("Nenhuma fala detectada")
            return ""
        texts = []
        for piece in split_long_audio(audio):
            if piece.size < MIN_SPEECH:
                continue
            with self._lock:
                text = self.model.recognize(piece, sample_rate=SAMPLE_RATE, language=self.cfg["idioma"] or None)
            text = clean_text(text)
            if text:
                texts.append(text)
        return join_pieces(texts)


class LiveSession:
    """Transcreve enquanto você fala, para a prévia do texto.

    O trecho ainda em aberto é re-transcrito sem parar (~1 s por revisão). Quando ele passa de
    ~12 s, a parte inicial é "confirmada" numa pausa (trechos curtos = revisões mais rápidas; o
    Whisper lê no máximo 30 s por vez). No final, se nada foi dito depois da última prévia, o texto
    dela é usado direto, sem transcrever de novo.
    """

    COMMIT_AFTER = int(12 * SAMPLE_RATE)
    COMMIT_MIN_S = 6.0
    OVERLAP = int(0.3 * SAMPLE_RATE)  # revisa a fronteira, caso uma palavra tenha sido cortada
    MIN_NEW = int(0.3 * SAMPLE_RATE)

    def __init__(self, transcriber: Transcriber, recorder: Recorder, generation: int, on_update=None):
        self.tr, self.rec, self.gen = transcriber, recorder, generation
        self.on_update = on_update  # recebe o texto "estável" (sem a palavra que pode estar cortada)
        self.committed: list[str] = []
        self.committed_end = 0  # amostras já confirmadas
        self.tail_text = ""
        self.tail_covered = 0  # amostras do trecho em aberto que a prévia já cobre
        self.peak = 0.0
        self.display = ""
        self.stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="previa", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _set_tail(self, text: str, covered: int, speech_open: bool = False) -> None:
        self.tail_text, self.tail_covered = text, covered
        self.display = join_pieces([*self.committed, text])
        if self.on_update:
            stable = self.display
            if speech_open:  # você ainda estava falando: a última palavra pode ter sido cortada
                stable = stable.rsplit(" ", 1)[0] if " " in stable else ""
            try:
                self.on_update(stable)
            except Exception:  # noqa: BLE001
                log.exception("Erro ao escrever a prévia no campo")

    def _run(self) -> None:
        try:
            while not self.stop_event.is_set():
                if self.tr.model is None:  # modelo ainda carregando
                    self.stop_event.wait(0.2)
                    continue
                t0 = time.monotonic()
                audio = self.rec.audio16(self.committed_end, self.gen)
                if self.stop_event.is_set():
                    break
                self.peak = max(self.peak, voice_peak(audio))
                if len(audio) > self.COMMIT_AFTER:
                    cut = find_quiet_cut(audio, SAMPLE_RATE, self.COMMIT_MIN_S, self.COMMIT_AFTER / SAMPLE_RATE)
                    text = self.tr.transcribe(audio[:cut], quiet=True)
                    if text:
                        self.committed.append(text)
                    self.committed_end += cut
                    self.tail_text, self.tail_covered = "", 0
                    continue
                if len(audio) - self.tail_covered >= self.MIN_NEW:
                    if has_voice(audio[max(0, self.tail_covered - self.OVERLAP) :], self.peak):
                        text = self.tr.transcribe(audio, quiet=True)
                        self._set_tail(text, len(audio), speech_open=has_voice(audio[-self.OVERLAP :], self.peak))
                    else:  # só silêncio novo: a prévia continua valendo
                        self._set_tail(self.tail_text, len(audio))
                self.stop_event.wait(max(0.0, 0.25 - (time.monotonic() - t0)))
        except Exception:  # noqa: BLE001
            log.exception("Erro na prévia ao vivo")

    def finalize(self, audio: np.ndarray) -> str:
        """Texto final da gravação inteira (`audio` é o que o Recorder devolveu ao parar)."""
        self.stop_event.set()
        self._thread.join(timeout=60)
        tail_audio = audio[self.committed_end :]
        peak = max(self.peak, voice_peak(tail_audio))
        covered = min(self.tail_covered, len(tail_audio))
        if covered and not has_voice(tail_audio[max(0, covered - self.OVERLAP) :], peak):
            tail = self.tail_text
            log.info("Texto final = última prévia (nada novo depois dela)")
        else:
            tail = self.tr.transcribe(tail_audio)
        return join_pieces([*self.committed, tail])
