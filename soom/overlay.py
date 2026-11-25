"""O indicador flutuante: cápsula de vidro (o que está atrás, desfocado), luz colorida estilo Siri
que reage à voz e uma esfera de cores fluidas.

Desempenho: o que não muda a cada quadro (vidro, sombra, reflexos, textos) é montado uma vez e
guardado; a cada quadro só são recalculados a luz/borda coloridas (uma faixa fina em volta da
cápsula) e a esfera. A transparência da animação de entrada/saída é aplicada pelo próprio Windows.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import numpy as np

from . import win32
from .config import APP_NAME, log, save_config
from .text import common_prefix_len
from .win32 import (
    AC_SRC_ALPHA,
    BITMAPINFO,
    BITMAPINFOHEADER,
    BLENDFUNCTION,
    CS_DBLCLKS,
    GWL_EXSTYLE,
    IDC_SIZEALL,
    MA_NOACTIVATE,
    PM_REMOVE,
    SW_HIDE,
    SW_SHOWNOACTIVATE,
    SWP_NOACTIVATE,
    SWP_NOSIZE,
    SWP_NOZORDER,
    ULW_ALPHA,
    WDA_EXCLUDEFROMCAPTURE,
    WM_CAPTURECHANGED,
    WM_LBUTTONDBLCLK,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    WM_MOUSEACTIVATE,
    WM_MOUSEMOVE,
    WM_SETCURSOR,
    WNDCLASSEXW,
    WNDPROC,
    WS_EX_LAYERED,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    WS_EX_TOPMOST,
    WS_EX_TRANSPARENT,
    WS_POPUP,
    gdi32,
    kernel32,
    user32,
)

if TYPE_CHECKING:
    from .app import App

# Cores da luz (Siri): azul, índigo, roxo, rosa, laranja, ciano
SIRI_COLORS = [(10, 132, 255), (94, 92, 230), (191, 90, 242), (255, 55, 95), (255, 159, 10), (64, 200, 255)]
CALM_COLORS = [(10, 132, 255), (90, 200, 250), (94, 92, 230), (64, 156, 255)]
ORB_BLOBS = (  # cor (0-1), velocidade x, velocidade y, fase, tamanho
    ((0.04, 0.52, 1.00), 0.9, 1.3, 0.0, 0.42),
    ((0.75, 0.35, 0.95), 1.2, 0.8, 2.1, 0.38),
    ((1.00, 0.22, 0.45), 0.7, 1.1, 4.2, 0.36),
    ((1.00, 0.62, 0.04), 1.5, 0.6, 1.0, 0.30),
)
ORB_BLOBS_CALM = (
    ((0.04, 0.52, 1.00), 0.9, 1.3, 0.0, 0.45),
    ((0.35, 0.80, 1.00), 1.2, 0.8, 2.1, 0.38),
    ((0.37, 0.36, 0.90), 0.7, 1.1, 4.2, 0.36),
)
DARK_TINT = np.array([0.075, 0.075, 0.095], np.float32)
LIGHT_TINT = np.array([0.96, 0.96, 0.975], np.float32)
INK_DARK_GLASS = np.array([1.0, 1.0, 1.0], np.float32)
INK_LIGHT_GLASS = np.array([0.07, 0.07, 0.09], np.float32)


# ---------------------------------------------------------------- utilidades de desenho
def gradient_lut(colors, n: int = 256) -> np.ndarray:
    cols = np.array([*colors, colors[0]], np.float32) / 255
    pos = np.linspace(0, 1, len(cols))
    x = np.linspace(0, 1, n, endpoint=False)
    return np.stack([np.interp(x, pos, cols[:, i]) for i in range(3)], axis=1).astype(np.float32)


def load_font(px: float, variant: str = "Regular"):
    """Segoe UI Variable (Windows 11), a mais parecida com a fonte da Apple.
    `variant`: "Semibold Display", "Regular", "Regular Small"..."""
    from PIL import ImageFont

    folder = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    size = max(8, round(px))
    try:
        font = ImageFont.truetype(str(folder / "SegUIVar.ttf"), size)
        for name in font.get_variation_names():
            label = name.decode() if isinstance(name, bytes) else str(name)
            if label.lower() == variant.lower():
                font.set_variation_by_name(name)
                break
        return font
    except OSError:
        pass
    for name in ("segoeuisb.ttf" if "semibold" in variant.lower() else "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(str(folder / name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def ease_out_cubic(x: float) -> float:
    return 1 - (1 - x) ** 3


def ease_out_back(x: float) -> float:
    c1 = 1.70158
    return 1 + (c1 + 1) * (x - 1) ** 3 + c1 * (x - 1) ** 2


def _blit(P: np.ndarray, A: np.ndarray, sP: np.ndarray, sA: np.ndarray, x: int, y: int) -> None:
    """Põe uma camada pequena (cor pré-multiplicada sP, opacidade sA) por cima, na posição (x, y)."""
    h, w = sA.shape
    H, W = A.shape
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1:
        return
    sp, sa = sP[y0 - y : y1 - y, x0 - x : x1 - x], sA[y0 - y : y1 - y, x0 - x : x1 - x]
    P[y0:y1, x0:x1] = sp + P[y0:y1, x0:x1] * (1 - sa)[..., None]
    A[y0:y1, x0:x1] = sa + A[y0:y1, x0:x1] * (1 - sa)


def _bgra(P: np.ndarray, A: np.ndarray) -> np.ndarray:
    """Cor pré-multiplicada (float 0-1) -> BGRA de 8 bits, o formato das janelas translúcidas."""
    a = np.clip(A, 0.0, 1.0)
    out = np.empty((*a.shape, 4), np.uint8)
    out[..., :3] = (np.minimum(P[..., ::-1], a[..., None]) * 255 + 0.5).astype(np.uint8)
    out[..., 3] = (a * 255 + 0.5).astype(np.uint8)
    return out


def scale_frame(frame: np.ndarray, sc: float) -> np.ndarray:
    """Aumenta/diminui o quadro a partir do centro (animação de entrada/saída)."""
    from PIL import Image

    H, W = frame.shape[:2]
    nw, nh = max(1, round(W * sc)), max(1, round(H * sc))
    img = np.asarray(Image.fromarray(frame, "RGBA").resize((nw, nh), Image.BILINEAR))
    out = np.zeros_like(frame)
    ox, oy = (W - nw) // 2, (H - nh) // 2
    sx0, sy0, dx0, dy0 = max(0, -ox), max(0, -oy), max(0, ox), max(0, oy)
    w, h = min(nw - sx0, W - dx0), min(nh - sy0, H - dy0)
    out[dy0 : dy0 + h, dx0 : dx0 + w] = img[sy0 : sy0 + h, sx0 : sx0 + w]
    return out


def blur_backdrop(img, s: float) -> tuple[np.ndarray, float, np.ndarray]:
    """O que está atrás do indicador, desfocado e com cores mais vivas (a base do vidro).
    Devolve também o brilho médio (para o vidro se adaptar) e uma miniatura (para saber se mudou)."""
    from PIL import Image, ImageFilter

    img = img.convert("RGB")
    w, h = img.size
    small = img.resize((max(1, w // 4), max(1, h // 4)), Image.BOX).filter(ImageFilter.GaussianBlur(4.5 * s))
    thumb = np.asarray(small.resize((32, 8), Image.BOX), np.float32) / 255
    a = np.asarray(small.resize((w, h), Image.BILINEAR), np.float32) / 255
    gray = a.mean(axis=2, keepdims=True)
    return np.clip(gray + (a - gray) * 1.4, 0, 1), float(thumb.mean()), thumb


# ---------------------------------------------------------------- desenho do indicador
class GlassRenderer:
    """Monta cada quadro do indicador (em BGRA), com bordas suaves e tudo em cache possível.

    Três níveis de cache: a forma (depende só do tamanho), a parte parada do quadro (vidro,
    sombra, reflexos e textos) e, a cada quadro, só a faixa colorida em volta e a esfera.
    """

    def __init__(self, s: float):
        self.s = s
        self.H = round(56 * s)  # altura da cápsula
        self.M = round(30 * s)  # margem para o brilho e a sombra
        self.R = round(28 * s)  # raio máximo dos cantos
        self.orb_d = round(34 * s)
        self.pad = round(11 * s)
        self.card_w = round(520 * s)  # largura quando mostra texto (modo caixinha)
        self.line_h = round(21 * s)
        self.max_lines = 3
        self.max_w = self.card_w + 2 * self.M
        self.max_h = self.H + self.max_lines * self.line_h + round(16 * s) + 2 * self.M
        self.font_title = load_font(15 * s, "Semibold Display")
        self.font_sub = load_font(11.5 * s, "Regular Small")
        self.font_text = load_font(14 * s, "Regular")
        self.luts = {"siri": gradient_lut(SIRI_COLORS), "calm": gradient_lut(CALM_COLORS)}
        self._shapes: OrderedDict = OrderedDict()
        self._texts: OrderedDict = OrderedDict()
        self._template = None  # cápsula "molde" bem larga: qualquer largura sai dela por recorte
        self._min_slice_w = round(2 * (self.R + 60 * s))
        # Esfera: a borda é suavizada pela própria fórmula, então basta o tamanho final.
        d = self.orb_d
        g = (np.arange(d, dtype=np.float32) + 0.5) / d * 2 - 1
        self._ox, self._oy = np.meshgrid(g, g)
        self._orb_r = np.sqrt(self._ox**2 + self._oy**2)
        self._orb_shade = (0.70 + 0.30 * np.sqrt(np.clip(1 - self._orb_r**2, 0, 1)))[..., None]  # volume
        self._orb_spec = np.exp(-(((self._ox + 0.30) ** 2) / 0.05 + ((self._oy + 0.42) ** 2) / 0.022)) * 0.42
        self._blob_colors = {
            key: [np.array(b[0], np.float32) for b in blobs] for key, blobs in (("siri", ORB_BLOBS), ("calm", ORB_BLOBS_CALM))
        }

    # ---- medidas
    def header_width(self, title: str, sub: str) -> int:
        tw = max(self.font_title.getlength(title), self.font_sub.getlength(sub) if sub else 0)
        return round(self.pad + self.orb_d + 12 * self.s + tw + 24 * self.s)

    def wrap(self, text: str) -> list[str]:
        width = self.card_w - 40 * self.s
        lines, cur = [], ""
        for word in text.split():
            cand = f"{cur} {word}" if cur else word
            if not cur or self.font_text.getlength(cand) <= width:
                cur = cand
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        if len(lines) > self.max_lines:
            lines = lines[-self.max_lines :]
            lines[0] = "… " + lines[0]
        return lines

    def size_for(self, title: str, sub: str, lines: list[str] | None) -> tuple[int, int]:
        w, h = self.header_width(title, sub), self.H
        if lines:
            w = max(w, self.card_w)
            h = self.H + len(lines) * self.line_h + round(12 * self.s)
        return w, h

    # ---- formas (dependem só do tamanho)
    def _draw_layers(self, w: int, h: int) -> tuple:
        """Desenha do zero as camadas de uma cápsula w x h: corpo, borda, brilho, sombra, reflexos."""
        from PIL import Image, ImageDraw, ImageFilter

        s, M = self.s, self.M
        W, Hh = w + 2 * M, h + 2 * M
        r = min(h / 2, self.R)
        box = (M, M, M + w, M + h)
        k = 3  # desenha em 3x e reduz: bordas suaves
        big_box = [c * k for c in box]

        def smooth(draw) -> np.ndarray:
            big = Image.new("L", (W * k, Hh * k), 0)
            draw(ImageDraw.Draw(big))
            return np.asarray(big.resize((W, Hh), Image.LANCZOS), np.float32) / 255

        body = smooth(lambda d: d.rounded_rectangle(big_box, radius=r * k, fill=255))
        rim = smooth(lambda d: d.rounded_rectangle(big_box, radius=r * k, outline=255, width=max(2, round(1.4 * s * k))))
        band = Image.new("L", (W, Hh), 0)
        ImageDraw.Draw(band).rounded_rectangle(box, radius=r, outline=255, width=max(2, round(5 * s)))
        glow = np.asarray(band.filter(ImageFilter.GaussianBlur(9 * s)), np.float32)
        glow /= max(1.0, float(glow.max()))
        sh = Image.new("L", (W, Hh), 0)
        ImageDraw.Draw(sh).rounded_rectangle((box[0], box[1] + 6 * s, box[2], box[3] + 6 * s), radius=r, fill=255)
        shadow = np.asarray(sh.filter(ImageFilter.GaussianBlur(14 * s)), np.float32) / 255 * 0.40
        yy = np.arange(Hh, dtype=np.float32)[:, None]
        sheen = (np.clip(1 - (yy - M) / (h * 0.55), 0, 1) ** 2 * 0.13) * body  # luz de cima no vidro
        edge = rim * (0.14 + 0.24 * np.clip(1 - (yy - M) / h, 0, 1))  # borda clarinha (efeito de lente)
        return body, rim, glow, shadow, np.maximum(sheen, edge).astype(np.float32)

    def _shape(self, w: int, h: int) -> SimpleNamespace:
        key = (w, h)
        shape = self._shapes.get(key)
        if shape is not None:
            self._shapes.move_to_end(key)
            return shape
        if h == self.H and self._min_slice_w <= w <= self.card_w:
            # O meio de uma cápsula é igual em toda a largura: recorta a ponta esquerda e a
            # direita do molde e junta. Muito mais rápido que desenhar de novo.
            if self._template is None:
                self._template = self._draw_layers(self.card_w, self.H)
            W = w + 2 * self.M
            left, right = W // 2, W - W // 2
            layers = tuple(np.concatenate((a[:, :left], a[:, a.shape[1] - right :]), axis=1) for a in self._template)
        else:
            layers = self._draw_layers(w, h)
        shape = self._finish_shape(w, h, *layers)
        self._shapes[key] = shape
        if len(self._shapes) > 48:
            self._shapes.popitem(last=False)
        return shape

    def _finish_shape(self, w, h, body, rim, glow, shadow, highlight) -> SimpleNamespace:
        Hh, W = body.shape
        # A luz e o filete coloridos só existem numa faixa em volta da borda: guarda só esses pixels.
        g_mask = (glow * (1 - body)).ravel() > 0.004
        r_mask = rim.ravel() > 0.004
        u = np.flatnonzero(g_mask | r_mask)
        uy, ux = np.divmod(u, W)
        ang = (np.arctan2((uy - Hh / 2) / max(h, 1), (ux - W / 2) / max(w, 1)) / (2 * np.pi)) % 1.0

        def variant(opacity: float) -> SimpleNamespace:
            """Constantes do vidro (opaco com fundo desfocado, ou liso levemente translúcido)."""
            alpha = body * opacity
            ab = highlight + alpha * (1 - highlight)  # vidro + reflexos
            return SimpleNamespace(
                glass_w=(alpha * (1 - highlight))[..., None].astype(np.float32),
                A=(ab + shadow * (1 - ab)).astype(np.float32),  # + sombra
                u_kb=(1 - ab).ravel()[u].astype(np.float32),  # quanto da luz colorida aparece
                u_ka=((1 - shadow) * (1 - ab)).ravel()[u].astype(np.float32),
            )

        return SimpleNamespace(
            W=W, Hh=Hh, highlight=highlight[..., None], u=u,
            u_glow=np.where(g_mask[u], glow.ravel()[u], 0).astype(np.float32),
            u_rim=np.where(r_mask[u], rim.ravel()[u], 0).astype(np.float32),
            u_ang=(ang * 256).astype(np.int32),
            glassy=variant(1.0), plain=variant(0.92),
            static_key=None, static=None,
        )  # fmt: skip

    def _text(self, text: str, font) -> np.ndarray:
        """Opacidade (0-1) das letras; a cor é aplicada na hora de desenhar."""
        from PIL import Image, ImageDraw

        key = (text, id(font))
        cached = self._texts.get(key)
        if cached is not None:
            self._texts.move_to_end(key)
            return cached
        asc, desc = font.getmetrics()
        img = Image.new("L", (max(1, int(font.getlength(text)) + 6), asc + desc + 4), 0)
        ImageDraw.Draw(img).text((2, 2), text, font=font, fill=255)
        cached = np.asarray(img, np.float32) / 255
        self._texts[key] = cached
        if len(self._texts) > 96:
            self._texts.popitem(last=False)
        return cached

    # ---- a parte parada do quadro (refeita só quando o fundo, o tom ou os textos mudam)
    def _static(self, sh, glass, glass_key, light, header, old_header, cross, lines) -> SimpleNamespace:
        key = (glass_key if glass is not None else None, round(light, 2), header, old_header, cross, lines)
        if sh.static_key == key:
            return sh.static
        s, M = self.s, self.M
        if glass is None:  # sem captura do fundo: vidro liso
            v, rgb = sh.plain, DARK_TINT * 1.3 * (1 - light) + LIGHT_TINT * light
        else:  # o vidro já vem tingido (escuro e claro) da captura; aqui só mistura os dois
            v, (dark, bright) = sh.glassy, glass
            rgb = dark if light <= 0.001 else bright if light >= 0.999 else dark * (1 - light) + bright * light
        P = sh.highlight + rgb * v.glass_w
        A = v.A.copy()
        # textos (brancos no vidro escuro, quase pretos no vidro claro)
        ink = INK_DARK_GLASS * (1 - light) + INK_LIGHT_GLASS * light
        cy = M + self.H / 2
        tx = M + self.pad + self.orb_d + round(12 * s)

        def draw(mask: np.ndarray, x: int, y: int, opacity: float) -> None:
            a = mask * opacity
            _blit(P, A, a[..., None] * ink, a, x, y)

        for labels, mult in ((header, cross), (old_header, 1.0 - cross)):
            if not labels or not labels[0] or mult <= 0:
                continue
            title, sub = labels
            ta = self._text(title, self.font_title)
            draw(ta, tx, round(cy - (8.5 * s if sub else 0) - ta.shape[0] / 2), 0.96 * mult)
            if sub:
                sa = self._text(sub, self.font_sub)
                draw(sa, tx, round(cy + 9.5 * s - sa.shape[0] / 2), (0.62 - 0.08 * light) * mult)
        for i, line in enumerate(lines):
            draw(self._text(line, self.font_text), M + round(20 * s), M + self.H - round(6 * s) + i * self.line_h, 0.94)
        u, d = sh.u, self.orb_d
        ox, oy = M + self.pad, round(cy - d / 2)
        static = SimpleNamespace(
            bgra=_bgra(P, A), u_P=P.reshape(-1, 3)[u], u_A=A.reshape(-1)[u], u_kb=v.u_kb, u_ka=v.u_ka,
            orb_xy=(ox, oy), orb_P=P[oy : oy + d, ox : ox + d].copy(), orb_A=A[oy : oy + d, ox : ox + d].copy(),
        )  # fmt: skip
        sh.static_key, sh.static = key, static
        return static

    def orb(self, t: float, level: float, mode: str) -> tuple[np.ndarray, np.ndarray]:
        """Esfera de cores que se movem e crescem com a voz."""
        x, y, r = self._ox, self._oy, self._orb_r
        calm = mode == "carregando"
        blobs, colors = (ORB_BLOBS_CALM, self._blob_colors["calm"]) if calm else (ORB_BLOBS, self._blob_colors["siri"])
        spin = {"ouvindo": 1.0, "transcrevendo": 2.2}.get(mode, 0.8)
        amp = 0.32 + 0.22 * level
        acc = np.zeros((*x.shape, 3), np.float32)
        for (_, fx, fy, ph, size), color in zip(blobs, colors, strict=True):
            bx = amp * math.sin(t * fx * spin + ph)
            by = amp * math.cos(t * fy * spin + ph * 1.3)
            sig2 = 2 * (size * (0.85 + 0.45 * level)) ** 2
            acc += np.exp(-((x - bx) ** 2 + (y - by) ** 2) / sig2)[..., None] * color
        peak = acc.max(axis=-1, keepdims=True)
        rgb = acc / np.maximum(peak, 1e-4) * (1 - np.exp(-peak * 2.0))  # cores vivas, sem estourar em branco
        rgb += np.array([0.05, 0.04, 0.13], np.float32) * (1 - rgb)  # fundo azul-marinho
        radius = 0.86 + 0.12 * level  # a esfera "respira" com a voz
        ring = np.exp(-(((r - radius + 0.05) / 0.05) ** 2)) * 0.32 * (r < radius)
        rgb = np.clip(rgb * self._orb_shade + (self._orb_spec + ring)[..., None], 0, 1)
        a = np.clip((radius - r) * self.orb_d / 2, 0, 1)  # borda suave (~1 pixel)
        return (rgb * a[..., None]).astype(np.float32), a.astype(np.float32)

    # ---- um quadro
    def render(
        self, w: int, h: int, *, t: float, phase: float, level: float, intensity: float, mode: str,
        glass=None, header: tuple[str, str], glass_key=None, light: float = 0.0,
        old_header: tuple[str, str] | None = None, cross: float = 1.0, lines: list[str] | None = None,
    ) -> np.ndarray:  # fmt: skip
        """Devolve o quadro em BGRA (pré-multiplicado), pronto para a janela. `glass` = (recorte do
        vidro escuro, recorte do vidro claro) ou None."""
        sh = self._shape(w, h)
        crossing = old_header is not None and cross < 1
        st = self._static(
            sh, glass, glass_key, light, header, old_header if crossing else None,
            round(cross, 1) if crossing else 1.0, tuple(lines or ()),
        )  # fmt: skip
        out = st.bgra.copy()
        # luz colorida em volta + filete na borda (giram e crescem com a voz)
        col = self.luts["calm" if mode == "carregando" else "siri"][(sh.u_ang + int(phase * 256)) & 255]
        ga = np.minimum(sh.u_glow * (intensity * 0.9), 1.0)
        ra = np.minimum(sh.u_rim * (0.30 + 0.60 * intensity), 1.0)
        P = st.u_P + col * (ga * st.u_kb)[:, None]
        P = np.minimum(col * 1.15, 1.0) * ra[:, None] + P * (1 - ra)[:, None]
        A = ra + (st.u_A + ga * st.u_ka) * (1 - ra)
        out.reshape(-1, 4)[sh.u] = _bgra(P, A)
        # esfera
        oP, oA = self.orb(t, level, mode)
        ox, oy = st.orb_xy
        d = self.orb_d
        out[oy : oy + d, ox : ox + d] = _bgra(oP + st.orb_P * (1 - oA)[..., None], oA + st.orb_A * (1 - oA))
        return out


class LayeredSurface:
    """Entrega cada quadro para a janela translúcida (UpdateLayeredWindow)."""

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.hdc_screen = user32.GetDC(None)
        self.hdc = gdi32.CreateCompatibleDC(self.hdc_screen)
        self.size = (0, 0)
        self.bmp = self.old = None
        self.bits = ctypes.c_void_p()
        self._blend = BLENDFUNCTION(0, 0, 255, AC_SRC_ALPHA)

    def _ensure(self, w: int, h: int) -> None:
        if (w, h) == self.size:
            return
        if self.bmp:
            gdi32.SelectObject(self.hdc, self.old)
            gdi32.DeleteObject(self.bmp)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = w, -h  # de cima para baixo
        bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
        self.bits = ctypes.c_void_p()
        self.bmp = gdi32.CreateDIBSection(self.hdc_screen, ctypes.byref(bmi), 0, ctypes.byref(self.bits), None, 0)
        self.old = gdi32.SelectObject(self.hdc, self.bmp)
        self.size = (w, h)

    def push(self, bgra: np.ndarray, x: int, y: int, alpha: int = 255) -> None:
        h, w = bgra.shape[:2]
        self._ensure(w, h)
        buf = np.ascontiguousarray(bgra)
        ctypes.memmove(self.bits, buf.ctypes.data, buf.nbytes)
        self._blend.SourceConstantAlpha = max(0, min(255, alpha))  # transparência geral (entrada/saída)
        user32.UpdateLayeredWindow(
            self.hwnd, self.hdc_screen, ctypes.byref(wt.POINT(x, y)), ctypes.byref(wt.SIZE(w, h)),
            self.hdc, ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(self._blend), ULW_ALPHA,
        )  # fmt: skip


# ---------------------------------------------------------------- a janela do indicador
class GlassOverlay:
    """Indicador flutuante (por padrão, acima da barra de tarefas). Não rouba o foco e o clique
    passa por ele, exceto em cima da cápsula: ali dá para arrastá-lo (a posição fica salva) e o
    duplo clique o devolve ao lugar padrão."""

    FRAME = 1 / 60
    BACKDROP_EVERY = 0.25  # segundos entre capturas do fundo (para o vidro)

    def __init__(self, app: App):
        self.app = app
        self.s = win32.system_dpi_scale()
        self.r = GlassRenderer(self.s)
        self._cursor = user32.LoadCursorW(None, ctypes.c_void_p(IDC_SIZEALL))
        self.hwnd = self._create_window()
        self.surface = LayeredSurface(self.hwnd)
        self.timer = win32.FrameTimer()
        self.capture = bool(user32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE))
        log.info("Indicador de vidro pronto (escala %.2f, fundo desfocado: %s)", self.s, "sim" if self.capture else "não")
        # animação
        self.shown = False
        self.appear = 0.0
        self.mode = "ouvindo"
        self.cur_w = self.cur_h = None
        self.level = self.phase = 0.0
        self.t0 = self.last = time.perf_counter()
        self.header = ("", "")
        self.old_header = None
        self.header_t = 0.0
        self.text_shown = ""
        self._chars = 0.0
        self.light = self.light_target = 0.0
        self.errors = 0
        # posição e vidro
        self.cx = self.bottom = 0
        self.region = None
        self.glass = None  # (vidro escuro, vidro claro, brilho médio, versão, região capturada, miniatura)
        self.backdrop_t = 0.0
        self._last_glass = None  # vidro do último quadro: (recortes, chave)
        self._frozen = None  # vidro "congelado" enquanto arrasta
        self._frozen_version = 0
        self.frame_size = (0, 0)
        self.yoff = 0
        # mouse
        self.dragging = False
        self._drag = None
        self._moved = False
        self.hittable = False
        self.hit_rect = None
        self.reset_requested = False
        self._warm_up()
        threading.Thread(target=self._backdrop_loop, name="vidro", daemon=True).start()

    # ---- preparação e janela
    def _warm_up(self) -> None:
        """Prepara fontes, formas e a captura de tela já na abertura, para o 1º quadro sair na hora."""
        try:
            self._place()  # já faz a primeira captura do fundo
            labels = self._labels()
            w, h = self.r.size_for(*labels, None)
            self.r.render(w, h, t=0, phase=0, level=0, intensity=0.5, mode="ouvindo", glass=None, header=labels)
            self.r.orb(0, 0, "transcrevendo")
        except Exception:  # noqa: BLE001
            log.exception("Falha ao preparar o indicador")

    def _create_window(self):
        self._wndproc_ref = WNDPROC(self._wndproc)  # a referência precisa ficar viva
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = CS_DBLCLKS
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = "SoomIndicador"
        user32.RegisterClassExW(ctypes.byref(wc))
        ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE
        hwnd = user32.CreateWindowExW(ex, "SoomIndicador", APP_NAME, WS_POPUP, 0, 0, 1, 1, None, None, hinst, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        return hwnd

    # ---- mouse (arrastar)
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_MOUSEACTIVATE:  # clicar no indicador não tira o foco do campo onde você escreve
                return MA_NOACTIVATE
            if msg == WM_SETCURSOR:
                user32.SetCursor(self._cursor)
                return 1
            if msg == WM_LBUTTONDOWN:
                x, y = win32.cursor_pos()
                self._drag, self.dragging, self._moved = (x, y, self.cx, self.bottom), True, False
                self._frozen = self._last_glass  # o vidro "viaja" junto, sem recalcular no caminho
                user32.SetCapture(hwnd)
                return 0
            if msg == WM_MOUSEMOVE and self.dragging:
                x, y = win32.cursor_pos()
                sx, sy, cx, bottom = self._drag
                self._moved = self._moved or abs(x - sx) + abs(y - sy) > 3
                if self._moved:
                    self._move_to(cx + x - sx, bottom + y - sy)
                    self._reposition_now()
                return 0
            if msg in (WM_LBUTTONUP, WM_CAPTURECHANGED) and self.dragging:
                self.dragging = False
                if msg == WM_LBUTTONUP:
                    user32.ReleaseCapture()
                if self._moved:
                    self._save_position()
                    self._frozen_version = self.glass[3] if self.glass else 0
                    self.backdrop_t = 0.0  # captura o novo fundo logo
                else:
                    self._frozen = None
                return 0
            if msg == WM_LBUTTONDBLCLK:
                self.reset_requested = True
                return 0
        except Exception:  # noqa: BLE001
            log.exception("Erro no indicador (mouse)")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _reposition_now(self) -> None:
        """Move a janela na hora, no ritmo do mouse (o desenho continua no ritmo da animação)."""
        W, Hh = self.frame_size
        if self.shown and W:
            flags = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
            user32.SetWindowPos(self.hwnd, None, self.cx - W // 2, self.bottom - Hh + self.yoff, 0, 0, flags)

    def _update_hit(self) -> None:
        """Só 'pega' o mouse com o cursor em cima da cápsula; fora dela, o clique passa direto."""
        rect = self.hit_rect if self.shown else None
        inside = False
        if rect:
            px, py = win32.cursor_pos()
            inside = rect[0] <= px < rect[2] and rect[1] <= py < rect[3]
        want = inside or self.dragging
        if want != self.hittable:
            ex = user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
            ex = (ex & ~WS_EX_TRANSPARENT) if want else (ex | WS_EX_TRANSPARENT)
            user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, ex)
            self.hittable = want
        hook = getattr(self.app, "hook", None)
        if hook is not None:
            hook.ignore_rect = rect  # clicar na cápsula (para arrastar) não interrompe o ditado

    # ---- posição
    def _set_region(self) -> None:
        left = self.cx - self.r.max_w // 2
        self.region = (left, self.bottom - self.r.max_h, left + self.r.max_w, self.bottom)

    def _move_to(self, cx: float, bottom: float) -> None:
        """Move (cx = centro, bottom = base da janela) sem deixar o indicador sair da tela."""
        vx, vy, vw, vh = win32.virtual_screen()
        M, edge = self.r.M, round(90 * self.s)
        self.cx = int(min(max(cx, vx + edge), vx + vw - edge))
        self.bottom = int(min(max(bottom, vy + M + self.r.H), vy + vh + M))
        self._set_region()

    def _saved_position(self) -> tuple[int, int] | None:
        pos = self.app.cfg.get("posicao_indicador")
        if not (isinstance(pos, (list, tuple)) and len(pos) == 2 and all(isinstance(v, (int, float)) for v in pos)):
            return None
        if not win32.monitor_exists_at(pos[0], pos[1]):
            return None  # a tela onde ele estava não existe mais
        return int(pos[0]), int(pos[1])

    def _save_position(self) -> None:
        self.app.cfg["posicao_indicador"] = [self.cx, self.bottom - self.r.M]
        save_config(self.app.cfg)
        log.info("Indicador movido; posição salva")

    def _place(self) -> None:
        """Usa a posição que você escolheu; senão, centraliza acima da barra de tarefas."""
        saved = self._saved_position()
        if saved:
            self._move_to(saved[0], saved[1] + self.r.M)
        else:
            wa = win32.work_area_near(win32.foreground_window())
            self.cx = (wa.left + wa.right) // 2
            self.bottom = wa.bottom - round(22 * self.s) + self.r.M
            self._set_region()
        if self.capture:  # a 1ª captura é feita na hora, para o vidro já aparecer certo
            try:
                self._refresh_backdrop()
            except Exception:  # noqa: BLE001
                log.exception("Não consegui capturar o fundo; usando vidro liso")
                self.capture = False

    # ---- vidro
    def _refresh_backdrop(self) -> None:
        from PIL import ImageGrab

        region = self.region
        arr, lum, thumb = blur_backdrop(ImageGrab.grab(bbox=region, all_screens=True), self.s)
        old = self.glass
        self.backdrop_t = time.perf_counter()
        if old is not None and old[4] == region and float(np.abs(old[5] - thumb).mean()) < 0.006:
            return  # o fundo praticamente não mudou: não refaz o vidro
        # Já deixa o vidro tingido nas duas versões (escura e clara), fora do ritmo da animação.
        td = 0.46 + 0.34 * min(1.0, max(0.0, (lum - 0.35) / 0.5))  # mais escuro sobre fundos médios
        tl = 0.50 + 0.25 * min(1.0, max(0.0, (0.85 - lum) / 0.3))  # mais leitoso sobre fundos não tão claros
        dark = (arr * (1 - td) + DARK_TINT * td).astype(np.float32)
        bright = (arr * (1 - tl) + LIGHT_TINT * tl).astype(np.float32)
        self.glass = (dark, bright, lum, old[3] + 1 if old else 1, region, thumb)
        if lum > 0.62:  # fundo claro -> vidro claro (com folga, para não ficar trocando)
            self.light_target = 1.0
        elif lum < 0.52:
            self.light_target = 0.0

    def _backdrop_loop(self) -> None:
        """Atualiza o vidro fora do ritmo da animação (e nunca durante o arrasto)."""
        while not self.app.quitting:
            due = time.perf_counter() - self.backdrop_t > self.BACKDROP_EVERY
            if self.capture and self.shown and not self.dragging and self.region and due:
                try:
                    self._refresh_backdrop()
                except Exception:  # noqa: BLE001
                    log.exception("Não consegui capturar o fundo; usando vidro liso")
                    self.capture, self.glass = False, None
            time.sleep(0.03)

    def _pick_glass(self, x: int, y: int, W: int, Hh: int):
        """Vidro deste quadro: ((recorte escuro, recorte claro), chave) ou (None, None)."""
        g = self.glass
        frozen = self._frozen
        if frozen is not None and (self.dragging or g is None or g[3] <= self._frozen_version):
            if frozen[0][0].shape[:2] == (Hh, W):
                return frozen
        self._frozen = None
        if g is None:
            return None, None
        dark, bright, _, version, region, _ = g
        gx, gy = x - region[0], y - region[1]
        if gx >= 0 and gy >= 0:
            crop = (dark[gy : gy + Hh, gx : gx + W], bright[gy : gy + Hh, gx : gx + W])
            if crop[0].shape[:2] == (Hh, W):
                return crop, (version, gx, gy, W, Hh)
        return None, None

    # ---- conteúdo
    def _labels(self) -> tuple[str, str]:
        if self.mode == "ouvindo":
            esc = " · Esc cancela" if self.app.cfg["esc_cancela"] else ""
            return "Ouvindo", f"{self.app.hotkey_text} para concluir{esc}"
        if self.mode == "transcrevendo":
            return "Finalizando", "escrevendo o texto"
        return "Preparando", "carregando o modelo de voz"

    def _advance_text(self, target: str, dt: float) -> None:
        """Revela o texto da prévia aos poucos (modo caixinha), em vez de pular em blocos."""
        cur = self.text_shown
        if not target.startswith(cur):
            cur = cur[: common_prefix_len(cur, target)]
        rest = len(target) - len(cur)
        if rest > 0:
            self._chars += max(30.0, rest / 0.3) * dt
            n = int(self._chars)
            self._chars -= n
            cur = target[: len(cur) + n]
        self.text_shown = cur

    # ---- um quadro
    def _tick(self) -> bool:
        now = time.perf_counter()
        dt = min(0.1, now - self.last)
        self.last = now
        if self.reset_requested:
            self.reset_requested = False
            self.app.cfg["posicao_indicador"] = None
            save_config(self.app.cfg)
            log.info("Indicador de volta ao lugar padrão")
            if self.shown:
                self._place()
        state = self.app.overlay_state()
        want = state is not None or self.dragging
        if want and not self.shown and self.appear <= 0:
            self._place()
        if state is not None:
            self.mode = state
        self.appear = min(1.0, self.appear + dt / 0.34) if want else max(0.0, self.appear - dt / 0.22)
        if self.appear <= 0:
            if self.shown:
                user32.ShowWindow(self.hwnd, SW_HIDE)
                self.shown = False
                self._update_hit()
            self.text_shown, self.cur_w, self.old_header = "", None, None
            return False
        r = self.r
        labels = self._labels()
        if labels != self.header:
            if self.header != ("", "") and self.shown:
                self.old_header, self.header_t = self.header, now
            self.header = labels
        preview = self.app.preview_text() if state is not None else (self.text_shown or None)
        lines = None
        if preview is not None:
            self._advance_text(preview, dt)
            lines = r.wrap(self.text_shown) if self.text_shown else []
        tw, th = r.size_for(*self.header, lines)
        if self.cur_w is None:
            self.cur_w, self.cur_h = float(tw), float(th)
        k = 1 - math.exp(-dt * 14)  # largura/altura mudam suavemente
        self.cur_w += (tw - self.cur_w) * k
        self.cur_h += (th - self.cur_h) * k
        w, h = int(round(self.cur_w / 2)) * 2, int(round(self.cur_h / 2)) * 2
        lv = self.app.recorder.level if self.mode == "ouvindo" else 0.0
        lv = min(1.0, math.sqrt(max(lv, 0.0)) * 2.6)
        self.level += (lv - self.level) * (1 - math.exp(-dt * (18 if lv > self.level else 6)))
        t = now - self.t0
        if self.mode == "ouvindo":
            intensity = 0.32 + 0.68 * self.level + 0.05 * math.sin(t * 2.4)
        elif self.mode == "transcrevendo":
            intensity = 0.55 + 0.12 * math.sin(t * 6)
        else:
            intensity = 0.40 + 0.10 * math.sin(t * 3)
        self.phase = (self.phase + dt * (0.08 + 0.30 * self.level + (0.25 if self.mode == "transcrevendo" else 0))) % 1
        W, Hh = w + 2 * r.M, h + 2 * r.M
        x, y = self.cx - W // 2, self.bottom - Hh
        glass, glass_key = self._pick_glass(x, y, W, Hh)
        if glass is not None:
            self._last_glass = (glass, glass_key)
        if not self.shown:  # na primeira aparição, já começa com o tom certo
            self.light = self.light_target
        self.light += (self.light_target - self.light) * (1 - math.exp(-dt * 6))
        cross = min(1.0, (now - self.header_t) / 0.2) if self.old_header else 1.0
        frame = r.render(
            w, h, t=t, phase=self.phase, level=self.level, intensity=intensity, mode=self.mode, glass=glass,
            glass_key=glass_key, light=self.light, header=self.header,
            old_header=self.old_header if cross < 1 else None, cross=cross, lines=lines,
        )  # fmt: skip
        if cross >= 1:
            self.old_header = None
        e = ease_out_cubic(self.appear)
        if self.appear < 1:
            frame = scale_frame(frame, 0.90 + 0.10 * ease_out_back(self.appear) if want else 0.95 + 0.05 * e)
        self.yoff = round((1 - e) * 14 * self.s) if want else 0
        self.frame_size = (W, Hh)
        self.surface.push(frame, x, y + self.yoff, round(255 * e))
        if not self.shown:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self.shown = True
        self.hit_rect = (x + r.M, y + self.yoff + r.M, x + r.M + w, y + self.yoff + r.M + h)
        self._update_hit()
        return True

    def run(self) -> None:
        """Laço da janela: desenha no ritmo da tela e reage na hora a mensagens (mouse)."""
        msg = wt.MSG()
        next_frame = time.perf_counter()
        while not self.app.quitting:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            now = time.perf_counter()
            if now >= next_frame:
                active = False
                if self.errors < 20:
                    try:
                        active = self._tick()
                    except Exception:  # noqa: BLE001
                        self.errors += 1
                        log.exception("Erro no indicador")
                period = self.FRAME if active else 0.05
                next_frame += period
                if next_frame < now:  # atrasou (PC ocupado): retoma o ritmo a partir de agora
                    next_frame = now + period
            self.timer.wait(next_frame - time.perf_counter())
        user32.DestroyWindow(self.hwnd)
