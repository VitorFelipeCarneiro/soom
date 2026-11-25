"""Testes da lógica (rápidos, sem modelo de voz e sem mexer no teclado/tela de verdade).

    python tests/test_logica.py
"""

import ctypes

import _runner  # noqa: F401 - ajusta o caminho de importação
import numpy as np

from soom import hotkeys, output, win32
from soom.audio import split_long_audio, trim_silence
from soom.config import SAMPLE_RATE
from soom.text import join_pieces

# ---------------------------------------------------------------- simulações
pressed: set[int] = set()
field: list[str] = []
stats = {"backspaces": 0}
fg = [100]


def fake_type(text, backspaces=0):
    stats["backspaces"] += backspaces
    for _ in range(backspaces):
        if field:
            field.pop()
    field.extend(text)


win32.key_is_down = lambda vk: vk in pressed
win32.modifiers_down = lambda: any(v in pressed for vks in win32.MOD_VKS.values() for v in vks)
win32.send_keys = lambda events: None
win32.type_text = fake_type
win32.foreground_window = lambda: fg[0]
win32.user32.CallNextHookEx = lambda *a: 0

H, LWIN, LCTRL, A_KEY, ENTER = 0x48, 0x5B, 0xA2, 0x41, 0x0D


def make_hook():
    events = {"toggle": 0, "cancel": 0, "enter": 0, "activity": []}
    mods, vk = hotkeys.parse_hotkey("win+h")
    hook = hotkeys.InputHook(
        mods, vk, None,
        on_toggle=lambda: events.__setitem__("toggle", events["toggle"] + 1),
        on_cancel=lambda: events.__setitem__("cancel", events["cancel"] + 1),
        on_activity=events["activity"].append,
        on_enter=lambda: events.__setitem__("enter", events["enter"] + 1),
    )  # fmt: skip
    return hook, events


def key(hook, vk, down=True, injected=False):
    s = win32.KBDLLHOOKSTRUCT(vk, 0, win32.LLKHF_INJECTED if injected else 0, 0, 0)
    return hook._kb_proc(win32.HC_ACTION, win32.WM_KEYDOWN if down else win32.WM_KEYUP, ctypes.addressof(s))


def click(hook, x=0, y=0):
    s = win32.MSLLHOOKSTRUCT(win32.wt.POINT(x, y), 0, 0, 0, 0)
    return hook._mouse_proc(win32.HC_ACTION, win32.WM_LBUTTONDOWN, ctypes.addressof(s))


def new_typer():
    field.clear()
    stats["backspaces"] = 0
    fg[0] = 100
    return output.LiveTyper()


def typed(t, text):
    t.update(text)
    t.wait_idle()


# ---------------------------------------------------------------- atalho
def test_parser_de_atalhos():
    assert hotkeys.parse_hotkey("ctrl+alt+space") == (frozenset({"ctrl", "alt"}), 0x20)
    assert hotkeys.parse_hotkey("F9") == (frozenset(), 0x78)
    assert hotkeys.hotkey_label("ctrl+alt+space") == "Ctrl+Alt+Espaço"
    try:
        hotkeys.parse_hotkey("win+banana")
        raise AssertionError("deveria recusar")
    except ValueError:
        pass


def test_h_sozinho_passa_direto():
    hook, ev = make_hook()
    assert key(hook, H) == 0 and key(hook, H, down=False) == 0 and ev["toggle"] == 0


def test_win_h_dispara_uma_vez_e_e_engolido():
    hook, ev = make_hook()
    pressed.add(LWIN)
    results = [key(hook, H), key(hook, H), key(hook, H, down=False)]  # inclui auto-repetição
    pressed.clear()
    assert results == [1, 1, 1] and ev["toggle"] == 1


def test_ctrl_win_h_nao_dispara():
    hook, ev = make_hook()
    pressed.update({LWIN, LCTRL})
    r = key(hook, H)
    pressed.clear()
    assert r == 0 and ev["toggle"] == 0


def test_eventos_injetados_sao_ignorados():
    hook, ev = make_hook()
    pressed.add(LWIN)
    r = key(hook, H, injected=True)
    pressed.clear()
    assert r == 0 and ev["toggle"] == 0


def test_esc_so_cancela_durante_a_gravacao():
    hook, ev = make_hook()
    assert key(hook, win32.VK_ESCAPE) == 0
    key(hook, win32.VK_ESCAPE, down=False)
    hook.esc_active = True
    assert key(hook, win32.VK_ESCAPE) == 1
    hook.esc_active = False
    assert key(hook, win32.VK_ESCAPE, down=False) == 1 and ev["cancel"] == 1


