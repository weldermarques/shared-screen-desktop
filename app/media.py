"""Captura de tela/janela e de áudio (PC inteiro, um aplicativo ou microfone) como
tracks do aiortc, e reprodução do áudio recebido (sounddevice)."""

from __future__ import annotations

import asyncio
import ctypes
import fractions
import logging
import os
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
    """Track de vídeo que captura um monitor ou uma janela numa thread própria.

    ``source`` é ("monitor", índice do mss) ou ("window", hwnd).
    """

    kind = "video"

    def __init__(self, source: tuple[str, int] = ("monitor", 1), fps: int = 20, max_height: int = 1080) -> None:
        super().__init__()
        self.fps = fps
        self.max_height = max_height
        self._source = source
        self._latest: np.ndarray | None = None
        self._lock = threading.Lock()
        self._running = True
        self._start: float | None = None
        self._timestamp = 0
        self._thread = threading.Thread(target=self._capture_loop, name="screen-capture", daemon=True)
        self._thread.start()

    def set_source(self, source: tuple[str, int]) -> None:
        self._source = source

    def _grab(self, sct) -> tuple[np.ndarray, int, int] | None:
        kind, value = self._source
        if kind == "window":
            from .winwindows import grab_window

            return grab_window(value)  # minimizada: None, mantém o último quadro
        monitors = sct.monitors
        mon = monitors[value if value < len(monitors) else 1]
        shot = sct.grab(mon)
        img = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4).copy()
        return img, mon["left"], mon["top"]

    def _capture_loop(self) -> None:
        interval = 1 / self.fps
        with mss.mss() as sct:
            while self._running:
                t0 = time.perf_counter()
                try:
                    grabbed = self._grab(sct)
                    if grabbed is not None:
                        img, left, top = grabbed
                        pos = _cursor_pos()
                        if pos:
                            scale = max(1, round(img.shape[0] / 1080))
                            _draw_cursor(img, pos[0] - left, pos[1] - top, scale)
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


class PcmTrack(MediaStreamTrack):
    """Base das tracks de áudio: recebe PCM s16 intercalado de uma thread de captura
    (``_push``) e entrega frames estéreo de 20 ms.

    ``device_clock=False`` (loopback): o ritmo é o do relógio e falta de dados vira silêncio,
    porque o loopback do WASAPI não entrega nada quando está tudo quieto.
    ``device_clock=True`` (microfone): espera os dados do dispositivo, que entrega
    continuamente; completar com silêncio aqui abriria buracos na voz.
    """

    kind = "audio"

    def __init__(self, rate: int, channels: int, device_clock: bool = False) -> None:
        super().__init__()
        self.muted = False
        self.rate = rate
        self.channels = channels
        self._device_clock = device_clock
        self._samples = int(rate * AUDIO_PTIME)
        self._buf = bytearray()
        self._buf_lock = threading.Lock()
        self._max_bytes = int(rate * 0.25) * channels * 2  # até 250 ms de atraso
        self._pts = 0
        self._start: float | None = None

    def _push(self, data: bytes) -> None:
        with self._buf_lock:
            self._buf.extend(data)
            overflow = len(self._buf) - self._max_bytes
            if overflow > 0:
                del self._buf[:overflow]

    async def _wait_device(self, need: int) -> None:
        # Espera por sondagem curta: sem Event/wait_for, que se perdem no loop aninhado do qasync.
        deadline = time.monotonic() + 0.5
        while self.readyState == "live":
            with self._buf_lock:
                if len(self._buf) >= need:
                    # Atraso acumulado (ex.: ninguém consumia ainda) vira atraso fixo: descarta.
                    if len(self._buf) > need * 3:
                        del self._buf[: len(self._buf) - need]
                    return
            if time.monotonic() > deadline:
                return  # dispositivo parado: manda silêncio para não travar a conexão
            await asyncio.sleep(0.005)

    async def recv(self) -> av.AudioFrame:
        if self.readyState != "live":
            raise asyncio.CancelledError
        need = self._samples * self.channels * 2
        if self._device_clock:
            await self._wait_device(need)
        elif self._start is None:
            self._start = time.time()
        else:
            wait = self._start + self._pts / self.rate - time.time()
            if wait > 0:
                await asyncio.sleep(wait)

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


class SystemAudioTrack(PcmTrack):
    """Áudio de tudo que toca no PC (loopback do dispositivo de saída padrão).

    Fallback para Windows antigo: inclui também o que o próprio app toca (voz da sala).
    """

    def __init__(self) -> None:
        import pyaudiowpatch as pyaudio

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

        super().__init__(int(speakers["defaultSampleRate"]), max(1, int(speakers["maxInputChannels"])))

        def callback(in_data, frame_count, time_info, status):
            self._push(in_data)
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

    def stop(self) -> None:
        super().stop()
        try:
            self._stream.stop_stream()
            self._stream.close()
            self._pa.terminate()
        except Exception:  # noqa: BLE001
            pass


