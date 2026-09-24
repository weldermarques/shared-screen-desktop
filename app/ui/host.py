from __future__ import annotations

import asyncio
import logging
from typing import Callable

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QMessageBox, QVBoxLayout, QWidget

from .. import config
from ..media import list_monitors
from ..rtc import HostSession, random_code
from .widgets import button, card, label

log = logging.getLogger(__name__)


class HostPage(QWidget):
    def __init__(self, on_back: Callable[[], None]) -> None:
        super().__init__()
        self.setObjectName("page")
        self._on_back = on_back
        self.code = random_code()
        self.session: HostSession | None = None
        self._audio_muted = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 24)

        top = QHBoxLayout()
        back = button("← Início", "link")
        back.clicked.connect(self._back)
        top.addWidget(back)
        top.addStretch()
        self.live_badge = label("● AO VIVO", "live")
        self.live_badge.hide()
        top.addWidget(self.live_badge)
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(16)
        root.addLayout(body, 1)

        panel, col = card()
        panel.setFixedWidth(370)
        body.addWidget(panel, 0, Qt.AlignmentFlag.AlignTop)

        col.addWidget(label("CÓDIGO DA SALA", "label"))
        col.addWidget(label(self.code, "code"))
        col.addWidget(label("LINK PARA OS ESPECTADORES", "label"))
        link_row = QHBoxLayout()
        self.link = QLineEdit(f"{config.WEB_URL}/r/{self.code}")
        self.link.setReadOnly(True)
        link_row.addWidget(self.link)
        self.copy_btn = button("Copiar", "secondary")
        self.copy_btn.clicked.connect(self._copy)
        link_row.addWidget(self.copy_btn)
        col.addLayout(link_row)
        col.addSpacing(6)

        col.addWidget(label("MONITOR", "label"))
        self.monitor_combo = QComboBox()
        for mon in list_monitors():
            self.monitor_combo.addItem(f"Monitor {mon['index']} — {mon['width']}×{mon['height']}", mon["index"])
        self.monitor_combo.currentIndexChanged.connect(self._monitor_changed)
        col.addWidget(self.monitor_combo)

        self.audio_check = QCheckBox("Transmitir o áudio do PC")
        self.audio_check.setChecked(True)
        col.addWidget(self.audio_check)
        col.addWidget(label("Envia tudo que toca no computador (filme, música, notificações).", "hint", wrap=True))

        stats = QHBoxLayout()
        self.viewers_lbl = label("", "stat")
        self.connected_lbl = label("", "stat")
        stats.addWidget(self.viewers_lbl)
        stats.addWidget(self.connected_lbl)
        col.addLayout(stats)
        self._set_stats(0, 0)

        audio_row = QHBoxLayout()
        self.audio_status = label("", "info")
        audio_row.addWidget(self.audio_status, 1)
        self.mute_btn = button("Mutar áudio", "secondary")
        self.mute_btn.clicked.connect(self._toggle_mute)
        audio_row.addWidget(self.mute_btn)
        self.audio_status.hide()
        self.mute_btn.hide()
        col.addLayout(audio_row)

        self.start_btn = button("Compartilhar minha tela", "big")
        self.start_btn.clicked.connect(self._start)
        col.addWidget(self.start_btn)
        self.stop_btn = button("Parar", "danger")
        self.stop_btn.clicked.connect(lambda: asyncio.ensure_future(self.stop()))
        self.stop_btn.hide()
        col.addWidget(self.stop_btn)
        self.error_lbl = label("", "error", wrap=True)
        self.error_lbl.hide()
        col.addWidget(self.error_lbl)

        self.preview = label("A pré-visualização aparece aqui", "muted")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(320, 180)
        self.preview.setStyleSheet("background: #000; border: 1px solid #2a3140; border-radius: 12px;")
        body.addWidget(self.preview, 1)

        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._update_preview)

    # --- ações ---

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.link.text())
        self.copy_btn.setText("Copiado!")
        QTimer.singleShot(1500, lambda: self.copy_btn.setText("Copiar"))

    def _back(self) -> None:
        async def go() -> None:
            await self.stop()
            self._on_back()

        asyncio.ensure_future(go())

    def _monitor_changed(self) -> None:
        if self.session:
            self.session.set_monitor(self.monitor_combo.currentData())

    def _start(self) -> None:
        asyncio.ensure_future(self._start_async())

    async def _start_async(self) -> None:
        self.error_lbl.hide()
        self.start_btn.setEnabled(False)
        self.start_btn.setText("Iniciando…")
        self.audio_check.setEnabled(False)
        session = HostSession(
            self.code,
            self.monitor_combo.currentData() or 1,
            self.audio_check.isChecked(),
            on_stats=self._set_stats,
        )
        try:
            await session.start()
        except Exception as exc:  # noqa: BLE001
            log.exception("falha ao iniciar transmissão")
            self.error_lbl.setText(f"Não foi possível iniciar: {exc}")
            self.error_lbl.show()
            self._set_idle()
            return
        self.session = session
        session.set_audio_muted(self._audio_muted)
        self.start_btn.hide()
        self.stop_btn.show()
        self.live_badge.show()
        self._refresh_audio()
        if self.audio_check.isChecked() and not session.has_audio:
            self.error_lbl.setText("Não foi possível capturar o áudio do PC; transmitindo só o vídeo.")
            self.error_lbl.show()
        self._preview_timer.start(500)

    async def stop(self) -> None:
        session, self.session = self.session, None
        self._preview_timer.stop()
        if session:
            await session.stop()
        self._set_idle()

    def _set_idle(self) -> None:
        self.start_btn.setText("Compartilhar minha tela")
        self.start_btn.setEnabled(True)
        self.start_btn.show()
        self.stop_btn.hide()
        self.live_badge.hide()
        self.audio_check.setEnabled(True)
        self.audio_status.hide()
        self.mute_btn.hide()
        self.preview.clear()
        self.preview.setText("A pré-visualização aparece aqui")
        self._set_stats(0, 0)

    def _toggle_mute(self) -> None:
        self._audio_muted = not self._audio_muted
        if self.session:
            self.session.set_audio_muted(self._audio_muted)
        self._refresh_audio()

    def _refresh_audio(self) -> None:
        has_audio = bool(self.session and self.session.has_audio)
        self.audio_status.setVisible(True)
        self.mute_btn.setVisible(has_audio)
        if not has_audio:
            text, style = "🔇 Sem áudio na transmissão", "info"
        elif self._audio_muted:
            text, style = "🔇 Áudio do PC mutado", "info"
        else:
            text, style = "🔊 Enviando o áudio do PC", "warn"
        self.audio_status.setText(text)
        self.audio_status.setObjectName(style)
        self.audio_status.style().unpolish(self.audio_status)
        self.audio_status.style().polish(self.audio_status)
        self.mute_btn.setText("Ativar áudio" if self._audio_muted else "Mutar áudio")

    def _set_stats(self, viewers: int, connected: int) -> None:
        self.viewers_lbl.setText(f"<b style='font-size:16pt'>{viewers}</b><br><span style='color:#8b93a5'>na sala</span>")
        self.connected_lbl.setText(
            f"<b style='font-size:16pt'>{connected}</b><br><span style='color:#8b93a5'>assistindo</span>"
        )

    def _update_preview(self) -> None:
        track = self.session.video if self.session else None
        img = getattr(track, "_latest", None)
        if img is None:
            return
        step = max(1, img.shape[1] // 960)
        small = np.ascontiguousarray(img[::step, ::step, :3])
        h, w = small.shape[:2]
        qimg = QImage(small.data, w, h, w * 3, QImage.Format.Format_BGR888)
        pix = QPixmap.fromImage(qimg).scaled(
            self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self.preview.setPixmap(pix)

    def confirm_leave(self) -> bool:
        if not self.session:
            return True
        answer = QMessageBox.question(self, "Encerrar transmissão", "Você está transmitindo. Deseja encerrar?")
        return answer == QMessageBox.StandardButton.Yes