def test_vigilancia_de_cliques_e_teclas():
    hook, ev = make_hook()
    key(hook, A_KEY)
    click(hook)
    assert ev["activity"] == []  # fora do ditado: nada
    hook.watching = True
    key(hook, LCTRL)
    pressed.add(LWIN)
    key(hook, H)
    key(hook, H, down=False)
    pressed.clear()
    assert ev["activity"] == []  # atalho e modificadores não contam
    key(hook, A_KEY)
    click(hook)
    assert ev["activity"] == ["teclado", "mouse"]


def test_enter_no_ditado_termina_e_envia():
    hook, ev = make_hook()
    hook.watching = True
    assert key(hook, ENTER) == 1 and key(hook, ENTER, down=False) == 1 and ev["enter"] == 1
    pressed.add(LCTRL)
    key(hook, ENTER)  # Ctrl+Enter conta como digitação
    pressed.clear()
    assert ev["enter"] == 1 and ev["activity"][-1] == "teclado"
    hook.watching = False
    assert key(hook, ENTER) == 0  # fora do ditado o Enter passa direto


def test_clique_no_indicador_nao_interrompe():
    hook, ev = make_hook()
    hook.watching = True
    hook.ignore_rect = (-10, -10, 10, 10)
    click(hook, 0, 0)
    assert ev["activity"] == []
    click(hook, 50, 50)
    assert ev["activity"] == ["mouse"]


def test_atalho_vai_e_volta_do_config():
    for text in ("win+h", "ctrl+alt+space", "f9", "win+shift+d", "ctrl+num5", "ctrl+vk186"):
        mods, vk = hotkeys.parse_hotkey(text)
        assert hotkeys.hotkey_to_text(mods, vk) == text
    assert hotkeys.describe_hotkey(*hotkeys.parse_hotkey("alt+ctrl+space")) == "Ctrl+Alt+Espaço"
    assert hotkeys.describe_hotkey(*hotkeys.parse_hotkey("win+h")) == "Win+H"


def test_atalhos_ruins_sao_recusados():
    p = hotkeys.parse_hotkey
    assert hotkeys.validate_hotkey(*p("a")) is not None  # letra sozinha atrapalha a digitação
    assert hotkeys.validate_hotkey(*p("shift+a")) is not None  # Shift+A é só um "A" maiúsculo
    assert hotkeys.validate_hotkey(*p("space")) is not None
    assert hotkeys.validate_hotkey(*p("win+l")) is not None  # reservado pelo Windows
    for ok in ("win+h", "ctrl+alt+space", "f9", "pause", "ctrl+shift+d"):
        assert hotkeys.validate_hotkey(*p(ok)) is None, ok


def test_config_do_atalho():
    r = hotkeys.resolve_shortcut
    assert r({"atalho": "ctrl+alt+space", "botao_mouse": None})[1:] == (0x20, None, "Ctrl+Alt+Espaço")
    assert r({"atalho": "", "botao_mouse": "lateral1"})[1:] == (None, "lateral1", "Botão lateral 1 do mouse")
    assert r({"atalho": "banana+", "botao_mouse": None})[3] == "Win+H"  # inválido: volta ao padrão
    assert r({"atalho": "", "botao_mouse": None})[3] == "Win+H"


def test_captura_de_um_atalho_novo():
    hook, ev = make_hook()
    got = []
    hook.start_capture(got.append)
    pressed.update({LCTRL, 0xA4})  # Ctrl + Alt apertados (eles passam para o Windows)
    assert key(hook, LCTRL) == 0
    assert key(hook, 0x20) == 1 and key(hook, 0x20, down=False) == 1  # Espaço: capturado e engolido
    pressed.clear()
    pressed.add(LWIN)
    assert key(hook, H) == 1  # nem o Win+H atual dispara o ditado durante a captura
    key(hook, H, down=False)
    pressed.clear()
    hook.start_capture(got.append)
    s = win32.MSLLHOOKSTRUCT(win32.wt.POINT(0, 0), 2 << 16, 0, 0, 0)  # botão lateral 2
    assert hook._mouse_proc(win32.HC_ACTION, win32.WM_XBUTTONDOWN, ctypes.addressof(s)) == 1
    hook.stop_capture()
    assert got == [("teclado", frozenset({"ctrl", "alt"}), 0x20), ("teclado", frozenset({"win"}), H), ("mouse", "lateral2")]
    assert ev["toggle"] == 0
    pressed.add(LWIN)
    assert key(hook, H) == 1 and ev["toggle"] == 1  # fora da captura, o atalho volta a funcionar
    pressed.clear()
    key(hook, H, down=False)


