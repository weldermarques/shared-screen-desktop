from __future__ import annotations

import asyncio
import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QProgressBar, QTextBrowser, QVBoxLayout

from .. import __version__, updater
from .widgets import button, label

log = logging.getLogger(__name__)


class UpdateDialog(QDialog):
    """Bloqueia o app até instalar a nova versão. Não há opção de pular."""

    def __init__(self, release: updater.Release) -> None:
        super().__init__()
        self.release = release
        self.setWindowTitle("Atualização obrigatória")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint | Qt.WindowType.CustomizeWindowHint)
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(10)
        layout.addWidget(label("Atualização obrigatória", "h2"))
        layout.addWidget(
            label(
                f"A versão <b>{release.version}</b> está disponível. "
                f"A versão instalada ({__version__}) não pode mais ser usada.",
                "muted",
                wrap=True,
            )
        )
        if release.notes.strip():
            notes = QTextBrowser()
            notes.setMarkdown(release.notes)
            notes.setMaximumHeight(140)
            layout.addWidget(notes)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)
        self.status = label("Baixando atualização…", "muted", wrap=True)
        layout.addWidget(self.status)

        row = QHBoxLayout()
        row.addStretch()
        self.retry_btn = button("Tentar novamente")
        self.retry_btn.clicked.connect(self.start)
        self.retry_btn.hide()
        quit_btn = button("Sair", "secondary")
        quit_btn.clicked.connect(QApplication.quit)
        row.addWidget(quit_btn)
        row.addWidget(self.retry_btn)
        layout.addLayout(row)

    def reject(self) -> None:  # Esc não fecha
        pass

    def closeEvent(self, event) -> None:  # noqa: N802
        QApplication.quit()

    def start(self) -> None:
        asyncio.ensure_future(self._run())

    def _on_progress(self, done: int, total: int) -> None:
        if total:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(done * 100 / total))
            self.status.setText(f"Baixando atualização… {done / 1e6:.1f} de {total / 1e6:.1f} MB")

    async def _run(self) -> None:
        self.retry_btn.hide()
        self.status.setObjectName("muted")
        self.status.setStyleSheet("")
        self.progress.setRange(0, 0)
        try:
            installer = await updater.download(self.release, self._on_progress)
            self.status.setText("Instalando… o app vai reabrir sozinho.")
            self.progress.setRange(0, 0)
            updater.launch_installer(installer)
            QTimer.singleShot(1000, QApplication.quit)
        except Exception as exc:  # noqa: BLE001
            log.exception("falha ao atualizar")
            self.status.setText(f"Não foi possível baixar a atualização: {exc}")
            self.status.setStyleSheet("color: #ef4d5a;")
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.retry_btn.show()
