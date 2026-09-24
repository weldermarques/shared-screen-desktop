"""Captura de áudio por processo no Windows (WASAPI "process loopback", Windows 10 2004+).

Permite capturar só o som de um aplicativo (e dos processos filhos dele, ex.: o
serviço de áudio do Chrome) ou o som de tudo MENOS um aplicativo (usado para
não retransmitir a voz da sala que o próprio app está tocando).

Implementado com ctypes puro: o ActivateAudioInterfaceAsync exige um objeto COM
de callback, montado aqui à mão (vtable de funções ctypes).
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import POINTER, byref, c_uint32, c_uint64, c_void_p, wintypes
from typing import Callable

log = logging.getLogger(__name__)

HRESULT = ctypes.c_long
S_OK = 0
E_NOINTERFACE = 0x80004002 - (1 << 32)

VT_BLOB = 65
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE = 1

AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
WAVE_FORMAT_PCM = 1

VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "GUID":
        h = text.replace("-", "")
        return cls(int(h[0:8], 16), int(h[8:12], 16), int(h[12:16], 16), (ctypes.c_ubyte * 8)(*bytes.fromhex(h[16:])))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, GUID) and bytes(self) == bytes(other)


IID_IUnknown = GUID.parse("00000000-0000-0000-C000-000000000046")
IID_IAgileObject = GUID.parse("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90")
IID_IActivateAudioInterfaceCompletionHandler = GUID.parse("41D949AB-9862-444A-80F6-C261334DA5EB")
IID_IAudioClient = GUID.parse("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
IID_IAudioCaptureClient = GUID.parse("C8ADBD64-E71E-48a0-A4DE-185C395CD317")


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
    _fields_ = [("ActivationType", ctypes.c_int), ("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", ctypes.c_int)]


class BLOB(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", c_void_p)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [
        ("vt", wintypes.USHORT),
        ("r1", wintypes.WORD),
        ("r2", wintypes.WORD),
        ("r3", wintypes.WORD),
        ("blob", BLOB),
        ("_pad", c_void_p),  # PROPVARIANT tem 24 bytes em 64 bits
    ]


# ---------------------------------------------------------------- chamadas COM por vtable


def _method(obj: c_void_p, index: int, restype, *argtypes):
    vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    fn = proto(vtable[index])
    return lambda *args: fn(obj, *args)


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} falhou (HRESULT 0x{hr & 0xFFFFFFFF:08X})")


def _release(obj: c_void_p | None) -> None:
    if obj:
        _method(obj, 2, wintypes.ULONG)()


# ---------------------------------------------------------------- objeto de callback

_QI = ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))
_ADDREF = ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)
_ACTIVATE_COMPLETED = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_void_p)


class _HandlerVtbl(ctypes.Structure):
    _fields_ = [("QueryInterface", _QI), ("AddRef", _ADDREF), ("Release", _ADDREF), ("ActivateCompleted", _ACTIVATE_COMPLETED)]


class _Handler(ctypes.Structure):
    _fields_ = [("lpVtbl", POINTER(_HandlerVtbl))]


class _CompletionHandler:
    """IActivateAudioInterfaceCompletionHandler (+ IAgileObject, exigido pela API)."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.hr = S_OK
        self.client = c_void_p()

        def query_interface(this, riid, ppv):
            iid = riid.contents
            if iid in (IID_IUnknown, IID_IAgileObject, IID_IActivateAudioInterfaceCompletionHandler):
                ppv[0] = this
                return S_OK
            ppv[0] = None
            return E_NOINTERFACE

        def completed(this, operation):
            try:
                hr_activate = HRESULT()
                unk = c_void_p()
                hr = _method(c_void_p(operation), 3, HRESULT, POINTER(HRESULT), POINTER(c_void_p))(
                    byref(hr_activate), byref(unk)
                )
                self.hr = hr if hr < 0 else hr_activate.value
                if self.hr >= 0:
                    self.client = unk
            except Exception:  # noqa: BLE001
                log.exception("erro no callback de ativação de áudio")
                self.hr = -1
            finally:
                self.done.set()
            return S_OK

        # O objeto vive enquanto este Python viver; contagem de referência fixa.
        self._callbacks = (_QI(query_interface), _ADDREF(lambda this: 1), _ADDREF(lambda this: 1), _ACTIVATE_COMPLETED(completed))
        self._vtbl = _HandlerVtbl(*self._callbacks)
        self._obj = _Handler(ctypes.pointer(self._vtbl))

    @property
    def pointer(self) -> c_void_p:
        return ctypes.cast(ctypes.pointer(self._obj), c_void_p)


# ---------------------------------------------------------------- API pública


def is_supported() -> bool:
    """Process loopback existe a partir do Windows 10 2004 (build 19041)."""
    if sys.platform != "win32":
        return False
    try:
        return sys.getwindowsversion().build >= 19041
    except Exception:  # noqa: BLE001
        return False