def test_trocar_o_atalho_na_hora():
    hook, ev = make_hook()
    hook.set_hotkey(*hotkeys.parse_hotkey("ctrl+alt+space"), None)
    pressed.add(LWIN)
    assert key(hook, H) == 0  # o antigo não dispara mais
    pressed.clear()
    pressed.update({LCTRL, 0xA4})
    assert key(hook, 0x20) == 1 and ev["toggle"] == 1
    pressed.clear()
    key(hook, 0x20, down=False)


# ---------------------------------------------------------------- texto no campo
def test_campo_acompanha_a_previa_e_o_final():
    t = new_typer()
    for s in ("o pessoal só", "Pessoal, só para lembrar", "Pessoal, só para lembrar, o relatório precisa ser"):
        typed(t, s)
    assert "".join(field) == "Pessoal, só para lembrar, o relatório precisa ser"
    final = "Pessoal, só para lembrar, o relatório precisa ser entregue até sexta-feira. "
    assert t.finish(final) and "".join(field) == final


def test_troca_so_a_palavra_final():
    t = new_typer()
    typed(t, "Qualquer dúvida, é só me enviar")
    stats["backspaces"] = 0
    typed(t, "Qualquer dúvida, é só me chamar")
    assert "".join(field).endswith("me chamar") and stats["backspaces"] == 6


def test_pontuacao_antiga_nao_reescreve_a_frase():
    t = new_typer()
    typed(t, "Bom dia a todos. Eu queria aproveitar esse")
    stats["backspaces"] = 0
    typed(t, "Bom dia a todos! Eu queria aproveitar esse e-mail")
    assert "".join(field) == "Bom dia a todos. Eu queria aproveitar esse e-mail" and stats["backspaces"] == 0
    typed(t, "Bom dia a todos! Eu queria aproveitar esse e-mail para")
    typed(t, "Bom dia a todos! Eu queria aproveitar esse e-mail, para fazer")
    assert "".join(field) == "Bom dia a todos. Eu queria aproveitar esse e-mail, para fazer"  # recente: corrige
    typed(t, "Bom dia a todos! Eu queria aproveitar este e-mail, para fazer")
    assert "".join(field) == "Bom dia a todos. Eu queria aproveitar este e-mail, para fazer"  # palavra trocada: corrige


def test_cancelar_apaga_o_que_foi_escrito():
    t = new_typer()
    typed(t, "isto vai sumir")
    t.erase()
    assert "".join(field) == "" and t.detached


def test_outra_janela_para_e_nao_apaga_nada():
    t = new_typer()
    field.extend("texto do usuário ")
    typed(t, "Oi, tudo")
    fg[0] = 200
    assert not t.update("Oi, tudo bem?") and not t.finish("Oi, tudo bem? ")
    assert "".join(field) == "texto do usuário Oi, tudo"


def test_sem_previa_o_final_vai_para_a_janela_atual():
    t = new_typer()
    fg[0] = 300
    assert t.finish("Olá. ") and "".join(field) == "Olá. "


# ---------------------------------------------------------------- áudio e texto
def test_corta_silencio_e_divide_falas_longas():
    rng = np.random.default_rng(0)
    speech = lambda s: (0.1 * rng.standard_normal(int(s * SAMPLE_RATE))).astype(np.float32)  # noqa: E731
    quiet = lambda s: np.zeros(int(s * SAMPLE_RATE), np.float32)  # noqa: E731
    audio = np.concatenate([quiet(2), speech(20), quiet(0.6), speech(20), quiet(0.6), speech(12), quiet(3)])
    trimmed = trim_silence(audio)
    assert abs(len(trimmed) / SAMPLE_RATE - 54.0) < 0.2
    chunks = split_long_audio(trimmed)
    assert len(chunks) == 3 and all(len(c) / SAMPLE_RATE <= 28.0 for c in chunks)
    assert trim_silence(quiet(3)).size == 0


def test_junta_pedacos_sem_maiuscula_falsa():
    assert join_pieces(["o mês que vem,", "Porque a equipe", "Maria chegou."]) == "o mês que vem, porque a equipe Maria chegou."
    assert join_pieces(["Fim da frase.", "Porque sim"]) == "Fim da frase. Porque sim"


if __name__ == "__main__":
    raise SystemExit(_runner.run(globals()))
