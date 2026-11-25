"""O maestro: liga o atalho, o microfone, o modelo, a prévia, o indicador e o ícone."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time

from . import startup, win32
from .audio import Recorder, Sounds
from .config import APP_DIR, APP_NAME, CONFIG_PATH, LAUNCHER, PREVIEW_MODES, SAMPLE_RATE, load_config, log, save_config, setup_logging
from .hotkeys import InputHook, resolve_shortcut
from .output import LiveTyper, insert_text
from .overlay import GlassOverlay
from .speech import LiveSession, Transcriber
from .tray import Tray
from .win32 import VK_RETURN

ENTER = [(VK_RETURN, False), (VK_RETURN, True)]


class App:
    """Estados: carregando -> pronto <-> ouvindo -> transcrevendo -> pronto.

    Comandos (do atalho, do Esc, do Enter, de cliques...) chegam numa fila e são tratados um de
    cada vez pela thread de controle; a transcrição final roda em outra thread, para o atalho
    continuar respondendo.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        mods, vk, mouse, self.hotkey_text = resolve_shortcut(cfg)
        self.recorder = Recorder(cfg["microfone"])
        self.transcriber = Transcriber(cfg)
        self.sounds = Sounds(bool(cfg["sons"]))
        self.cmds: queue.Queue = queue.Queue()
        self.jobs: queue.Queue = queue.Queue()
        self.recording = False
        self.rec_started = 0.0
        self.session: LiveSession | None = None  # prévia da gravação atual
        self.finishing: LiveSession | None = None  # prévia da gravação sendo finalizada
        self.typer: LiveTyper | None = None  # escreve a prévia no campo (modo "no_campo")
        self.pending = 0
        self.quitting = False
        self.mutex = None
        self.overlay: GlassOverlay | None = None
        self.shortcut_window = None
        self._lock = threading.Lock()
        self.tray = Tray(self)
        self.hook = InputHook(
            mods,
            vk,
            mouse,
            on_toggle=lambda: self.cmds.put(("toggle",)),
            on_cancel=lambda: self.cmds.put(("cancel",)),
            on_activity=lambda kind: self.cmds.put(("activity", kind)),
            on_enter=lambda: self.cmds.put(("enter",)),
        )

    # ---- estado
    def preview_mode(self) -> str:
        mode = str(self.cfg.get("previa") or "desligada").lower()
        return mode if mode in PREVIEW_MODES else "no_campo"

    def state(self) -> str:
        if self.recording:
            return "ouvindo"
        if self.pending:
            return "transcrevendo"
        if self.transcriber.error:
            return "erro"
        if not self.transcriber.ready.is_set():
            return "carregando"
        return "pronto"

    def status_text(self) -> str:
        return {
            "pronto": f"pronto ({self.transcriber.device})",
            "erro": "erro ao carregar o modelo (veja o soom.log)",
            "carregando": "carregando o modelo...",
            "ouvindo": "ouvindo...",
            "transcrevendo": "transcrevendo...",
        }[self.state()]

    def overlay_state(self) -> str | None:
        if not self.cfg["mostrar_indicador"]:
            return None
        if self.recording:
            return "ouvindo"
        if self.pending:
            return "transcrevendo" if self.transcriber.model is not None else "carregando"
        return None

    def preview_text(self) -> str | None:
        """Texto da prévia para a caixinha (None = a caixinha não mostra texto)."""
        if self.preview_mode() != "na_caixinha":
            return None
        session = self.session if self.recording else self.finishing
        return session.display if session else ""

    def refresh(self) -> None:
        self.tray.update(self.state(), self.status_text())

    # ---- ciclo de vida
    def start(self) -> None:
        startup.sync(bool(self.cfg["iniciar_com_windows"]))
        self.tray.run()
        self.hook.start()
        for target, name in ((self._load_model, "modelo"), (self._control_loop, "controle"), (self._job_loop, "transcricao")):
            threading.Thread(target=target, name=name, daemon=True).start()
        self.refresh()

    def _load_model(self) -> None:
        self.transcriber.load()
        self.refresh()
        if self.transcriber.model is not None:
            self.tray.notify(f"Pronto! Aperte {self.hotkey_text} para ditar ({self.transcriber.device}).")
        else:
            self.sounds.play("erro")
            self.tray.notify("Não consegui carregar o modelo de voz. Veja o arquivo soom.log.")

    # ---- comandos
    def _control_loop(self) -> None:
        while True:
            cmd = self.cmds.get()
            try:
                self._handle(cmd)
            except Exception:  # noqa: BLE001
                log.exception("Erro no controle")
            self.refresh()

    def _handle(self, cmd: tuple) -> None:
        kind = cmd[0]
        if kind == "toggle":
            self._stop_recording() if self.recording else self._start_recording()
        elif kind == "cancel" and self.recording:
            self._cancel_recording()
        elif kind == "timeout" and self.recording and cmd[1] == self.rec_started:
            log.info("Tempo máximo atingido; encerrando a gravação")
            self._stop_recording()
        elif kind == "enter":
            if self.recording and self.typer:
                log.info("Enter durante o ditado: termina, completa o texto e envia")
                self._stop_recording(then_enter=True)
            else:  # o Enter não era para nós (o ditado já tinha acabado): devolve ao app
                win32.send_keys(ENTER)
        elif kind == "activity" and self.recording and self.typer and self.typer.typed:
            # Como no Win+H: clicou ou digitou no meio do ditado, ele para (nunca escreve fora do lugar)
            log.info("Usuário %s durante o ditado: parando", "clicou" if cmd[1] == "mouse" else f"apertou {cmd[1]}")
            self.typer.detach()
            self._stop_recording()
        elif kind == "detached" and self.recording and cmd[1] is self.typer:
            log.info("A janela mudou durante o ditado: parando")
            self._stop_recording()

    def _start_recording(self) -> None:
        try:
            generation = self.recorder.start()
        except Exception as e:  # noqa: BLE001
            log.exception("Microfone indisponível")
            self.sounds.play("erro")
            self.tray.notify(f"Não consegui abrir o microfone: {e}")
            return
        mode = self.preview_mode()
        self.typer = LiveTyper() if mode == "no_campo" else None
        if mode != "desligada":
            self.session = LiveSession(self.transcriber, self.recorder, generation, self._typer_callback(self.typer))
            self.session.start()
        if self.typer:
            self.hook.set_watch(True)
        self.recording = True
        self.rec_started = time.monotonic()
        self.hook.esc_active = bool(self.cfg["esc_cancela"])
        self.sounds.play("inicio")
        limit = self.cfg["duracao_maxima_segundos"]
        if limit:
            started = self.rec_started
            timer = threading.Timer(float(limit), lambda: self.cmds.put(("timeout", started)))
            timer.daemon = True
            timer.start()
        log.info("Gravando...")

    def _typer_callback(self, typer: LiveTyper | None):
        """Leva cada prévia para o campo; se não der mais (outra janela), avisa o controle uma vez."""
        if typer is None:
            return None

        def on_update(text: str) -> None:
            if not typer.update(text) and not typer.reported:
                typer.reported = True
                self.cmds.put(("detached", typer))

        return on_update

    def _end_recording_state(self) -> None:
        self.recording = False
        self.hook.esc_active = False
        self.hook.set_watch(False)

    def _cancel_recording(self) -> None:
        if self.session:
            self.session.stop()
            self.session = None
        if self.typer:
            self.typer.erase()  # apaga do campo o que a prévia já tinha escrito
            self.typer = None
        self.recorder.stop()
        self._end_recording_state()
        self.sounds.play("nada")
        log.info("Ditado cancelado")

    def _stop_recording(self, then_enter: bool = False) -> None:
        session, self.session = self.session, None
        typer, self.typer = self.typer, None
        if session:
            session.stop()
        audio = self.recorder.stop()
        with self._lock:
            self.pending += 1
            self.finishing = session
        self._end_recording_state()
        self.sounds.play("fim")
        log.info("Gravação encerrada: %.1f s de áudio", len(audio) / SAMPLE_RATE)
        self.jobs.put((audio, session, typer, then_enter))

    # ---- texto final
    def _job_loop(self) -> None:
        while True:
            audio, session, typer, then_enter = self.jobs.get()
            self.finishing = session
            try:
                self.transcriber.ready.wait()
                if self.transcriber.model is None:
                    self.sounds.play("erro")
                    self.tray.notify("O modelo de voz não carregou; não dá para transcrever. Veja o soom.log.")
                    continue
                t0 = time.perf_counter()
                text = session.finalize(audio) if session else self.transcriber.transcribe(audio)
                log.info("Texto final em %.2f s (%d caracteres)", time.perf_counter() - t0, len(text))
                if text and self.cfg["espaco_no_final"] and not then_enter:
                    text += " "
                delivered = self._deliver(text, typer)
                if then_enter and delivered:  # o Enter que você apertou, agora com o texto completo
                    win32.wait_for_modifier_release()
                    win32.send_keys(ENTER)
                if not text:
                    self.sounds.play("nada")
            except Exception:  # noqa: BLE001
                log.exception("Erro na transcrição")
                self.sounds.play("erro")
            finally:
                if typer is not None:
                    typer.close()
                with self._lock:
                    self.pending -= 1
                    if self.finishing is session:
                        self.finishing = None
                self.refresh()

    def _deliver(self, text: str, typer: LiveTyper | None) -> bool:
        """Coloca o texto final no campo. Devolve True se ele ficou no lugar certo."""
        restore = bool(self.cfg["restaurar_area_de_transferencia"])
        if typer is not None and typer.finish(text):  # corrige no campo o que a prévia escreveu
            return True
        if typer is None or not typer.typed:  # nada foi escrito ainda: cola onde você estiver agora
            if text:
                insert_text(text, restore)
            return True
        if text:
            import pyperclip

            pyperclip.copy(text)
            self.tray.notify(
                "Parei de escrever porque você clicou, digitou ou trocou de janela durante o ditado. "
                "O texto completo foi copiado: use Ctrl+V se precisar."
            )
        return False

    # ---- menu do ícone
    def open_shortcut_window(self) -> None:
        """Abre a janela de trocar o atalho (só uma por vez)."""
        from .shortcut import ShortcutWindow

        win = self.shortcut_window
        if win is not None and win.root is not None:
            win.raise_requested = True  # já está aberta: só traz para a frente
            return
        if self.recording:
            self.tray.notify("Termine o ditado antes de trocar o atalho.")
            return
        self.shortcut_window = ShortcutWindow(self)
        threading.Thread(target=self.shortcut_window.show, name="janela-atalho", daemon=True).start()

    def set_shortcut(self, kind: str, value: str) -> None:
        """kind 'teclado' (value = 'ctrl+alt+space'...) ou 'mouse' (value = 'lateral1'...)."""
        if kind == "mouse":
            self.cfg["atalho"], self.cfg["botao_mouse"] = "", value
        else:
            self.cfg["atalho"], self.cfg["botao_mouse"] = value, None
        save_config(self.cfg)
        mods, vk, mouse, self.hotkey_text = resolve_shortcut(self.cfg)
        self.hook.set_hotkey(mods, vk, mouse)
        log.info("Atalho trocado para %s", self.hotkey_text)
        self.tray.refresh_menu()
        self.tray.notify(f"Pronto! Agora o atalho é {self.hotkey_text}.")

    def autostart_enabled(self) -> bool:
        return startup.is_enabled()

    def toggle_autostart(self) -> None:
        on = not startup.is_enabled()
        startup.enable() if on else startup.disable()
        self.cfg["iniciar_com_windows"] = on
        save_config(self.cfg)
        self.tray.refresh_menu()

    def reset_overlay_position(self) -> None:
        if self.overlay is not None:
            self.overlay.reset_requested = True  # o próprio indicador aplica (na thread dele)
        else:
            self.cfg["posicao_indicador"] = None
            save_config(self.cfg)

    def open_config(self) -> None:
        subprocess.Popen(["notepad.exe", str(CONFIG_PATH)])  # noqa: S603, S607

    def open_folder(self) -> None:
        os.startfile(APP_DIR)  # noqa: S606

    def restart(self) -> None:
        if self.mutex:
            win32.kernel32.CloseHandle(self.mutex)
            self.mutex = None
        subprocess.Popen([sys.executable, str(LAUNCHER)], cwd=APP_DIR)  # noqa: S603
        self.quit()

    def quit(self) -> None:
        """Sair: desliga tudo. Com o processo encerrado, o Windows remove os ganchos e o Win+H
        volta a ser o do sistema."""
        log.info("Saindo do Soom")
        if self.recording:
            self.recorder.stop()
        self.quitting = True
        self.tray.stop()


def run_app() -> None:
    setup_logging()
    mutex = win32.single_instance("Local\\Soom-instancia-unica")
    if mutex is None:
        win32.message_box("O Soom já está aberto (ícone perto do relógio).", APP_NAME)
        return
    win32.set_dpi_awareness()
    cfg = load_config()
    log.info("Iniciando %s (atalho %s, modelo %s)", APP_NAME, cfg["atalho"], cfg["modelo"])
    app = App(cfg)
    app.mutex = mutex
    app.start()
    try:
        app.overlay = GlassOverlay(app)
        app.overlay.run()  # a janela do indicador vive na thread principal
    except Exception:  # noqa: BLE001
        log.exception("Indicador visual indisponível; seguindo sem ele")
        while not app.quitting:
            time.sleep(0.3)
    log.info("Encerrado")
    os._exit(0)  # encerra também as threads do microfone e do modelo
