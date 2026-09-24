from __future__ import annotations

import asyncio
import logging
from typing import Callable

import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSlider, QVBoxLayout, QWidget

from ..rtc import ViewerSession
from .voice import VoiceControls
from .widgets import button, label

log = logging.getLogger(__name__)

STATUS_TEXT = {
    "connecting": "Entrando na sala…",
    "waiting": "Aguardando o apresentador iniciar o compartilhamento…",
    "negotiating": "Conectando à transmissão…",
    "watching": "",
    "ended": "A transmissão foi encerrada.",
    "error": "Não foi possível conectar.",
}


class VideoWidget(QWidget):
    def __init__(self, on_double_click: Callable[[], None]) -> None:
        super().__init__()
        self._image: QImage | None = None
        self._message = ""
        self._on_double_click = on_double_click
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(320, 180)

    def set_frame(self, rgb: np.ndarray) -> None:
        h, w = rgb.shape[:2]
        self._image = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
        self.update()

    def clear(self) -> None:
        self._image = None
        self.update()

    def set_message(self, text: str) -> None:
        self._message = text
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._image is not None and not self._message:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            size = self._image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x = (self.width() - size.width()) // 2
            y = (self.height() - size.height()) // 2
            p.drawImage(QRect(x, y, size.width(), size.height()), self._image)
        if self._message:
            p.setPen(Qt.GlobalColor.gray)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._message)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self._on_double_click()


class ViewerPage(QWidget):
    def __init__(self, code: str, on_back: Callable[[], None], on_fullscreen: Callable[[], None]) -> None:
        super().__init__()
        self.setObjectName("page")
        self.code = code
        self._on_back = on_back
        self._muted = False
        self._has_audio = False
        self.session = ViewerSession(code, self._set_status, self._on_frame, self._on_audio)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.video = VideoWidget(on_fullscreen)
        root.addWidget(self.video, 1)

        self.bar = QFrame()
        self.bar.setObjectName("bar")
        bar = QHBoxLayout(self.bar)
        bar.setContentsMargins(16, 8, 16, 8)
        back = button("← Sair", "link")
        back.clicked.connect(self._back)
        bar.addWidget(back)
        bar.addStretch()
        bar.addWidget(label(f"Sala <b>{code}</b>", "muted"))
        bar.addStretch()
        self.voice = VoiceControls(code)
        bar.addWidget(self.voice)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #2a3140;")
        bar.addSpacing(8)
        bar.addWidget(sep)
        bar.addSpacing(8)
        self.stream_lbl = label("Transmissão", "hint")
        bar.addWidget(self.stream_lbl)

        self.mute_btn = button("🔊", "secondary")
        self.mute_btn.setFixedWidth(44)
        self.mute_btn.setStyleSheet("padding: 6px 0; font-size: 12pt;")
        self.mute_btn.clicked.connect(self._toggle_mute)
        bar.addWidget(self.mute_btn)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(100)
        self.volume.setFixedWidth(120)
        self.volume.setToolTip("Volume da transmissão")
        self.volume.valueChanged.connect(self._volume_changed)
        bar.addWidget(self.volume)
        self.no_audio = label("Sem áudio", "hint")
        bar.addWidget(self.no_audio)
        self.fs_btn = button("⛶ Tela cheia", "secondary")
        self.fs_btn.clicked.connect(on_fullscreen)
        bar.addWidget(self.fs_btn)
        root.addWidget(self.bar)

        self._on_audio(False)
        self._set_status("connecting")

    async def start(self) -> None:
        try:
            await self.session.start()
        except Exception as exc:  # noqa: BLE001
            log.exception("falha ao entrar na sala")
            self._set_status("error")
            self.video.set_message(f"Não foi possível conectar.\n{exc}")

    async def stop(self) -> None:
        await self.session.close()
        await self.voice.stop()

    def set_fullscreen_ui(self, fullscreen: bool) -> None:
        self.bar.setVisible(not fullscreen)

    # --- callbacks da sessão ---

    def _set_status(self, status: str) -> None:
        self.video.set_message(STATUS_TEXT.get(status, ""))
        if status != "watching":
            self.video.clear()
        self._on_audio(self._has_audio)

    def _on_frame(self, rgb: np.ndarray) -> None:
        self.video.set_frame(rgb)

    def _on_audio(self, has_audio: bool) -> None:
        self._has_audio = has_audio
        self.mute_btn.setVisible(has_audio)
        self.volume.setVisible(has_audio)
        self.stream_lbl.setVisible(has_audio)
        self.no_audio.setVisible(not has_audio and self.session.status == "watching")

    # --- controles ---

    def _back(self) -> None:
        async def go() -> None:
            await self.stop()
            self._on_back()

        asyncio.ensure_future(go())

    def _toggle_mute(self) -> None:
        self._muted = not self._muted
        if not self._muted and self.volume.value() == 0:
            self.volume.setValue(50)
        self._apply_volume()

    def _volume_changed(self, value: int) -> None:
        self._muted = value == 0
        self._apply_volume()

    def _apply_volume(self) -> None:
        vol = self.volume.value() / 100
        self.session.set_volume(vol, self._muted)
        self.mute_btn.setText("🔇" if self._muted or vol == 0 else "🔉" if vol < 0.5 else "🔊")