class ProcessLoopbackCapture:
    """Captura o áudio de um processo (include) ou de todos menos ele (exclude).

    Entrega PCM s16 intercalado para ``on_data`` numa thread própria.
    """

    RATE = 48000
    CHANNELS = 2

    def __init__(self, pid: int, include: bool, on_data: Callable[[bytes], None]) -> None:
        self.pid = pid
        self.include = include
        self._on_data = on_data
        self._running = True
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="process-loopback", daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            self._running = False
            raise OSError("Tempo esgotado ao iniciar a captura de áudio do aplicativo.")
        if self._error:
            raise self._error

    def stop(self) -> None:
        self._running = False
        self._thread.join(timeout=2)

    def _run(self) -> None:
        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
        client = capture = None
        event = None
        try:
            client = self._activate()
            fmt = WAVEFORMATEX(WAVE_FORMAT_PCM, self.CHANNELS, self.RATE, self.RATE * self.CHANNELS * 2, self.CHANNELS * 2, 16, 0)
            flags = (
                AUDCLNT_STREAMFLAGS_LOOPBACK
                | AUDCLNT_STREAMFLAGS_EVENTCALLBACK
                | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
                | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
            )
            # IAudioClient: 3 Initialize, 10 Start, 11 Stop, 13 SetEventHandle, 14 GetService
            _check(
                _method(client, 3, HRESULT, ctypes.c_int, wintypes.DWORD, ctypes.c_longlong, ctypes.c_longlong, POINTER(WAVEFORMATEX), c_void_p)(
                    AUDCLNT_SHAREMODE_SHARED, flags, 200_000, 0, byref(fmt), None
                ),
                "IAudioClient.Initialize",
            )
            event = ctypes.windll.kernel32.CreateEventW(None, False, False, None)
            _check(_method(client, 13, HRESULT, wintypes.HANDLE)(event), "SetEventHandle")
            capture = c_void_p()
            _check(
                _method(client, 14, HRESULT, POINTER(GUID), POINTER(c_void_p))(byref(IID_IAudioCaptureClient), byref(capture)),
                "GetService(IAudioCaptureClient)",
            )
            _check(_method(client, 10, HRESULT)(), "IAudioClient.Start")
        except BaseException as exc:  # noqa: BLE001
            self._error = exc
            self._ready.set()
            _release(capture)
            _release(client)
            if event:
                ctypes.windll.kernel32.CloseHandle(event)
            ole32.CoUninitialize()
            return

        self._ready.set()
        frame_bytes = self.CHANNELS * 2
        # IAudioCaptureClient: 3 GetBuffer, 4 ReleaseBuffer, 5 GetNextPacketSize
        get_buffer = _method(capture, 3, HRESULT, POINTER(c_void_p), POINTER(c_uint32), POINTER(wintypes.DWORD), POINTER(c_uint64), POINTER(c_uint64))
        release_buffer = _method(capture, 4, HRESULT, c_uint32)
        next_packet = _method(capture, 5, HRESULT, POINTER(c_uint32))
        data, frames, flags, packet = c_void_p(), c_uint32(), wintypes.DWORD(), c_uint32()
        try:
            while self._running:
                ctypes.windll.kernel32.WaitForSingleObject(event, 100)
                while self._running and next_packet(byref(packet)) >= 0 and packet.value:
                    if get_buffer(byref(data), byref(frames), byref(flags), None, None) < 0:
                        break
                    size = frames.value * frame_bytes
                    if flags.value & AUDCLNT_BUFFERFLAGS_SILENT or not data.value:
                        chunk = bytes(size)
                    else:
                        chunk = ctypes.string_at(data.value, size)
                    release_buffer(frames.value)
                    self._on_data(chunk)
        except Exception:  # noqa: BLE001
            log.exception("falha na captura de áudio do aplicativo")
        finally:
            _method(client, 11, HRESULT)()
            _release(capture)
            _release(client)
            ctypes.windll.kernel32.CloseHandle(event)
            ole32.CoUninitialize()

    def _activate(self) -> c_void_p:
        mode = PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE if self.include else PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE
        params = AUDIOCLIENT_ACTIVATION_PARAMS(AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK, self.pid, mode)
        prop = PROPVARIANT()
        prop.vt = VT_BLOB
        prop.blob.cbSize = ctypes.sizeof(params)
        prop.blob.pBlobData = ctypes.cast(ctypes.pointer(params), c_void_p)

        handler = _CompletionHandler()
        operation = c_void_p()
        activate = ctypes.windll.mmdevapi.ActivateAudioInterfaceAsync
        activate.restype = HRESULT
        activate.argtypes = [wintypes.LPCWSTR, POINTER(GUID), POINTER(PROPVARIANT), c_void_p, POINTER(c_void_p)]
        _check(
            activate(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, byref(IID_IAudioClient), byref(prop), handler.pointer, byref(operation)),
            "ActivateAudioInterfaceAsync",
        )
        try:
            if not handler.done.wait(5):
                raise OSError("Tempo esgotado ativando a captura de áudio do aplicativo.")
            _check(handler.hr, "ativação do process loopback")
            return handler.client
        finally:
            _release(operation)
