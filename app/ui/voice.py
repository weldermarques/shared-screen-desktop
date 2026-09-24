from __future__ import annotations

import asyncio
import logging

import hashlib

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QInputDialog, QLabel, QSlider, QVBoxLayout, QWidget

from .. import settings
from ..voice import VoiceSession
from .widgets import button, label

log = logging.getLogger(__name__)


class VoiceControls(QWidget):
    """Entrar/sair da voz da sala, mutar o microfone e o volume das vozes."""

    def __init__(self, code: str, two_rows: bool = False, on_people=None) -> None:
        """``two_rows``: botões numa linha e contagem/volume embaixo (painel estreito).
        ``on_people()``: chamado quando a lista de pessoas muda."""
        super().__init__()
        self._on_people = on_people or (lambda: None)
        self.code = code
        self.session: VoiceSession | None = None
        self._mic_muted = False
        self._busy = False
        self._compact = not two_rows
        self._sharing = False
        self._name = settings.get_name()

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
        self.join_btn.clicked.connect(lambda: asyncio.ensure_future(self.join()))
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
        self.volume_icon = label("🔈", "muted")
        self.volume_icon.setToolTip("Volume das vozes")
        row2.addWidget(self.volume_icon)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(100)
        self.volume.setFixedWidth(80)
        self.volume.setToolTip("Volume das vozes")
        self.volume.valueChanged.connect(self._apply_volume)
        row2.addWidget(self.volume)
        if not two_rows:
            row.addWidget(self.leave_btn)

        self.people = PeopleList(on_rename=self._rename)
        # Anel de "falando" nos avatares: mede o volume localmente, sem rede.
        self._speaking_timer = QTimer(self)
        self._speaking_timer.timeout.connect(
            lambda: self.session and self.people.set_speaking(self.session.speaking())
        )
        if two_rows:
            outer.addWidget(self.people)

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

    async def join(self, mic_muted: bool | None = None) -> None:
        if self._busy or self.session:
            return
        if mic_muted is not None:
            self._mic_muted = mic_muted
        self._busy = True
        self.error_lbl.hide()
        self.join_btn.setEnabled(False)
        self.join_btn.setText("Entrando…")
        session = VoiceSession(self.code, self._on_change, name=self._name)
        session.set_mic_muted(self._mic_muted)
        session.set_sharing(self._sharing)
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

    def sharer_name(self) -> str | None:
        """Nome de outra pessoa que está compartilhando, se houver."""
        for person in self.session.people() if self.session else []:
            if person["sharing"] and not person["me"]:
                return person["name"]
        return None

    def set_sharing(self, sharing: bool) -> None:
        """Marca você como "compartilhando" na lista de todos."""
        self._sharing = sharing
        if self.session:
            self.session.set_sharing(sharing)

    def _rename(self) -> None:
        name, ok = QInputDialog.getText(self, "Seu nome", "Como você aparece na sala:", text=self._name)
        if ok and name.strip():
            self._name = settings.set_name(name)
            if self.session:
                self.session.set_name(self._name)

    def _on_change(self, connected: int, present: int) -> None:
        if not self.session:
            return
        self.people.set_people(self.session.people())
        self._on_people()
        if not self._compact:
            return  # no painel a lista de pessoas já mostra quem está na voz
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
        for w in (self.mic_btn, self.count_lbl, self.volume, self.volume_icon, self.leave_btn, self.people):
            w.setVisible(joined)
        if joined:
            self.people.set_people(self.session.people())
            self._speaking_timer.start(100)
        else:
            self._speaking_timer.stop()
        if joined:
            self.error_lbl.hide()
            if self._compact:
                self.mic_btn.setText("🔇" if self._mic_muted else "🎙️")
                self.mic_btn.setToolTip("Ativar microfone" if self._mic_muted else "Mutar microfone")
            else:
                self.mic_btn.setText("🔇 Mic mutado" if self._mic_muted else "🎙️ Mutar mic")
            if self._compact and not self.count_lbl.text():
                self.count_lbl.setText("só você na voz")
        else:
            self.count_lbl.setText("")


