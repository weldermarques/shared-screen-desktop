from __future__ import annotations

import asyncio
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QSlider, QVBoxLayout, QWidget

from ..voice import VoiceSession
from .widgets import button, label

log = logging.getLogger(__name__)


class VoiceControls(QWidget):
    """Entrar/sair da voz da sala, mutar o microfone e o volume das vozes."""

    def __init__(self, code: str, two_rows: bool = False) -> None:
        """``two_rows``: botões numa linha e contagem/volume embaixo (painel estreito)."""
        super().__init__()
        self.code = code
        self.session: VoiceSession | None = None
        self._mic_muted = False
        self._busy = False
        self._compact = not two_rows

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        outer.addLayout(row)
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        if two_rows:
            outer.addLayout(row2)
        else:
            row2 = row

        self.join_btn = button("🎙️ Entrar na voz", "secondary")
        self.join_btn.setToolTip("Conversa por voz com todos na sala. Use fone de ouvido para não dar eco.")
        self.join_btn.clicked.connect(lambda: asyncio.ensure_future(self._join()))
        row.addWidget(self.join_btn)

        self.mic_btn = button("", "secondary")
        self.mic_btn.clicked.connect(self._toggle_mic)
        if self._compact:
            self.mic_btn.setFixedWidth(44)
            self.mic_btn.setStyleSheet("padding: 6px 0; font-size: 12pt;")
        row.addWidget(self.mic_btn)
        self.leave_btn = button("Sair da voz", "link")
        self.leave_btn.clicked.connect(lambda: asyncio.ensure_future(self.stop()))
        self.count_lbl = label("", "muted")
        if two_rows:
            row.addWidget(self.leave_btn)
            row.addStretch()
        row2.addWidget(self.count_lbl)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(100)
        self.volume.setFixedWidth(80)
        self.volume.setToolTip("Volume das vozes")
        self.volume.valueChanged.connect(self._apply_volume)
        row2.addWidget(self.volume)
        if not two_rows:
            row.addWidget(self.leave_btn)

        self.error_lbl = label("", "error", wrap=two_rows)
        self.error_lbl.hide()
        if two_rows:
            outer.addWidget(self.error_lbl)
        else:
            row.addWidget(self.error_lbl)
        row.addStretch()
        if two_rows:
            row2.addStretch()
        self._refresh()

    async def _join(self) -> None:
        if self._busy or self.session:
            return
        self._busy = True
        self.error_lbl.hide()
        self.join_btn.setEnabled(False)
        self.join_btn.setText("Entrando…")
        session = VoiceSession(self.code, self._on_change)
        session.set_mic_muted(self._mic_muted)
        try:
            await session.start()
        except Exception as exc:  # noqa: BLE001
            log.exception("falha ao entrar na voz")
            self.error_lbl.setText(f"Sem voz: {exc}")
            self.error_lbl.setToolTip(str(exc))
            self.error_lbl.show()
        else:
            self.session = session
            self._apply_volume()
        self._busy = False
        self._refresh()

    async def stop(self) -> None:
        session, self.session = self.session, None
        if session:
            await session.stop()
        self._refresh()

    def _on_change(self, connected: int, present: int) -> None:
        if not self.session:
            return
        if present == 0:
            text = "só você na voz"
        else:
            text = f"{present + 1} na voz"
            if connected < present:
                text += " (conectando…)"
        self.count_lbl.setText(text)

    def _toggle_mic(self) -> None:
        self._mic_muted = not self._mic_muted
        if self.session:
            self.session.set_mic_muted(self._mic_muted)
        self._refresh()

    def _apply_volume(self) -> None:
        if self.session:
            vol = self.volume.value() / 100
            self.session.set_volume(vol, vol == 0)

    def _refresh(self) -> None:
        joined = self.session is not None
        self.join_btn.setVisible(not joined)
        self.join_btn.setEnabled(True)
        self.join_btn.setText("🎙️ Entrar na voz")
        for w in (self.mic_btn, self.count_lbl, self.volume, self.leave_btn):
            w.setVisible(joined)
        if joined:
            self.error_lbl.hide()
            if self._compact:
                self.mic_btn.setText("🔇" if self._mic_muted else "🎙️")
                self.mic_btn.setToolTip("Ativar microfone" if self._mic_muted else "Mutar microfone")
            else:
                self.mic_btn.setText("🔇 Mic mutado" if self._mic_muted else "🎙️ Mutar mic")
            if not self.count_lbl.text():
                self.count_lbl.setText("só você na voz")
        else:
            self.count_lbl.setText("")
