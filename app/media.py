"""Captura de tela (mss) e de áudio do sistema (WASAPI loopback) como tracks do aiortc,
e reprodução do áudio recebido (sounddevice)."""

from __future__ import annotations

import asyncio
import ctypes
import fractions
import logging
import threading
import time
from collections import deque

import av
import mss
import numpy as np
from aiortc.mediastreams import MediaStreamTrack

log = logging.getLogger(__name__)

VIDEO_CLOCK_RATE = 90000
AUDIO_PTIME = 0.020


# ---------------------------------------------------------------- monitores


def list_monitors() -> list[dict]:
    """Monitores físicos (o índice 0 do mss é a área virtual de todos juntos)."""
    with mss.mss() as sct:
        return [
            {"index": i, "width": m["width"], "height": m["height"], "left": m["left"], "top": m["top"]}
            for i, m in enumerate(sct.monitors)
            if i > 0
        ]


# ---------------------------------------------------------------- cursor

# O mss não captura o ponteiro do mouse no Windows; desenhamos uma seta simples.
_ARROW = [
    "X...........",
    "XX..........",
    "XOX.........",
    "XOOX........",
    "XOOOX.......",
    "XOOOOX......",
    "XOOOOOX.....",
    "XOOOOOOX....",
    "XOOOOOOOX...",
    "XOOOOOOOOX..",
    "XOOOOOOOOOX.",
    "XOOOOOOXXXXX",
    "XOOOXOOX....",
    "XOOXXOOX....",
    "XOX..XOOX...",
    "XX...XOOX...",
    "X.....XOOX..",
    "......XOOX..",
    ".......XX...",
]
_ARROW_BORDER = np.array([[c == "X" for c in row] for row in _ARROW])
_ARROW_FILL = np.array([[c == "O" for c in row] for row in _ARROW])


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _cursor_pos() -> tuple[int, int] | None:
    try:
        pt = _POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return pt.x, pt.y
    except Exception:  # noqa: BLE001 - não-Windows
        pass
    return None


def _draw_cursor(img: np.ndarray, x: int, y: int, scale: int) -> None:
    border = np.kron(_ARROW_BORDER, np.ones((scale, scale), dtype=bool))
    fill = np.kron(_ARROW_FILL, np.ones((scale, scale), dtype=bool))
    h, w = border.shape
    ih, iw = img.shape[:2]
    if x >= iw or y >= ih or x + w <= 0 or y + h <= 0:
        return
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, iw), min(y + h, ih)
    sub = img[y0:y1, x0:x1]
    b = border[y0 - y : y1 - y, x0 - x : x1 - x]
    f = fill[y0 - y : y1 - y, x0 - x : x1 - x]
    sub[b, :3] = 0
    sub[f, :3] = 255


# ---------------------------------------------------------------- vídeo


class ScreenTrack(MediaStreamTrack):
    """Track de vídeo que captura um monitor numa thread própria."""

    kind = "video"

    def __init__(self, monitor_index: int = 1, fps: int = 20, max_height: int = 1080) -> None:
        super().__init__()
        self.fps = fps
        self.max_height = max_height
        self._monitor_index = monitor_index
        self._latest: np.ndarray | None = None
        self._lock = threading.Lock()
        self._running = True
        self._start: float | None = None
        self._timestamp = 0
        self._thread = threading.Thread(target=self._capture_loop, name="screen-capture", daemon=True)
        self._thread.start()

    def set_monitor(self, index: int) -> None:
        self._monitor_index = index

    def _capture_loop(self) -> None:
        interval = 1 / self.fps
        with mss.mss() as sct:
            while self._running:
                t0 = time.perf_counter()
                try:
                    monitors = sct.monitors
                    mon = monitors[self._monitor_index if self._monitor_index < len(monitors) else 1]
                    shot = sct.grab(mon)
                    img = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4).copy()
                    pos = _cursor_pos()
                    if pos:
                        scale = max(1, round(shot.height / 1080))
                        _draw_cursor(img, pos[0] - mon["left"], pos[1] - mon["top"], scale)
                    with self._lock:
                        self._latest = img
                except Exception:  # noqa: BLE001 - ex.: tela bloqueada (UAC/lock screen)
                    log.debug("falha na captura", exc_info=True)
                time.sleep(max(0, interval - (time.perf_counter() - t0)))

    def _build_frame(self) -> av.VideoFrame:
        with self._lock:
            img = self._latest
        if img is None:
            img = np.zeros((720, 1280, 4), dtype=np.uint8)
        h, w = img.shape[:2]
        # Limita a resolução (encoder em software) e garante dimensões pares.
        if h > self.max_height:
            w, h = int(w * self.max_height / h), self.max_height
        w, h = w - w % 2, h - h % 2
        frame = av.VideoFrame.from_ndarray(img, format="bgra")
        return frame.reformat(width=w, height=h, format="yuv420p")

    async def recv(self) -> av.VideoFrame:
        if self.readyState != "live":
            raise asyncio.CancelledError
        ptime = 1 / self.fps
        if self._start is None:
            self._start = time.time()
        else:
            self._timestamp += int(ptime * VIDEO_CLOCK_RATE)
            wait = self._start + self._timestamp / VIDEO_CLOCK_RATE - time.time()
            if wait > 0:
                await asyncio.sleep(wait)

        frame = await asyncio.get_running_loop().run_in_executor(None, self._build_frame)
        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, VIDEO_CLOCK_RATE)
        return frame

    def stop(self) -> None:
        self._running = False
        super().stop()