# ---------------------------------------------------------------- lista de pessoas

AVATAR_COLORS = ["#5b8cff", "#e0679a", "#43b581", "#f0a04b", "#9b6bff", "#2fb3c7", "#e05d5d", "#8a9a3c"]


def initials(name: str) -> str:
    words = [w for w in name.replace("_", " ").replace(".", " ").split() if w]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return (name[:2] or "?").upper()


SPEAKING_COLOR = "#43d17a"
AVATAR_SIZE = 32
RING = 3  # espaço reservado para o anel: o avatar não muda de tamanho ao falar


def avatar(name: str, size: int = AVATAR_SIZE, dpr: float = 1.0, speaking: bool = False) -> QPixmap:
    """Círculo com as iniciais (cor sai do nome, igual para todos) e anel verde ao falar."""
    color = AVATAR_COLORS[int(hashlib.md5(name.encode()).hexdigest(), 16) % len(AVATAR_COLORS)]
    pix = QPixmap(int(size * dpr), int(size * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if speaking:
        pen = p.pen()
        pen.setColor(QColor(SPEAKING_COLOR))
        pen.setWidthF(2.2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(1.2, 1.2, size - 2.4, size - 2.4))
    inner = QRectF(RING + 1, RING + 1, size - 2 * (RING + 1), size - 2 * (RING + 1))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(inner)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(inner.height() * 0.42))
    p.setFont(font)
    p.setPen(QColor("white"))
    p.drawText(inner, Qt.AlignmentFlag.AlignCenter, initials(name))
    p.end()
    return pix


class PeopleList(QWidget):
    """Quem está na voz: avatar, nome e ícones (🖥️ compartilhando, 🔇 mutado)."""

    def __init__(self, on_rename) -> None:
        super().__init__()
        self._on_rename = on_rename
        self._key: list | None = None
        self._avatars: dict[str, tuple[QLabel, str]] = {}
        self._speaking: set[str] = set()
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 4, 0, 0)
        self._layout.setSpacing(4)

    def set_people(self, people: list[dict]) -> None:
        key = [(p["id"], p["name"], p["muted"], p["sharing"], p["connected"]) for p in people]
        if key == self._key:
            return  # evita reconstruir a lista a cada "sync" igual
        self._key = key
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._avatars.clear()
        dpr = self.devicePixelRatioF() or 1.0
        for person in people:
            self._layout.addWidget(self._row(person, dpr))

    def set_speaking(self, ids: set[str]) -> None:
        """Redesenha só os avatares que mudaram (chamado a cada 100 ms)."""
        if ids == self._speaking:
            return
        dpr = self.devicePixelRatioF() or 1.0
        for pid in ids ^ self._speaking:
            if pid in self._avatars:
                pic, name = self._avatars[pid]
                pic.setPixmap(avatar(name, dpr=dpr, speaking=pid in ids))
        self._speaking = set(ids)

    def _row(self, person: dict, dpr: float) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        pic = QLabel()
        pic.setPixmap(avatar(person["name"], dpr=dpr, speaking=person["id"] in self._speaking))
        pic.setFixedSize(AVATAR_SIZE, AVATAR_SIZE)
        self._avatars[person["id"]] = (pic, person["name"])
        h.addWidget(pic)
        name = label(person["name"] + (" (você)" if person["me"] else ""), None if person["connected"] else "muted")
        h.addWidget(name)
        if not person["connected"]:
            h.addWidget(label("conectando…", "hint"))
        if person["me"]:
            edit = button("✏️", "link")
            edit.setToolTip("Mudar seu nome")
            edit.clicked.connect(self._on_rename)
            h.addWidget(edit)
        h.addStretch()
        flags = [("🖥️", "compartilhando a tela", person["sharing"]), ("🔇", "microfone mutado", person["muted"])]
        on = [f for f in flags if f[2]]
        if on:
            status = label(" ".join(f[0] for f in on), "muted")
            status.setToolTip(", ".join(f[1] for f in on))
            h.addWidget(status)
        return row
