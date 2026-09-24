from __future__ import annotations

import asyncio
import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget

from .. import APP_NAME, __version__, updater
from .home import HomePage
from .host import HostPage
from .update_dialog import UpdateDialog
from .viewer import ViewerPage

log = logging.getLogger(__name__)

UPDATE_CHECK_INTERVAL_MS = 30 * 60 * 1000


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1100, 700)
        self.setMinimumSize(760, 520)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.page = None
        self._closing = False
        self._update_dialog: UpdateDialog | None = None
        self.show_home()

        QShortcut(QKeySequence(Qt.Key.Key_F11), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.exit_fullscreen)

        # Enquanto o app está aberto, continua verificando se saiu versão nova.
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(lambda: asyncio.ensure_future(self._periodic_update_check()))
        if updater.update_checks_enabled():
            self._update_timer.start(UPDATE_CHECK_INTERVAL_MS)

    # --- navegação ---

    def _set_page(self, page) -> None:
        old = self.page
        self.page = page
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        if old is not None:
            self.stack.removeWidget(old)
            old.deleteLater()

    def show_home(self) -> None:
        self.exit_fullscreen()
        self._set_page(HomePage(on_host=self.show_host, on_watch=self.show_viewer))

    def show_host(self) -> None:
        self._set_page(HostPage(on_back=self.show_home))

    def show_viewer(self, code: str) -> None:
        page = ViewerPage(code, on_back=self.show_home, on_fullscreen=self.toggle_fullscreen)
        self._set_page(page)
        asyncio.ensure_future(page.start())

    # --- tela cheia ---

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.exit_fullscreen()
        elif isinstance(self.page, ViewerPage):
            self.page.set_fullscreen_ui(True)
            self.showFullScreen()

    def exit_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        if isinstance(self.page, ViewerPage):
            self.page.set_fullscreen_ui(False)

    # --- atualização / encerramento ---

    async def _stop_page(self) -> None:
        page = self.page
        if isinstance(page, (HostPage, ViewerPage)):
            try:
                await page.stop()
            except Exception:  # noqa: BLE001
                log.exception("erro ao encerrar sessão")

    async def _periodic_update_check(self) -> None:
        if self._update_dialog is not None:
            return
        try:
            release = await updater.check_for_update()
        except Exception:  # noqa: BLE001 - sem rede etc.
            log.warning("verificação de atualização falhou", exc_info=True)
            return
        if not release:
            return
        self._update_timer.stop()
        await self._stop_page()
        self.exit_fullscreen()
        self.hide()
        self._update_dialog = UpdateDialog(release)
        self._update_dialog.show()
        self._update_dialog.start()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._closing:
            event.accept()
            return
        event.ignore()
        if isinstance(self.page, HostPage) and not self.page.confirm_leave():
            return
        self._closing = True

        async def shutdown() -> None:
            await self._stop_page()
            self.close()
            QApplication.quit()

        asyncio.ensure_future(shutdown())
