"""Janelas e processos do Windows: listar janelas visíveis e capturar a imagem de uma janela."""

from __future__ import annotations

import ctypes
import os
from ctypes import byref, wintypes

import numpy as np

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14
TH32CS_SNAPPROCESS = 0x2

for _fn in ("IsWindow", "IsIconic", "IsWindowVisible", "GetWindowTextLengthW"):
    getattr(user32, _fn).argtypes = [wintypes.HWND]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


# ---------------------------------------------------------------- processos


def _processes() -> dict[int, tuple[int, str]]:
    """pid -> (pid do pai, nome do executável)."""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    procs: dict[int, tuple[int, str]] = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel32.Process32FirstW(snap, byref(entry))
        while ok:
            procs[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            ok = kernel32.Process32NextW(snap, byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return procs


def _root_pid(pid: int, procs: dict[int, tuple[int, str]]) -> int:
    """Sobe até o processo principal do mesmo executável (ex.: o chrome.exe "pai" de todos).

    O process loopback captura a árvore de processos, então o som de abas/serviço de
    áudio (processos filhos) entra junto.
    """
    exe = procs.get(pid, (0, ""))[1].lower()
    seen = {pid}
    while True:
        parent = procs.get(pid, (0, ""))[0]
        if parent in seen or parent not in procs or procs[parent][1].lower() != exe:
            return pid
        seen.add(parent)
        pid = parent


# ---------------------------------------------------------------- janelas


def _is_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD()
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_CLOAKED, byref(cloaked), ctypes.sizeof(cloaked))
    except Exception:  # noqa: BLE001
        return False
    return bool(cloaked.value)


def list_windows() -> list[dict]:
    """Janelas de aplicativo visíveis (as mesmas que aparecem no Alt+Tab, aproximadamente)."""
    procs = _processes()
    own = os.getpid()
    result: list[dict] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or _is_cloaked(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, byref(pid))
        if pid.value == own:
            return True
        exe = procs.get(pid.value, (0, "?"))[1]
        result.append(
            {"hwnd": int(hwnd), "title": buf.value, "pid": pid.value, "root_pid": _root_pid(pid.value, procs), "exe": exe}
        )
        return True

    user32.EnumWindows(callback, 0)
    return result


def list_audio_apps() -> list[dict]:
    """Um item por aplicativo com janela aberta (agrupado pelo processo principal)."""
    apps: dict[int, dict] = {}
    for win in list_windows():
        apps.setdefault(win["root_pid"], {"pid": win["root_pid"], "exe": win["exe"], "title": win["title"]})
    return sorted(apps.values(), key=lambda a: a["exe"].lower())


def window_exists(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def grab_window(hwnd: int) -> tuple[np.ndarray, int, int] | None:
    """Imagem BGRA da área cliente da janela, mais a posição dela na tela.

    Usa PrintWindow (funciona mesmo com a janela atrás de outras; minimizada, não).
    """
    if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        return None
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w < 16 or h < 16:
        return None
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, byref(origin))

    hdc_win = user32.GetDC(hwnd)
    hdc = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    old = gdi32.SelectObject(hdc, bmp)
    try:
        if not user32.PrintWindow(hwnd, hdc, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            return None
        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(header)
        header.biWidth, header.biHeight = w, -h  # negativo = de cima para baixo
        header.biPlanes, header.biBitCount = 1, 32
        img = np.empty((h, w, 4), dtype=np.uint8)
        if not gdi32.GetDIBits(hdc, bmp, 0, h, img.ctypes.data, byref(header), 0):
            return None
        return img, origin.x, origin.y
    finally:
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(hdc)
        user32.ReleaseDC(hwnd, hdc_win)