# ---------------------------------------------------------------- áudio (captura)


class SystemAudioTrack(MediaStreamTrack):
    """Áudio de tudo que toca no PC (loopback do dispositivo de saída padrão)."""

    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        import pyaudiowpatch as pyaudio

        self.muted = False
        self._pa = pyaudio.PyAudio()
        wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = self._pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if not speakers.get("isLoopbackDevice"):
            for dev in self._pa.get_loopback_device_info_generator():
                if speakers["name"] in dev["name"]:
                    speakers = dev
                    break
            else:
                raise RuntimeError("Dispositivo de loopback de áudio não encontrado.")

        self.rate = int(speakers["defaultSampleRate"])
        self.channels = max(1, int(speakers["maxInputChannels"]))
        self._samples = int(self.rate * AUDIO_PTIME)
        self._buf = bytearray()
        self._buf_lock = threading.Lock()
        self._max_bytes = int(self.rate * 0.25) * self.channels * 2  # até 250 ms de atraso
        self._pts = 0
        self._start: float | None = None

        def callback(in_data, frame_count, time_info, status):
            with self._buf_lock:
                self._buf.extend(in_data)
                overflow = len(self._buf) - self._max_bytes
                if overflow > 0:
                    del self._buf[:overflow]
            return (None, pyaudio.paContinue)

        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=speakers["index"],
            frames_per_buffer=self._samples,
            stream_callback=callback,
        )
        log.info("capturando áudio de %s (%s Hz, %s canais)", speakers["name"], self.rate, self.channels)

    async def recv(self) -> av.AudioFrame:
        if self.readyState != "live":
            raise asyncio.CancelledError
        if self._start is None:
            self._start = time.time()
        else:
            wait = self._start + self._pts / self.rate - time.time()
            if wait > 0:
                await asyncio.sleep(wait)

        need = self._samples * self.channels * 2
        with self._buf_lock:
            chunk = bytes(self._buf[:need])
            del self._buf[:need]
        # O loopback do WASAPI não entrega nada quando está tudo em silêncio.
        pcm = np.frombuffer(chunk.ljust(need, b"\0"), dtype=np.int16).reshape(-1, self.channels)
        if self.channels > 2:
            pcm = pcm[:, :2]
        elif self.channels == 1:
            pcm = np.repeat(pcm, 2, axis=1)
        if self.muted:
            pcm = np.zeros_like(pcm)

        frame = av.AudioFrame(format="s16", layout="stereo", samples=self._samples)
        frame.planes[0].update(np.ascontiguousarray(pcm).tobytes())
        frame.sample_rate = self.rate
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, self.rate)
        self._pts += self._samples
        return frame

    def stop(self) -> None:
        super().stop()
        try:
            self._stream.stop_stream()
            self._stream.close()
            self._pa.terminate()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- áudio (reprodução)


class AudioPlayer:
    """Toca frames de áudio recebidos (s16 48 kHz estéreo) com controle de volume."""

    RATE = 48000
    CHANNELS = 2

    def __init__(self) -> None:
        import sounddevice as sd

        self.volume = 1.0
        self.muted = False
        self._chunks: deque[np.ndarray] = deque()
        self._pending = 0
        self._primed = False
        self._lock = threading.Lock()
        self._stream = sd.OutputStream(
            samplerate=self.RATE, channels=self.CHANNELS, dtype="int16", callback=self._callback, latency="low"
        )
        self._stream.start()
        self._resampler = av.AudioResampler(format="s16", layout="stereo", rate=self.RATE)

    def push(self, frame: av.AudioFrame) -> None:
        for out in self._resampler.resample(frame):
            pcm = out.to_ndarray().reshape(-1, self.CHANNELS)
            with self._lock:
                self._chunks.append(pcm)
                self._pending += len(pcm)
                # Descarta atraso acumulado acima de ~300 ms.
                while self._pending > self.RATE * 0.3 and self._chunks:
                    self._pending -= len(self._chunks.popleft())

    def _callback(self, outdata, frames, time_info, status) -> None:
        outdata.fill(0)
        with self._lock:
            if not self._primed:
                if self._pending < self.RATE * 0.06:  # espera 60 ms de buffer
                    return
                self._primed = True
            filled = 0
            while filled < frames and self._chunks:
                chunk = self._chunks[0]
                take = min(frames - filled, len(chunk))
                outdata[filled : filled + take] = chunk[:take]
                if take == len(chunk):
                    self._chunks.popleft()
                else:
                    self._chunks[0] = chunk[take:]
                self._pending -= take
                filled += take
            if filled < frames:
                self._primed = False
        vol = 0.0 if self.muted else self.volume
        if vol != 1.0:
            outdata[:] = (outdata * vol).astype(np.int16)

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass
