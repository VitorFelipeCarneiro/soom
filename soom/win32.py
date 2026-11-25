"""Acesso às funções do Windows (via ctypes): teclado, mouse, janelas, telas e desenho.

Tudo que conversa direto com o sistema fica aqui; os outros módulos chamam `win32.<função>`.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

# ---------------------------------------------------------------- constantes
WH_KEYBOARD_LL, WH_MOUSE_LL, HC_ACTION = 13, 14, 0
WM_SETCURSOR, WM_MOUSEACTIVATE, WM_TIMER = 0x0020, 0x0021, 0x0113
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK = 0x0200, 0x0201, 0x0202, 0x0203
WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0204, 0x0207, 0x0208
WM_XBUTTONDOWN, WM_XBUTTONUP, WM_CAPTURECHANGED = 0x020B, 0x020C, 0x0215
WM_APP = 0x8000
LLKHF_INJECTED, LLMHF_INJECTED = 0x10, 0x01
VK_BACK, VK_RETURN, VK_CONTROL, VK_ESCAPE, VK_V = 0x08, 0x0D, 0x11, 0x1B, 0x56
VK_MASK = 0xE8  # tecla "vazia": impede o Menu Iniciar de abrir ao soltar o Win
INPUT_KEYBOARD, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 1, 0x0002, 0x0004
GWL_EXSTYLE = -20
WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x08, 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x4, 0x10
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
MA_NOACTIVATE, CS_DBLCLKS, IDC_SIZEALL = 3, 0x0008, 32646
WDA_EXCLUDEFROMCAPTURE = 0x11  # a janela some de capturas de tela
ULW_ALPHA, AC_SRC_ALPHA = 0x2, 0x1
MONITOR_DEFAULTTONULL, MONITOR_DEFAULTTONEAREST = 0, 2
SPI_GETWORKAREA = 0x0030
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
PM_REMOVE, QS_ALLINPUT, INFINITE = 0x1, 0x04FF, 0xFFFFFFFF
CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS = 0x2, 0x1F0003
ERROR_ALREADY_EXISTS = 183


# ---------------------------------------------------------------- estruturas
class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (
        ("pt", wt.POINT),
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD))


class _INPUTUNION(ctypes.Union):
    _fields_ = (("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT))


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (("type", wt.DWORD), ("u", _INPUTUNION))


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = (
        ("cbSize", wt.UINT),
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
        ("hIconSm", wt.HICON),
    )


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = (
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    )


class BITMAPINFO(ctypes.Structure):
    _fields_ = (("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3))


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = (
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    )


class MONITORINFO(ctypes.Structure):
    _fields_ = (("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD))


# ---------------------------------------------------------------- assinaturas das funções
def _declare(dll, name: str, restype, *argtypes) -> None:
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = restype, argtypes


P = ctypes.POINTER
_declare(user32, "SetWindowsHookExW", wt.HHOOK, ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD)
_declare(user32, "UnhookWindowsHookEx", wt.BOOL, wt.HHOOK)
_declare(user32, "CallNextHookEx", LRESULT, wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM)
_declare(user32, "GetMessageW", wt.BOOL, P(wt.MSG), wt.HWND, wt.UINT, wt.UINT)
_declare(user32, "PeekMessageW", wt.BOOL, P(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT)
_declare(user32, "TranslateMessage", wt.BOOL, P(wt.MSG))
_declare(user32, "DispatchMessageW", LRESULT, P(wt.MSG))
_declare(user32, "PostThreadMessageW", wt.BOOL, wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM)
_declare(user32, "MsgWaitForMultipleObjects", wt.DWORD, wt.DWORD, P(wt.HANDLE), wt.BOOL, wt.DWORD, wt.DWORD)
_declare(user32, "SetTimer", ctypes.c_size_t, wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p)
_declare(user32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
_declare(user32, "SendInput", wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_int)
_declare(user32, "GetForegroundWindow", wt.HWND)
_declare(user32, "GetCursorPos", wt.BOOL, P(wt.POINT))
_declare(user32, "GetWindowLongPtrW", ctypes.c_ssize_t, wt.HWND, ctypes.c_int)
_declare(user32, "SetWindowLongPtrW", ctypes.c_ssize_t, wt.HWND, ctypes.c_int, ctypes.c_ssize_t)
_declare(user32, "SetWindowPos", wt.BOOL, wt.HWND, wt.HWND, *(ctypes.c_int,) * 4, wt.UINT)
_declare(user32, "ShowWindow", wt.BOOL, wt.HWND, ctypes.c_int)
_declare(user32, "DestroyWindow", wt.BOOL, wt.HWND)
_declare(user32, "DefWindowProcW", LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_declare(user32, "RegisterClassExW", wt.ATOM, P(WNDCLASSEXW))
_declare(
    user32, "CreateWindowExW", wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    *(ctypes.c_int,) * 4, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
)  # fmt: skip
_declare(user32, "GetDC", wt.HDC, wt.HWND)
_declare(
    user32, "UpdateLayeredWindow", wt.BOOL, wt.HWND, wt.HDC, P(wt.POINT), P(wt.SIZE), wt.HDC, P(wt.POINT),
    wt.DWORD, P(BLENDFUNCTION), wt.DWORD,
)  # fmt: skip
_declare(user32, "SetWindowDisplayAffinity", wt.BOOL, wt.HWND, wt.DWORD)
_declare(user32, "SetCapture", wt.HWND, wt.HWND)
_declare(user32, "ReleaseCapture", wt.BOOL)
_declare(user32, "LoadCursorW", wt.HANDLE, wt.HINSTANCE, ctypes.c_void_p)
_declare(user32, "SetCursor", wt.HANDLE, wt.HANDLE)
_declare(user32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
_declare(user32, "MonitorFromWindow", wt.HANDLE, wt.HWND, wt.DWORD)
_declare(user32, "MonitorFromPoint", wt.HANDLE, wt.POINT, wt.DWORD)
_declare(user32, "GetMonitorInfoW", wt.BOOL, wt.HANDLE, P(MONITORINFO))
_declare(user32, "SystemParametersInfoW", wt.BOOL, wt.UINT, wt.UINT, P(wt.RECT), wt.UINT)
_declare(user32, "MessageBoxW", ctypes.c_int, wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT)
_declare(user32, "MapVirtualKeyW", wt.UINT, wt.UINT, wt.UINT)
_declare(user32, "GetKeyNameTextW", ctypes.c_int, wt.LONG, wt.LPWSTR, ctypes.c_int)
_declare(user32, "GetParent", wt.HWND, wt.HWND)
_declare(kernel32, "GetModuleHandleW", wt.HMODULE, wt.LPCWSTR)
_declare(kernel32, "GetCurrentThreadId", wt.DWORD)
_declare(kernel32, "CreateMutexW", wt.HANDLE, ctypes.c_void_p, wt.BOOL, wt.LPCWSTR)
_declare(kernel32, "CloseHandle", wt.BOOL, wt.HANDLE)
_declare(kernel32, "CreateWaitableTimerExW", wt.HANDLE, ctypes.c_void_p, wt.LPCWSTR, wt.DWORD, wt.DWORD)
_declare(kernel32, "SetWaitableTimer", wt.BOOL, wt.HANDLE, P(wt.LARGE_INTEGER), wt.LONG, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL)
_declare(gdi32, "CreateCompatibleDC", wt.HDC, wt.HDC)
_declare(gdi32, "CreateDIBSection", wt.HBITMAP, wt.HDC, P(BITMAPINFO), wt.UINT, P(ctypes.c_void_p), wt.HANDLE, wt.DWORD)
_declare(gdi32, "SelectObject", wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
_declare(gdi32, "DeleteObject", wt.BOOL, wt.HGDIOBJ)
del P


# ---------------------------------------------------------------- teclado
MOD_VKS = {"ctrl": (0xA2, 0xA3), "alt": (0xA4, 0xA5), "shift": (0xA0, 0xA1), "win": (0x5B, 0x5C)}
MODIFIER_VKS = frozenset({v for vks in MOD_VKS.values() for v in vks} | {0x10, 0x11, 0x12, VK_MASK})


def send_raw(items: list[tuple[int, int, int]]) -> None:
    """Envia eventos de teclado (tecla virtual, caractere, flags) como se viessem do teclado."""
    arr = (INPUT * len(items))()
    for i, (vk, scan, flags) in enumerate(items):
        arr[i].type = INPUT_KEYBOARD
        arr[i].ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    user32.SendInput(len(items), arr, ctypes.sizeof(INPUT))


def send_keys(events: list[tuple[int, bool]]) -> None:
    """Envia uma sequência de (tecla, soltar?)."""
    send_raw([(vk, 0, KEYEVENTF_KEYUP if up else 0) for vk, up in events])


def type_text(text: str, backspaces: int = 0) -> None:
    """Apaga `backspaces` caracteres antes do cursor e digita `text` (qualquer caractere Unicode)."""
    items = [(VK_BACK, 0, flags) for _ in range(backspaces) for flags in (0, KEYEVENTF_KEYUP)]
    raw = text.encode("utf-16-le")
    for i in range(0, len(raw), 2):
        unit = raw[i] | (raw[i + 1] << 8)
        items += [(0, unit, KEYEVENTF_UNICODE), (0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    for i in range(0, len(items), 400):  # em lotes, para nenhum app perder teclas
        if i:
            time.sleep(0.01)
        send_raw(items[i : i + 400])


def key_is_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def key_name(vk: int) -> str:
    """Nome da tecla como o Windows mostra no teclado do usuário (ex.: 'Ç', ';')."""
    scan = user32.MapVirtualKeyW(vk, 0)
    if not scan:
        return ""
    buf = ctypes.create_unicode_buffer(32)
    n = user32.GetKeyNameTextW(scan << 16, buf, len(buf))
    return buf.value[:n] if n > 0 else ""


def modifiers_down() -> bool:
    return any(key_is_down(v) for vks in MOD_VKS.values() for v in vks)


def wait_for_modifier_release(timeout: float = 3.0) -> None:
    """Espera soltar Win/Ctrl/Alt/Shift, para o que enviamos não virar outro atalho."""
    end = time.monotonic() + timeout
    while modifiers_down() and time.monotonic() < end:
        time.sleep(0.02)


# ---------------------------------------------------------------- janelas e telas
def foreground_window() -> int:
    return user32.GetForegroundWindow() or 0


def cursor_pos() -> tuple[int, int]:
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def virtual_screen() -> tuple[int, int, int, int]:
    """(x, y, largura, altura) da área que junta todas as telas."""
    m = user32.GetSystemMetrics
    return m(SM_XVIRTUALSCREEN), m(SM_YVIRTUALSCREEN), m(SM_CXVIRTUALSCREEN), m(SM_CYVIRTUALSCREEN)


def monitor_exists_at(x: int, y: int) -> bool:
    return bool(user32.MonitorFromPoint(wt.POINT(int(x), int(y)), MONITOR_DEFAULTTONULL))


def work_area_near(hwnd: int | None) -> wt.RECT:
    """Área útil (sem a barra de tarefas) da tela onde está a janela `hwnd`."""
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    mon = user32.MonitorFromWindow(hwnd or None, MONITOR_DEFAULTTONEAREST)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return mi.rcWork
    rect = wt.RECT()
    user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    return rect


def set_dpi_awareness() -> None:
    """Desenha em pixels reais (nítido em telas com escala de 125%, 150%...)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def system_dpi_scale() -> float:
    try:
        return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96)
    except (AttributeError, OSError):
        return 1.0


def single_instance(name: str):
    """Devolve um 'cadeado' do sistema, ou None se o app já estiver aberto."""
    handle = kernel32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return handle


def message_box(text: str, title: str) -> None:
    user32.MessageBoxW(None, text, title, 0x40)


class FrameTimer:
    """Espera precisa (fração de milissegundo) que também acorda na hora se chegar uma mensagem
    para a janela (ex.: o mouse arrastando o indicador)."""

    def __init__(self):
        self.handle = kernel32.CreateWaitableTimerExW(
            None, None, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS
        ) or kernel32.CreateWaitableTimerExW(None, None, 0, TIMER_ALL_ACCESS)
        self._handles = (wt.HANDLE * 1)(self.handle) if self.handle else None

    def wait(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self.handle:
            due = wt.LARGE_INTEGER(-max(1, int(seconds * 10_000_000)))  # negativo = relativo, em 100 ns
            kernel32.SetWaitableTimer(self.handle, ctypes.byref(due), 0, None, None, False)
            user32.MsgWaitForMultipleObjects(1, self._handles, False, INFINITE, QS_ALLINPUT)
        else:
            user32.MsgWaitForMultipleObjects(0, None, False, max(1, int(seconds * 1000)), QS_ALLINPUT)