class ProcessAudioTrack(PcmTrack):
    """Áudio de um aplicativo só (include) ou de todo o PC menos um aplicativo (exclude)."""

    def __init__(self, pid: int, include: bool) -> None:
        from .winaudio import ProcessLoopbackCapture

        super().__init__(ProcessLoopbackCapture.RATE, ProcessLoopbackCapture.CHANNELS)
        self._capture = ProcessLoopbackCapture(pid, include, self._push)
        log.info("capturando áudio %s o processo %s", "só de" if include else "de tudo menos", pid)

    def stop(self) -> None:
        super().stop()
        self._capture.stop()


class LevelMeter:
    """Diz se há som (alguém falando) olhando o volume RMS dos últimos blocos de áudio."""

    THRESHOLD = 500  # RMS mínimo em int16 (~ -36 dBFS)
    NOISE_FACTOR = 4  # e bem acima do ruído de fundo (ventilador, chiado do fone)
    HOLD = 0.3  # s aceso depois do último bloco alto, para não piscar entre as palavras

    def __init__(self) -> None:
        self._last_loud = 0.0
        self._floor = float(self.THRESHOLD)

    def update(self, pcm: np.ndarray) -> None:
        if not pcm.size:
            return
        rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))
        # Piso de ruído: desce na hora, sobe devagar (fala não "vira" ruído de fundo).
        self._floor = rms if rms < self._floor else self._floor + (rms - self._floor) * 0.002
        if rms > max(self.THRESHOLD, self._floor * self.NOISE_FACTOR):
            self._last_loud = time.monotonic()

    @property
    def active(self) -> bool:
        return time.monotonic() - self._last_loud < self.HOLD


class MicrophoneTrack(PcmTrack):
    """Microfone padrão do Windows (voz da sala)."""

    def __init__(self) -> None:
        import sounddevice as sd

        super().__init__(48000, 1, device_clock=True)
        self.meter = LevelMeter()

        def callback(data, frames, t, status) -> None:
            chunk = bytes(data)
            self.meter.update(np.frombuffer(chunk, dtype=np.int16))
            self._push(chunk)

        self._stream = sd.RawInputStream(
            samplerate=self.rate,
            channels=1,
            dtype="int16",
            blocksize=self._samples,
            callback=callback,
        )
        self._stream.start()
        log.info("microfone aberto: %s", sd.query_devices(kind="input")["name"])

    def stop(self) -> None:
        super().stop()
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass


def open_audio_source(source: tuple[str, int]) -> PcmTrack | None:
    """("none", 0) | ("system", 0) | ("app", pid)."""
    from . import winaudio

    kind, pid = source
    if kind == "app":
        return ProcessAudioTrack(pid, include=True)
    if kind == "system":
        # Todo o PC menos o próprio app: não retransmite a voz da sala.
        if winaudio.is_supported():
            try:
                return ProcessAudioTrack(os.getpid(), include=False)
            except Exception:  # noqa: BLE001
                log.warning("process loopback indisponível; usando loopback do PC inteiro", exc_info=True)
        return SystemAudioTrack()
    return None


# ---------------------------------------------------------------- áudio (reprodução)


class AudioPlayer:
    """Toca frames de áudio recebidos (s16 48 kHz estéreo) com controle de volume."""

    RATE = 48000
    CHANNELS = 2
    START_BUFFER = 0.08  # s
    MAX_BUFFER = 0.30
    BUFFER_STEP = 0.04

    def __init__(self) -> None:
        import sounddevice as sd

        self.volume = 1.0
        self.muted = False
        self._chunks: deque[np.ndarray] = deque()
        self._pending = 0
        self._primed = False
        # Folga antes de tocar: cresce a cada falta de áudio (rede instável) até o teto.
        self._target = self.START_BUFFER
        self._lock = threading.Lock()
        self._stream = sd.OutputStream(
            samplerate=self.RATE, channels=self.CHANNELS, dtype="int16", callback=self._callback, latency="low"
        )
        self._stream.start()
        self._resampler = av.AudioResampler(format="s16", layout="stereo", rate=self.RATE)
        self.meter = LevelMeter()

    def push(self, frame: av.AudioFrame) -> None:
        for out in self._resampler.resample(frame):
            pcm = out.to_ndarray().reshape(-1, self.CHANNELS)
            self.meter.update(pcm)
            with self._lock:
                self._chunks.append(pcm)
                self._pending += len(pcm)
                # Descarta atraso acumulado bem acima da folga desejada.
                while self._pending > self.RATE * (self._target + 0.25) and self._chunks:
                    self._pending -= len(self._chunks.popleft())

    def _callback(self, outdata, frames, time_info, status) -> None:
        outdata.fill(0)
        with self._lock:
            if not self._primed:
                if self._pending < self.RATE * self._target:
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
                self._target = min(self.MAX_BUFFER, self._target + self.BUFFER_STEP)
        vol = 0.0 if self.muted else self.volume
        if vol != 1.0:
            outdata[:] = (outdata * vol).astype(np.int16)

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass
