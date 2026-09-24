from __future__ import annotations

import asyncio
import logging
from typing import Callable

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLineEdit, QMessageBox, QScrollArea, QVBoxLayout, QWidget

from .. import config, winaudio, winwindows
from ..media import list_monitors
from ..rtc import HostSession, random_code
from .voice import VoiceControls
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
        self._audio_label = ""

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
        # Em janela baixa o painel rola em vez de espremer os botões.
        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(382)
        scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }"
            "QScrollBar::handle:vertical { background: #2a3140; border-radius: 4px; min-height: 30px; }"
            "QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page"
            " { background: none; height: 0; }"
        )
        body.addWidget(scroll)

        col.addWidget(label("CÓDIGO DA SALA", "label"))
        col.addWidget(label(self.code, "code"))
        col.addWidget(label("LINK PARA OS ESPECTADORES", "label"))
        link_row = QHBoxLayout()
        self.link = QLineEdit(f"{config.WEB_URL}/r/{self.code}")
        self.link.setReadOnly(True)
        link_row.addWidget(self.link)
        self.copy_btn = button("📋", "secondary")
        self.copy_btn.setFixedWidth(44)
        self.copy_btn.setStyleSheet("padding: 6px 0; font-size: 12pt;")
        self.copy_btn.setToolTip("Copiar link")
        self.copy_btn.clicked.connect(self._copy)
        link_row.addWidget(self.copy_btn)
        col.addLayout(link_row)
        col.addSpacing(6)

        col.addWidget(label("O QUE COMPARTILHAR", "label"))
        source_row = QHBoxLayout()
        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(0)
        self.source_combo.activated.connect(self._source_changed)
        source_row.addWidget(self.source_combo, 1)
        refresh = button("🔄", "secondary")
        refresh.setFixedWidth(40)
        refresh.setStyleSheet("padding: 6px 0; font-size: 12pt;")
        refresh.setToolTip("Atualizar a lista de janelas e aplicativos")
        refresh.clicked.connect(self._fill_sources)
        source_row.addWidget(refresh)
        col.addLayout(source_row)

        col.addWidget(label("ÁUDIO", "label"))
        self.audio_combo = QComboBox()
        self.audio_combo.setMinimumWidth(0)
        self.audio_combo.currentIndexChanged.connect(self._audio_hint_changed)
        col.addWidget(self.audio_combo)
        self.audio_hint = label("", "hint", wrap=True)
        col.addWidget(self.audio_hint)
        self._fill_sources()

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

        col.addSpacing(6)
        col.addWidget(label("VOZ DA SALA", "label"))
        self.voice = VoiceControls(self.code, two_rows=True)
        col.addWidget(self.voice)
        col.addWidget(label("Converse com quem está na sala (app ou navegador). Use fone para não dar eco.", "hint", wrap=True))

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
        self.copy_btn.setText("✅")
        self.copy_btn.setToolTip("Copiado!")
        QTimer.singleShot(1500, self._reset_copy)

    def _reset_copy(self) -> None:
        self.copy_btn.setText("📋")
        self.copy_btn.setToolTip("Copiar link")

    def _back(self) -> None:
        async def go() -> None:
            await self.leave()
            self._on_back()

        asyncio.ensure_future(go())

    def _fill_sources(self) -> None:
        """Monitores + janelas abertas; e as fontes de áudio (PC inteiro / um app / nenhuma)."""
        current_video = self.source_combo.currentData()
        current_audio = self.audio_combo.currentData()
        windows = winwindows.list_windows()

        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for mon in list_monitors():
            self.source_combo.addItem(
                f"🖥️ Monitor {mon['index']} — {mon['width']}×{mon['height']}", ("monitor", mon["index"])
            )
        for win in windows:
            self.source_combo.addItem(f"🪟 {_short(win['title'])}  ({win['exe']})", ("window", win["hwnd"]))
            self.source_combo.setItemData(self.source_combo.count() - 1, win["title"], Qt.ItemDataRole.ToolTipRole)
        index = self.source_combo.findData(current_video)
        self.source_combo.setCurrentIndex(max(0, index))
        self.source_combo.blockSignals(False)

        self.audio_combo.blockSignals(True)
        self.audio_combo.clear()
        self.audio_combo.addItem("🔊 Todo o PC", ("system", 0))
        if winaudio.is_supported():
            for app in winwindows.list_audio_apps():
                self.audio_combo.addItem(f"🎯 Só {app['exe']} — {_short(app['title'], 32)}", ("app", app["pid"]))
        self.audio_combo.addItem("🔇 Sem áudio", ("none", 0))
        index = self.audio_combo.findData(current_audio)
        self.audio_combo.setCurrentIndex(max(0, index))
        self.audio_combo.blockSignals(False)
        self._audio_hint_changed()
        self._windows = {w["hwnd"]: w for w in windows}

    def _source_changed(self) -> None:
        source = self.source_combo.currentData()
        if not source:
            return
        if self.session:
            self.session.set_video_source(source)
            return
        # Escolheu uma janela: por padrão manda só o som daquele aplicativo.
        win = self._windows.get(source[1]) if source[0] == "window" else None
        if win and self.audio_combo.currentData() != ("none", 0):
            index = self.audio_combo.findData(("app", win["root_pid"]))
            if index >= 0:
                self.audio_combo.setCurrentIndex(index)

    def _audio_hint_changed(self) -> None:
        kind = (self.audio_combo.currentData() or ("none", 0))[0]
        if kind == "system":
            text = "Tudo que toca no computador (filme, música, notificações). A voz da sala não vai junto."
            if not winaudio.is_supported():
                text = "Tudo que toca no computador. Para escolher um aplicativo só, é preciso Windows 10 (2004) ou mais novo."
        elif kind == "app":
            text = "Só o som deste aplicativo (ex.: só o navegador). O resto do PC fica de fora."
        else:
            text = "A transmissão vai sem som."
        self.audio_hint.setText(text)

    def _start(self) -> None:
        asyncio.ensure_future(self._start_async())

    async def _start_async(self) -> None:
        self.error_lbl.hide()
        self.start_btn.setEnabled(False)
        self.start_btn.setText("Iniciando…")
        self.audio_combo.setEnabled(False)
        audio_source = self.audio_combo.currentData() or ("none", 0)
        self._audio_label = self.audio_combo.currentText().split(" — ")[0].replace("🎯 Só ", "")
        session = HostSession(
            self.code,
            self.source_combo.currentData() or ("monitor", 1),
            audio_source,
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
        if audio_source[0] != "none" and not session.has_audio:
            self.error_lbl.setText("Não foi possível capturar o áudio; transmitindo só o vídeo.")
            self.error_lbl.show()
        self._preview_timer.start(500)

    async def leave(self) -> None:
        """Sai da página: encerra a transmissão e a voz."""
        await self.stop()
        await self.voice.stop()

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
        self.audio_combo.setEnabled(True)
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
        what = f"de {self._audio_label}" if self.session and self.session.audio_source[0] == "app" else "do PC"
        if not has_audio:
            text, style = "🔇 Sem áudio na transmissão", "info"
        elif self._audio_muted:
            text, style = f"🔇 Áudio {what} mutado", "info"
        else:
            text, style = f"🔊 Enviando o áudio {what}", "warn"
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


def _short(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
