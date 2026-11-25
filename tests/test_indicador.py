"""Testes do indicador: velocidade do desenho, a janela real (escondida, nada aparece na tela) e
o arrastar com o mouse (simulado).

    python tests/test_indicador.py
"""

import math
import time

import _runner  # noqa: F401 - ajusta o caminho de importação
import numpy as np

from soom import config, overlay, win32

win32.set_dpi_awareness()
config.save_config = overlay.save_config = lambda cfg: None  # não mexe no config.json de verdade
LABELS = ("Ouvindo", "Win+H para concluir · Esc cancela")


def _avg_ms(fn, n=60):
    fn()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) / n * 1000


def test_quadro_rapido_e_bem_formado():
    r = overlay.GlassRenderer(1.0)
    w, h = r.size_for(*LABELS, None)
    W, Hh = w + 2 * r.M, h + 2 * r.M
    glass = np.full((Hh, W, 3), 0.4, np.float32)
    i = [0]

    def frame():
        i[0] += 1
        return r.render(w, h, t=i[0] / 60, phase=i[0] / 600, level=0.5, intensity=0.7, mode="ouvindo",
                        glass=(glass, glass), glass_key=1, header=LABELS)  # fmt: skip

    ms = _avg_ms(frame)
    out = frame()
    print(f"      {ms:.2f} ms por quadro (cápsula {W}x{Hh})")
    assert out.shape == (Hh, W, 4) and out.dtype == np.uint8
    assert (out[..., :3] <= out[..., 3:4]).all()  # cor pré-multiplicada válida
    assert ms < 8, "quadro lento demais (um quadro a 60 por segundo tem 16 ms)"


def test_mudar_de_largura_e_rapido():
    """A cápsula muda de largura (ex.: Ouvindo -> Finalizando) sem engasgar."""
    r = overlay.GlassRenderer(1.0)
    w, h = r.size_for(*LABELS, None)
    r.render(w, h, t=0, phase=0, level=0, intensity=0.5, mode="ouvindo", header=LABELS)
    worst = 0.0
    for dw in range(2, 120, 2):  # uma largura nova a cada quadro
        t0 = time.perf_counter()
        r.render(w - dw, h, t=0, phase=0, level=0, intensity=0.5, mode="ouvindo", header=LABELS)
        worst = max(worst, (time.perf_counter() - t0) * 1000)
    print(f"      pior quadro mudando de largura: {worst:.2f} ms")
    assert worst < 25, "mudar de largura deveria caber em pouco mais de um quadro"


def test_recorte_do_molde_igual_ao_desenho_direto():
    r = overlay.GlassRenderer(1.0)
    w = 300
    cut = r._shape(w, r.H)
    direct = r._finish_shape(w, r.H, *r._draw_layers(w, r.H))
    assert np.abs(cut.glassy.A - direct.glassy.A).max() < 0.02
    assert np.abs(cut.highlight - direct.highlight).max() < 0.02


class FakeRecorder:
    level = 0.0


class FakeApp:
    quitting = False
    hotkey_text = "Win+H"
    recorder = FakeRecorder()

    def __init__(self):
        self.cfg = {"esc_cancela": True, "posicao_indicador": None}
        self.state = None

    def overlay_state(self):
        return self.state

    def preview_text(self):
        return None


def _overlay():
    shown = []
    win32.user32.ShowWindow = lambda hwnd, cmd: shown.append(cmd) or 1  # nunca mostra de verdade
    app = FakeApp()
    return overlay.GlassOverlay(app), app, shown


def test_janela_aceita_os_quadros_e_aparece_some():
    ov, app, shown = _overlay()
    ulw = [0, 0]
    original = win32.user32.UpdateLayeredWindow

    def counting(*args):
        ok = original(*args)
        ulw[0 if ok else 1] += 1
        return ok

    win32.user32.UpdateLayeredWindow = counting
    try:
        times = []
        for i in range(150):
            app.state = "ouvindo" if i < 90 else ("transcrevendo" if i < 110 else None)
            app.recorder.level = 0.03 * (1 + math.sin(i / 5))
            t0 = time.perf_counter()
            ov._tick()
            times.append((time.perf_counter() - t0) * 1000)
            time.sleep(1 / 60)
    finally:
        win32.user32.UpdateLayeredWindow = original
    times.sort()
    print(f"      quadros aceitos: {ulw[0]}, recusados: {ulw[1]} | mediana {times[len(times) // 2]:.2f} ms, "
          f"95% abaixo de {times[int(len(times) * 0.95)]:.2f} ms")  # fmt: skip
    assert ulw[0] > 0 and ulw[1] == 0
    assert shown[:2] == [win32.SW_SHOWNOACTIVATE, win32.SW_HIDE]
    assert ov.errors == 0


def test_arrastar_move_na_hora_salva_e_congela_o_vidro():
    ov, app, _ = _overlay()
    app.state = "ouvindo"
    for _ in range(25):
        ov._tick()
        time.sleep(1 / 60)
    moves = []
    original = win32.user32.SetWindowPos
    win32.user32.SetWindowPos = lambda *a: moves.append(a[2:4]) or 1
    mouse = [ov.cx, ov.bottom - 40]
    win32.cursor_pos = lambda: tuple(mouse)
    try:
        start = (ov.cx, ov.bottom)
        version = ov.glass[3] if ov.glass else 0
        ov._wndproc(ov.hwnd, win32.WM_LBUTTONDOWN, 0, 0)
        for step in range(1, 11):
            mouse[0], mouse[1] = start[0] - 30 * step, start[1] - 40 - 20 * step
            ov._wndproc(ov.hwnd, win32.WM_MOUSEMOVE, 0, 0)
            ov._tick()
        assert len(moves) == 10, "a janela deve andar a cada movimento do mouse"
        assert ov.glass is None or ov.glass[3] == version, "o vidro não deve ser recapturado durante o arrasto"
        ov._wndproc(ov.hwnd, win32.WM_LBUTTONUP, 0, 0)
    finally:
        win32.user32.SetWindowPos = original
    assert (ov.cx, ov.bottom) == (start[0] - 300, start[1] - 200)
    assert app.cfg["posicao_indicador"] == [ov.cx, ov.bottom - ov.r.M]
    ov._place()  # simula reabrir: usa a posição salva
    assert (ov.cx, ov.bottom) == (start[0] - 300, start[1] - 200)
    ov._wndproc(ov.hwnd, win32.WM_LBUTTONDBLCLK, 0, 0)  # duplo clique: volta ao padrão
    ov._tick()
    assert app.cfg["posicao_indicador"] is None
    ov._move_to(-100_000, -100_000)
    vx, vy, _, _ = win32.virtual_screen()
    assert ov.cx >= vx and ov.bottom >= vy  # não sai da tela


if __name__ == "__main__":
    raise SystemExit(_runner.run(globals()))
