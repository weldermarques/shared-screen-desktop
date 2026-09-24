"""Sala única e permanente: quem abre o app já está nela.

Qualquer pessoa pode compartilhar (uma por vez: quem começa por último assume) e todos
assistem e conversam por voz. Não há "dono" da sala.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

import numpy as np
from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .. import config, winaudio, winwindows
from ..media import list_monitors
from ..rtc import HostSession, ViewerSession
from .voice import VoiceControls
from .widgets import button, card, label

log = logging.getLogger(__name__)

STATUS_TEXT = {
    "connecting": "Entrando na sala…",
    "waiting": "Ninguém está compartilhando a tela agora.",
    "negotiating": "Conectando à transmissão…",
    "watching": "",
    "ended": "Ninguém está compartilhando a tela agora.",
    "error": "Não foi possível conectar à sala.",
}

SCROLL_STYLE = (
    "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
    "QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }"
    "QScrollBar::handle:vertical { background: #2a3140; border-radius: 4px; min-height: 30px; }"
    "QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page"
    " { background: none; height: 0; }"
)
ICON_BUTTON_STYLE = "padding: 6px 0; font-size: 12pt;"


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


class RoomPage(QWidget):
    def __init__(self, on_fullscreen: Callable[[], None]) -> None:
        super().__init__()
        self.setObjectName("page")
        self.code = config.ROOM_CODE
        self.host: HostSession | None = None
        self.viewer: ViewerSession | None = None
        self._audio_muted = False
        self._audio_label = ""
        self._stream_muted = False
        self._stream_has_audio = False
        self._closed = False
        self._windows: dict[int, dict] = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- palco: vídeo de quem compartilha (ou a sua pré-visualização) + barra
        stage = QVBoxLayout()
        stage.setSpacing(0)
        root.addLayout(stage, 1)
        self.video = VideoWidget(on_fullscreen)
        stage.addWidget(self.video, 1)

        self.bar = QFrame()
        self.bar.setObjectName("bar")
        bar = QHBoxLayout(self.bar)
        bar.setContentsMargins(16, 8, 16, 8)
        self.live_badge = label("● VOCÊ ESTÁ COMPARTILHANDO", "live")
        bar.addWidget(self.live_badge)
        bar.addStretch()
        self.stream_lbl = label("Transmissão", "hint")
        bar.addWidget(self.stream_lbl)
        self.stream_mute_btn = button("🔊", "secondary")
        self.stream_mute_btn.setFixedWidth(44)
        self.stream_mute_btn.setStyleSheet(ICON_BUTTON_STYLE)
        self.stream_mute_btn.clicked.connect(self._toggle_stream_mute)
        bar.addWidget(self.stream_mute_btn)
        self.stream_volume = QSlider(Qt.Orientation.Horizontal)
        self.stream_volume.setRange(0, 100)
        self.stream_volume.setValue(100)
        self.stream_volume.setFixedWidth(120)
        self.stream_volume.setToolTip("Volume da transmissão")
        self.stream_volume.valueChanged.connect(self._stream_volume_changed)
        bar.addWidget(self.stream_volume)
        self.fs_btn = button("⛶ Tela cheia", "secondary")
        self.fs_btn.clicked.connect(on_fullscreen)
        bar.addWidget(self.fs_btn)
        stage.addWidget(self.bar)

        # --- painel lateral
        panel, col = card()
        panel.setFixedWidth(370)
        self.side = QScrollArea()
        self.side.setWidget(panel)
        self.side.setWidgetResizable(True)
        self.side.setFrameShape(QFrame.Shape.NoFrame)
        self.side.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.side.setFixedWidth(398)
        self.side.setStyleSheet(SCROLL_STYLE)
        self.side.setContentsMargins(0, 0, 0, 0)
        side_wrap = QVBoxLayout()
        side_wrap.setContentsMargins(12, 12, 16, 12)
        side_wrap.addWidget(self.side)
        root.addLayout(side_wrap)
        self._side_wrap = side_wrap

        self.link = f"{config.WEB_URL}/r/{self.code}"
        title_row = QHBoxLayout()
        title_row.addWidget(label(f"Sala {self.code}", "h2"))
        self.copy_btn = button("📎", "link")
        self.copy_btn.setStyleSheet("font-size: 14pt; padding: 0 4px;")
        self.copy_btn.clicked.connect(self._copy)
        title_row.addWidget(self.copy_btn)
        title_row.addStretch()
        col.addLayout(title_row)
        self._reset_copy()

        col.addSpacing(10)
        col.addWidget(label("NA SALA", "label"))
        self.voice = VoiceControls(self.code, two_rows=True, on_people=self._refresh_share_status)
        col.addWidget(self.voice)
        col.addWidget(label("Use fone de ouvido para não dar eco.", "hint", wrap=True))

        col.addSpacing(10)
        col.addWidget(label("COMPARTILHAR A TELA", "label"))
        self.share_status = label("", "info", wrap=True)
        col.addWidget(self.share_status)

        col.addWidget(label("O que compartilhar", "muted"))
        source_row = QHBoxLayout()
        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(0)
        self.source_combo.activated.connect(self._source_changed)
        source_row.addWidget(self.source_combo, 1)
        refresh = button("🔄", "secondary")
        refresh.setFixedWidth(40)
        refresh.setStyleSheet(ICON_BUTTON_STYLE)
        refresh.setToolTip("Atualizar a lista de janelas e aplicativos")
        refresh.clicked.connect(self._fill_sources)
        source_row.addWidget(refresh)
        col.addLayout(source_row)

        self.audio_title = label("Áudio", "muted")
        col.addWidget(self.audio_title)
        self.audio_combo = QComboBox()
        self.audio_combo.setMinimumWidth(0)
        self.audio_combo.currentIndexChanged.connect(self._audio_hint_changed)
        col.addWidget(self.audio_combo)
        self.audio_hint = label("", "hint", wrap=True)
        col.addWidget(self.audio_hint)
        self._fill_sources()

        audio_row = QHBoxLayout()
        self.audio_status = label("", "info")
        audio_row.addWidget(self.audio_status, 1)
        self.mute_btn = button("Mutar áudio", "secondary")
        self.mute_btn.clicked.connect(self._toggle_mute)
        audio_row.addWidget(self.mute_btn)
        col.addLayout(audio_row)
        self.watchers_lbl = label("", "muted")
        col.addWidget(self.watchers_lbl)

        self.start_btn = button("Compartilhar minha tela", "big")
        self.start_btn.clicked.connect(lambda: asyncio.ensure_future(self._start_sharing()))
        col.addWidget(self.start_btn)
        self.stop_btn = button("Parar de compartilhar", "danger")
        self.stop_btn.clicked.connect(lambda: asyncio.ensure_future(self.stop_sharing()))
        col.addWidget(self.stop_btn)
        self.error_lbl = label("", "error", wrap=True)
        self.error_lbl.hide()
        col.addWidget(self.error_lbl)
        col.addStretch()

        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._update_preview)
        self._set_idle()
        self._viewer_status("connecting")

    # ------------------------------------------------------------ ciclo de vida

    async def start(self) -> None:
        """Entra na sala: assiste a quem estiver compartilhando e entra na voz (mic mutado)."""
        await asyncio.gather(self._start_viewer(), self.voice.join(mic_muted=True))

    async def leave(self) -> None:
        self._closed = True
        await self.stop_sharing(restart_viewer=False)
        await self._stop_viewer()
        await self.voice.stop()

    def set_fullscreen_ui(self, fullscreen: bool) -> None:
        self.bar.setVisible(not fullscreen)
        self.side.setVisible(not fullscreen)
        m = 0 if fullscreen else 12
        self._side_wrap.setContentsMargins(0 if fullscreen else 12, m, 0 if fullscreen else 16, m)

    def confirm_leave(self) -> bool:
        if not self.host:
            return True
        answer = QMessageBox.question(self, "Sair", "Você está compartilhando a tela. Deseja sair mesmo assim?")
        return answer == QMessageBox.StandardButton.Yes

    # ------------------------------------------------------------ assistir

    async def _start_viewer(self) -> None:
        if self._closed or self.viewer:
            return
        viewer = ViewerSession(self.code, self._viewer_status, self._on_frame, self._on_stream_audio)
        self.viewer = viewer
        self._apply_stream_volume()
        try:
            await viewer.start()
        except Exception as exc:  # noqa: BLE001
            log.exception("falha ao entrar na sala")
            if self.viewer is viewer:
                self._viewer_status("error")
                self.video.set_message(f"Não foi possível conectar à sala.\n{exc}")

    async def _stop_viewer(self) -> None:
        viewer, self.viewer = self.viewer, None
        if viewer:
            await viewer.close()
        self._on_stream_audio(False)

    def _viewer_status(self, status: str) -> None:
        if self.host:
            return  # mostrando a própria pré-visualização
        self.video.set_message(STATUS_TEXT.get(status, ""))
        if status != "watching":
            self.video.clear()
        self._refresh_share_status()
        self._on_stream_audio(self._stream_has_audio)

    def _on_frame(self, rgb: np.ndarray) -> None:
        if not self.host:
            self.video.set_frame(rgb)

    def _on_stream_audio(self, has_audio: bool) -> None:
        self._stream_has_audio = has_audio
        watching = bool(self.viewer and self.viewer.status == "watching" and not self.host)
        for w in (self.stream_lbl, self.stream_mute_btn, self.stream_volume):
            w.setVisible(watching and has_audio)
        self.fs_btn.setVisible(watching or bool(self.host))

    def _toggle_stream_mute(self) -> None:
        self._stream_muted = not self._stream_muted
        if not self._stream_muted and self.stream_volume.value() == 0:
            self.stream_volume.setValue(50)
        self._apply_stream_volume()

    def _stream_volume_changed(self, value: int) -> None:
        self._stream_muted = value == 0
        self._apply_stream_volume()

    def _apply_stream_volume(self) -> None:
        vol = self.stream_volume.value() / 100
        if self.viewer:
            self.viewer.set_volume(vol, self._stream_muted)
        self.stream_mute_btn.setText("🔇" if self._stream_muted or vol == 0 else "🔉" if vol < 0.5 else "🔊")

    # ------------------------------------------------------------ compartilhar

    async def _start_sharing(self) -> None:
        self.error_lbl.hide()
        self.start_btn.setEnabled(False)
        self.start_btn.setText("Iniciando…")
        audio_source = self.audio_combo.currentData() or ("none", 0)
        self._audio_label = self.audio_combo.currentText().split(" — ")[0].replace("🎯 Só ", "")
        # Quem compartilha não assiste a si mesmo (e não ouviria o próprio áudio de volta).
        await self._stop_viewer()
        session = HostSession(
            self.code,
            self.source_combo.currentData() or ("monitor", 1),
            audio_source,
            on_stats=self._set_stats,
            on_replaced=self._replaced,
        )
        try:
            await session.start()
        except Exception as exc:  # noqa: BLE001
            log.exception("falha ao iniciar transmissão")
            self.error_lbl.setText(f"Não foi possível compartilhar: {exc}")
            self.error_lbl.show()
            self._set_idle()
            await self._start_viewer()
            return
        self.host = session
        session.set_audio_muted(self._audio_muted)
        self.voice.set_sharing(True)
        self._set_sharing()
        if audio_source[0] != "none" and not session.has_audio:
            self.error_lbl.setText("Não foi possível capturar o áudio; compartilhando só o vídeo.")
            self.error_lbl.show()

    async def stop_sharing(self, restart_viewer: bool = True, notify: bool = True) -> None:
        session, self.host = self.host, None
        self._preview_timer.stop()
        self.voice.set_sharing(False)
        if session:
            await session.stop(notify=notify)
        self._set_idle()
        if restart_viewer and not self._closed:
            self.video.clear()
            await self._start_viewer()

    def _replaced(self) -> None:
        # Outra pessoa começou a compartilhar: sai sem avisar "parou" (quem assiste já trocou).
        log.info("outra pessoa assumiu o compartilhamento")
        asyncio.ensure_future(self.stop_sharing(notify=False))
        self.error_lbl.setText("Outra pessoa começou a compartilhar, então sua transmissão foi encerrada.")
        self.error_lbl.show()

    def _set_sharing(self) -> None:
        self.start_btn.hide()
        self.stop_btn.show()
        self.live_badge.show()
        self.watchers_lbl.show()
        # O áudio não muda durante a transmissão; a linha de status já mostra o que está indo.
        for w in (self.audio_title, self.audio_combo, self.audio_hint):
            w.hide()
        self.video.set_message("")
        self._refresh_audio()
        self._refresh_share_status()
        self._on_stream_audio(False)
        self._preview_timer.start(100)

    def _set_idle(self) -> None:
        self.start_btn.setText("Compartilhar minha tela")
        self.start_btn.setEnabled(True)
        self.start_btn.show()
        self.stop_btn.hide()
        self.live_badge.hide()
        self.watchers_lbl.hide()
        for w in (self.audio_title, self.audio_combo, self.audio_hint):
            w.show()
        self.audio_status.hide()
        self.mute_btn.hide()
        self._set_stats(0, 0)
        self._refresh_share_status()

    def _refresh_share_status(self) -> None:
        if self.host:
            text = "Você está compartilhando. Todos na sala estão vendo sua tela."
        elif self.viewer and self.viewer.status in ("watching", "negotiating"):
            who = self.voice.sharer_name() or "Alguém"
            text = f"{who} está compartilhando agora. Se você compartilhar, passa a ser a sua tela."
        else:
            text = "Ninguém está compartilhando. Clique abaixo para mostrar sua tela para a sala."
        self.share_status.setText(text)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.link)
        self.copy_btn.setText("✅")
        self.copy_btn.setToolTip("Copiado!")
        QTimer.singleShot(1500, self._reset_copy)

    def _reset_copy(self) -> None:
        self.copy_btn.setText("📎")
        self.copy_btn.setToolTip(f"Copiar o link para assistir pelo navegador: {self.link}")

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
        if self.host:
            self.host.set_video_source(source)
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

    def _toggle_mute(self) -> None:
        self._audio_muted = not self._audio_muted
        if self.host:
            self.host.set_audio_muted(self._audio_muted)
        self._refresh_audio()

    def _refresh_audio(self) -> None:
        has_audio = bool(self.host and self.host.has_audio)
        self.audio_status.setVisible(True)
        self.mute_btn.setVisible(has_audio)
        what = f"de {self._audio_label}" if self.host and self.host.audio_source[0] == "app" else "do PC"
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
        self.watchers_lbl.setText(f"👀 {connected} assistindo")

    def _update_preview(self) -> None:
        track = self.host.video if self.host else None
        img = getattr(track, "_latest", None)
        if img is None:
            return
        step = max(1, img.shape[1] // 1280)
        # BGRA -> RGB, reduzido: só uma pré-visualização.
        self.video.set_frame(np.ascontiguousarray(img[::step, ::step, 2::-1]))


def _short(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
